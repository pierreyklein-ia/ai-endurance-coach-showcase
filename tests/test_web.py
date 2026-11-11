"""v0.9 — application web : session, vues, validation de séance, chat, import, push, carnet."""
import dataclasses
import json
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from coach.pilot.carnet import apply_carnet, parse_carnet
from coach.pilot.runtime import build_pilot
from coach.web import auth
from coach.web.app import create_app
from coach.web.push import Notifier, PushGone
from coach.web.service import CoachService
from tests.fakes_google import Svc
from tests.fitwriter import write_fit
from tests.test_pilot import ST, TODAY, _major_llm, fill_history

CODE = "pas-de-code-faible"
SECRET = b"s" * 32


class FakeSender:
    def __init__(self):
        self.sent, self.gone = [], set()

    def __call__(self, sub, data):
        if sub["endpoint"] in self.gone:
            raise PushGone("410")
        self.sent.append((sub["endpoint"], json.loads(data)))


def make(tmp_path, llm=None, logged=True):
    svc = Svc()
    p = build_pilot(home=tmp_path, calendar_service=svc, today_fn=lambda: TODAY,
                    settings=dataclasses.replace(ST), llm=llm)
    fill_history(p.store)
    sender = FakeSender()
    service = CoachService(p, Notifier(p.store, sender))
    app = create_app(service, passcode=CODE, secret=SECRET, public_key="PUBKEY", background=False)
    c = TestClient(app)
    if logged:
        assert c.post("/api/login", json={"passcode": CODE}).status_code == 200
    c.pilot, c.service, c.sender, c.gsvc = p, service, sender, svc
    return c


# --------------------------------------------------------------------------- session
def test_api_requires_session_and_rejects_bad_code(tmp_path):
    c = make(tmp_path, logged=False)
    assert c.get("/api/today").status_code == 401
    assert c.get("/").status_code == 200                       # la coquille reste accessible
    assert c.post("/api/login", json={"passcode": "faux"}).status_code == 401
    for _ in range(5):
        c.post("/api/login", json={"passcode": "faux"})
    assert c.post("/api/login", json={"passcode": CODE}).status_code == 429   # anti-devinage


def test_signed_token_expires_and_resists_tampering():
    t = auth.sign(SECRET, now=1000, days=1)
    assert auth.verify(SECRET, t, now=1000 + 3600) == "pilote"
    assert auth.verify(SECRET, t, now=1000 + 2 * 86400) is None
    assert auth.verify(b"autre" * 8, t, now=1000) is None
    body, mac = t.split(".")
    assert auth.verify(SECRET, body[:-2] + "AA." + mac, now=1000) is None


def test_pwa_assets_and_security_headers(tmp_path):
    c = make(tmp_path, logged=False)
    m = c.get("/manifest.webmanifest")
    assert m.json()["display"] == "standalone"
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and "push" in sw.text
    assert "script-src 'self'" in c.get("/").headers["content-security-policy"]


# --------------------------------------------------------------------------- vues
def test_today_and_week_show_plan_and_activities(tmp_path):
    c = make(tmp_path)
    c.pilot.write_plan(c.pilot.repo.propose_taper(TODAY)[0])
    t = c.get("/api/today").json()
    assert t["days_to_race"] == 15 and t["state"]["ctl"] > 0
    w = c.get("/api/week", params={"start": "2026-10-05"}).json()
    assert w["start"] == "2026-10-05" and len(w["days"]) == 7
    assert sum(len(d["sessions"]) for d in w["days"]) >= 4 and w["planned_min"] > 0
    s = c.get("/api/state").json()
    assert s["activities"] and s["acwr"] is not None


def test_feedback_is_saved_and_can_alert_the_coach(tmp_path):
    c = make(tmp_path)
    c.pilot.write_plan(c.pilot.repo.propose_taper(TODAY)[0])
    days = c.get("/api/week", params={"start": "2026-10-05"}).json()["days"]
    sid = next(d for d in days if d["sessions"])["sessions"][0]["session_id"]
    r = c.post(f"/api/sessions/{sid}/feedback", json={"status": "done", "rpe": 6, "note": ""})
    assert r.status_code == 200 and r.json()["feedback"]["rpe"] == 6 and r.json()["messages"] == []
    assert c.post(f"/api/sessions/{sid}/feedback", json={"status": "nope"}).status_code == 422
    assert c.post("/api/sessions/inconnue/feedback", json={"status": "done"}).status_code == 404
    r = c.post(f"/api/sessions/{sid}/feedback",
               json={"status": "modified", "rpe": 8, "note": "Mollet qui tire", "notify_coach": True})
    msgs = r.json()["messages"]
    assert msgs[0]["role"] == "user" and "RPE 8/10" in msgs[0]["text"] and msgs[1]["role"] == "coach"

    def boom(text):
        raise RuntimeError("API Claude indisponible")
    c.service.chat = boom                                       # le coach plante : le retour reste enregistré
    r = c.post(f"/api/sessions/{sid}/feedback", json={"status": "done", "rpe": 5, "note": "ok", "notify_coach": True})
    assert r.status_code == 200 and r.json()["feedback"]["rpe"] == 5 and "indisponible" in r.json()["coach_error"]


# --------------------------------------------------------------------------- chat
def test_chat_goes_through_graph_and_keeps_history(tmp_path):
    c = make(tmp_path)
    c.pilot.write_plan(c.pilot.repo.propose_taper(TODAY)[0])
    r = c.post("/api/chat", json={"text": "J'ai fait une soirée avec beaucoup d'alcool hier soir, je m'entraîne comment demain ?"})
    out = r.json()
    assert "pas d'intensité" in out["messages"][-1]["text"] and out["calendar_changed"]
    assert out["messages"][-1]["meta"]["nodes"]                     # trace pour le debug et la soutenance
    near = c.pilot.store.plan(TODAY, TODAY + timedelta(days=1))
    assert all(s.intensity in ("z1", "rest") for s in near)
    h = c.get("/api/chat").json()
    assert [m["role"] for m in h["messages"]] == ["user", "coach"] and h["pending"] is False


def test_major_change_waits_for_yes_in_the_app(tmp_path):
    c = make(tmp_path, llm=_major_llm())
    c.pilot.write_plan(c.pilot.repo.propose_taper(TODAY)[0])
    r = c.post("/api/chat", json={"text": "Jeudi soir je n'aurai que 30 minutes, on adapte ?"}).json()
    assert r["pending"] and r["messages"][-1]["kind"] == "ask"
    assert c.get("/api/chat").json()["pending"]                     # le bouton « Oui » reste affiché
    r = c.post("/api/chat", json={"text": "oui"}).json()
    assert not r["pending"]
    assert c.pilot.store.plan(date(2026, 10, 1), date(2026, 10, 1))[0].intensity == "z1"


def test_chat_daily_budget_cap(tmp_path):
    c = make(tmp_path)
    c.pilot.settings.daily_budget_eur = 0.0
    r = c.post("/api/chat", json={"text": "Je me sens bien"}).json()
    assert r["messages"][-1]["kind"] == "system" and "Budget" in r["messages"][-1]["text"]
    assert c.pilot.deps.llm.usage_log == []


# --------------------------------------------------------------------------- données
def test_upload_fit_from_phone(tmp_path):
    c = make(tmp_path)
    f = tmp_path / "x.fit"
    write_fit(f, datetime(2026, 9, 30, 5, tzinfo=UTC), 1800, lambda i: 150)
    r = c.post("/api/upload", params={"name": "Suunto.fit"}, content=f.read_bytes())
    assert r.status_code == 200 and r.json()["status"] == "new" and r.json()["activity"]["minutes"] == 30
    assert c.post("/api/upload", params={"name": "photo.jpg"}, content=b"x").status_code == 422


def test_sync_without_strava_explains(tmp_path):
    c = make(tmp_path)
    r = c.post("/api/sync").json()
    assert r["ok"] is False and "Strava" in r["message"]


# --------------------------------------------------------------------------- notifications
def test_push_subscribe_test_and_morning_evening_reminders(tmp_path):
    c = make(tmp_path)
    c.pilot.write_plan(c.pilot.repo.propose_taper(TODAY - timedelta(days=1))[0])
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}
    assert c.post("/api/push/subscribe", json=sub).status_code == 200
    assert c.post("/api/push/test").json()["sent"] == 1
    svc = c.service
    has_today = bool(svc._sessions(TODAY, TODAY))
    assert svc.tick(datetime(2026, 9, 30, 6)) == []                 # trop tôt
    done = svc.tick(datetime(2026, 9, 30, 7, 5))
    assert done == (["am"] if has_today else [])
    assert svc.tick(datetime(2026, 9, 30, 7, 6)) == []              # une seule fois par jour
    assert svc.tick(datetime(2026, 9, 30, 21, 1)) == (["pm"] if has_today else [])
    c.sender.gone.add(sub["endpoint"])                              # abonnement révoqué -> nettoyé
    c.service.notifier.send("x", "y")
    assert c.pilot.store.push_subs() == []


# --------------------------------------------------------------------------- carnet
def test_import_carnet_export_keeps_titles_feedback_and_weeks(tmp_path):
    exp = tmp_path / "export"
    (exp / "sessions").mkdir(parents=True)
    (exp / "weeks").mkdir()
    docs = {
        "2026-10-01-1": {"date": "2026-10-01", "week": "2026-09-28", "order": 1, "kind": "seuil", "key": True,
                         "title": "Seuil 5×4'", "detail": "20 min EF, 5×4' au seuil", "minutes": 60,
                         "status": "done", "rpe": 6, "note": "ok"},
        "2026-10-01-2": {"date": "2026-10-01", "week": "2026-09-28", "order": 2, "kind": "renfo",
                         "title": "Force A", "detail": "Box squat 3×6", "minutes": 50, "status": "planned"},
        "2026-10-04-1": {"date": "2026-10-04", "week": "2026-09-28", "order": 1, "kind": "sl",
                         "title": "Sortie longue", "detail": "2 h 30 vallonné, 1 000 m D+", "minutes": 150},
        "2027-05-07-1": {"date": "2027-05-07", "week": "2027-05-03", "order": 1, "kind": "course",
                         "title": "VVX 85 km", "detail": "Départ 6 h", "minutes": 540},
    }
    for k, v in docs.items():
        (exp / "sessions" / f"{k}.json").write_text(json.dumps({"id": k, "data": v, "version": 1}))
    (exp / "weeks" / "2026-09-28.json").write_text(json.dumps(
        {"id": "2026-09-28", "data": {"phase": "Base", "focus": "Seuil contrôlé", "keys": [], "review": "Bien."}}))
    imp = parse_carnet(exp)
    by = {s.session_id: s for s in imp.sessions}
    assert by["carnet-2026-10-01-1"].intensity == "threshold" and by["carnet-2026-10-01-2"].planned_load == 0
    assert by["carnet-2026-10-04-1"].elevation_gain_m == 1000 and by["carnet-2027-05-07-1"].is_race
    (tmp_path / "home").mkdir()
    c = make(tmp_path / "home")
    assert apply_carnet(c.pilot.store, imp) == 4
    w = c.get("/api/week", params={"start": "2026-09-28"}).json()
    wed = next(d for d in w["days"] if d["date"] == "2026-10-01")["sessions"]
    assert [s["title"] for s in wed] == ["Seuil 5×4'", "Force A"] and wed[0]["feedback"]["rpe"] == 6
    assert w["focus"] == "Seuil contrôlé" and w["review"] == "Bien."
    c.post("/api/sessions/carnet-2026-10-01-1/feedback", json={"status": "skipped"})
    apply_carnet(c.pilot.store, imp)                                # un réimport n'écrase pas l'app
    assert c.pilot.store.feedback_for(["carnet-2026-10-01-1"])["carnet-2026-10-01-1"]["status"] == "skipped"


def test_about_tab_shows_the_running_version_and_stays_in_sync(tmp_path):
    import re
    from pathlib import Path
    c = make(tmp_path)
    root = Path(__file__).resolve().parent.parent
    version = (root / "VERSION").read_text().strip()
    r = c.get("/api/about")
    assert r.status_code == 200
    j = r.json()
    assert j["version"] == version and j["cards"] >= 41 and j["registry"]
    # l'interface et le cache hors-ligne portent le même numéro : un écran périmé se voit dans l'onglet Détails.
    # L'interface React lit VERSION au build (vite.config.ts → __APP_VERSION__), affiché « Interface chargée v… ».
    vite = (root / "frontend/vite.config.ts").read_text()
    assert '"../VERSION"' in vite and "__APP_VERSION__" in vite
    assert "__APP_VERSION__" in (root / "frontend/src/version.ts").read_text()
    assert '"Interface chargée"' in (root / "frontend/src/views/Details.tsx").read_text()
    assert 'data-tab={id}' in (root / "frontend/src/components/Tabs.tsx").read_text()
    assert f'coach-v{version}' in (root / "coach/web/static/sw.js").read_text()
    assert 'data-tab="details"' in (root / "coach/web/static/index.html").read_text()      # ancienne interface (/legacy)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)


def test_root_serves_the_built_react_app_and_keeps_the_legacy_page(tmp_path):
    import shutil
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / "coach/web/static"
    site = tmp_path / "static"
    shutil.copytree(static, site, ignore=shutil.ignore_patterns("app"))
    (tmp_path / "home").mkdir()
    c = make(tmp_path / "home", logged=False)
    app = create_app(c.service, passcode=CODE, secret=SECRET, background=False, static_dir=site)
    t = TestClient(app)
    # pas encore construite : l'ancienne interface reste servie sur /
    r = t.get("/")
    assert r.status_code == 200 and "/static/app.js" in r.text and r.headers["cache-control"] == "no-cache"
    # construite (npm run build → static/app/) : / sert React, /legacy l'ancienne
    (site / "app" / "assets").mkdir(parents=True)
    (site / "app" / "index.html").write_text('<!doctype html><div id="root"></div>'
                                             '<script type="module" src="/static/app/assets/index-x.js"></script>')
    (site / "app" / "assets" / "index-x.js").write_text("console.log(1)")
    r = t.get("/")
    assert r.status_code == 200 and 'id="root"' in r.text and r.headers["cache-control"] == "no-cache"
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert t.get("/static/app/assets/index-x.js").status_code == 200
    legacy = t.get("/legacy")
    assert legacy.status_code == 200 and "/static/app.js" in legacy.text
    assert t.get("/static/styles.css").status_code == 200 and t.get("/static/icon.svg").status_code == 200
    assert t.get("/sw.js").status_code == 200 and t.get("/manifest.webmanifest").json()["display"] == "standalone"
    assert t.get("/api/today").status_code == 401
