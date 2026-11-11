"""v0.10-v0.11 — nouvel athlète : bilan depuis Strava, bloc de départ, plan négocié dans le chat."""
import dataclasses
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from coach.diagnosis import diagnose
from coach.onboarding import (
    Prefs,
    Profile,
    apply_turn,
    check_block,
    initial_block,
    parse_date_fr,
    parse_goal,
    parse_turn,
    week_layout,
)
from coach.pilot.runtime import build_pilot
from coach.pilot.store import Activity
from coach.web.app import create_app
from coach.web.service import CoachService
from tests.test_pilot import ST

TODAY = date(2026, 10, 27)
CODE, SECRET = "pas-de-code-faible", b"s" * 32


def act(day: date, km: float, minutes: float, dplus: float = 0, name: str = "Footing", sport: str = "Course",
        hour: int = 7):
    t = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    return Activity(start_utc=t, local_day=day, sport=sport, duration_s=minutes * 60, distance_m=km * 1000,
                    dplus_m=dplus, avg_hr=None, load=minutes * 1.0, load_source="estimate", name=name,
                    moving_s=minutes * 60)


def trail_runner(today=TODAY, big_effort_days_ago: int | None = None, quality_recent: bool = False):
    """Historique type : beaucoup de trail lent, vitesse travaillée il y a 4-6 mois, un 10 km de référence."""
    acts = []
    start = today - timedelta(days=200)
    for i in range(200):
        d = start + timedelta(days=i)
        recent = (today - d).days <= 84
        if d.weekday() in (1, 3):
            q = (not recent) or quality_recent
            acts.append(act(d, 12, 60, 20, "10x400m" if q else "Footing", "Course") if q
                        else act(d, 10, 70, 400, "Trail", "Trail"))
        elif d.weekday() == 6:
            acts.append(act(d, 25, 210, 1400, "Sortie longue", "Trail"))
        elif d.weekday() == 4:
            acts.append(act(d, 10, 65, 350, "Trail", "Trail"))
    acts.append(act(start + timedelta(days=30), 10, 38.0, 20, "10 km de ma ville"))
    if big_effort_days_ago is not None:
        acts.append(act(today - timedelta(days=big_effort_days_ago), 160, 30 * 60, 9000, "Ultra", "Trail", hour=4))
    return sorted(acts, key=lambda a: a.start_utc)


PROFILE = Profile(first_name="Alex", goal_name="Trail de printemps", goal_date=date(2027, 4, 24))


def test_diagnosis_flags_neglected_speed():
    d = diagnose(trail_runner(), TODAY)
    ids = {f.id for f in d.findings}
    assert d.focus == "vitesse" and "vitesse" in ids and "terrain" in ids
    assert d.reference and abs(d.reference.equivalent_10k_s - 38 * 60) < 5
    assert d.paces["seuil"] and d.vma_kmh and 17 < d.vma_kmh < 19


def test_no_speed_flag_when_quality_is_regular():
    d = diagnose(trail_runner(quality_recent=True), TODAY)
    assert "vitesse" not in {f.id for f in d.findings}


def test_block_waits_after_big_effort_and_respects_days():
    d = diagnose(trail_runner(big_effort_days_ago=12), TODAY)
    assert "recuperation" in {f.id for f in d.findings}
    b = initial_block(d, PROFILE, TODAY + timedelta(days=1))
    hard = [s for s in b.sessions if s.intensity in ("vo2", "threshold")]
    assert b.intensity_from == TODAY - timedelta(days=12) + timedelta(days=28)
    assert [w["kind"] for w in b.weeks][:2] == ["Récupération", "Reprise"]
    assert hard and min(s.day for s in hard) >= b.intensity_from
    assert all(s.day > TODAY for s in b.sessions)
    lay = week_layout(PROFILE, Prefs())
    assert lay["days"] == [1, 2, 3, 4, 6] and lay["quality"] == [1, 3]      # mar/jeu qualité, dim longue
    for w in range(len(b.weeks)):
        monday = date.fromisoformat(b.weeks[w]["start"])
        wk = [s for s in b.sessions if monday <= s.day < monday + timedelta(days=7)]
        assert len({s.day for s in wk if s.intensity != "rest" and not s.session_id.endswith("-bonus")}) <= PROFILE.days_per_week
        assert sum(s.duration_min for s in wk if s.intensity != "rest" and not s.session_id.endswith("-bonus")) == b.weeks[w]["minutes"]
    assert [w["kind"] for w in b.weeks][-4:] == ["Vitesse 1", "Vitesse 2", "Vitesse 3", "Vitesse 4 (allégée)"]


def test_block_without_recent_effort_starts_speed_now_and_passes_guard():
    acts = trail_runner()
    d = diagnose(acts, TODAY)
    b = initial_block(d, PROFILE, TODAY + timedelta(days=1))
    assert len(b.weeks) == 4 and b.intensity_from == TODAY + timedelta(days=1)
    assert b.weeks[0]["kind"] == "Vitesse 1"
    hist = [0.0] * 200
    for a in acts:
        i = (a.local_day - (TODAY - timedelta(days=200))).days
        if 0 <= i < 200:
            hist[i] += a.load
    assert check_block(hist, b, TODAY + timedelta(days=1)) == []


def test_french_parsers():
    assert parse_date_fr("le 20 mars", TODAY) == date(2027, 3, 20)
    assert parse_date_fr("20/03/2027", TODAY) == date(2027, 3, 20)
    assert parse_date_fr("dès lundi", TODAY) == date(2026, 11, 2)            # mardi 27/10 -> lundi 02/11
    g = parse_goal("Le Triple des Vallées le 20 mars, 104 km et 2000 m D+", TODAY).events[0]
    assert (g.name, g.date, g.distance_km, g.dplus_m, g.kind) == \
        ("Le Triple des Vallées", date(2027, 3, 20), 104, 2000, "trail")
    assert parse_date_fr("dès la semaine prochaine", TODAY) == date(2026, 11, 2)
    t = parse_turn("Je me sens prêt à reprendre dès lundi, 6 jours par semaine et sortie longue le samedi", TODAY)
    assert (t.intent, t.resume_from, t.days_per_week, t.long_day) == ("modify", date(2026, 11, 2), 6, 5)
    assert parse_turn("Ça me va !", TODAY).intent == "accept"
    assert parse_turn("non", TODAY).intent == "reject"
    assert parse_turn("pourquoi du 30/30 ?", TODAY).intent == "question"


PY_MSG = ("Je veux reprendre dès la semaine prochaine, je me sens très bien! Mes sorties longues sont plutot en "
          "semaine, je préfère pas prendre trop de temps en entrainement le weekend car famille, j'aime bien faire du "
          "double seuil ca me fait bien progresser, je peux faire des doubles sorties le lundi mardi jeudi et vendredi, "
          "j'aime bien faire du gros volume")


def test_every_preference_is_taken_into_account():
    t = parse_turn(PY_MSG, TODAY)
    assert (t.resume_from, t.long_day, t.weekend_max_min, t.double_threshold, t.double_days, t.likes_volume) == \
        (date(2026, 11, 2), 2, 60, True, [0, 1, 3, 4], True)
    p, prefs, taken = apply_turn(t, PROFILE, Prefs(), TODAY)
    assert len(taken) == 6
    d = diagnose(trail_runner(big_effort_days_ago=12), TODAY)
    b = initial_block(d, p, TODAY + timedelta(days=1), prefs)
    hard_days = {}
    for s_ in b.sessions:
        assert s_.day.weekday() not in (5, 6) or s_.duration_min <= 60          # week-end léger
        if s_.intensity in ("vo2", "threshold", "long"):
            hard_days.setdefault(s_.day, []).append(s_)
        if s_.description.startswith("Footing du matin"):
            assert s_.day.weekday() in (0, 1, 3, 4)                             # doubles aux jours permis
    days = sorted(hard_days)
    assert all((b2 - a2).days > 1 for a2, b2 in zip(days, days[1:]))            # jamais 2 jours durs d'affilée
    assert all(s_.day.weekday() == 2 for s_ in b.sessions if s_.intensity == "long")
    doubles = [x for x in b.sessions if x.description.startswith("Double seuil")]
    first_int = min(x.day for x in b.sessions if x.intensity in ("vo2", "threshold"))
    monday = lambda x: x - timedelta(days=x.weekday())                       # noqa: E731
    assert doubles and monday(min(x.day for x in doubles)) > monday(first_int)  # pas la semaine du retour
    mins = [w["minutes"] for w in b.weeks if w["kind"].startswith("Vitesse") and "allégée" not in w["kind"]]
    assert all(b2 <= a2 * 1.3 + 5 for a2, b2 in zip(mins, mins[1:]))           # +30 % maximum


def test_athlete_cannot_skip_the_safety_floor():
    d = diagnose(trail_runner(big_effort_days_ago=12), TODAY)
    p, prefs, changed = apply_turn(parse_turn("je reprends demain à fond", TODAY), PROFILE, Prefs(), TODAY)
    b = initial_block(d, p, TODAY + timedelta(days=1), prefs)
    floor = TODAY - timedelta(days=12) + timedelta(days=21)                 # J+21 au plus tôt après > 24 h
    assert b.resume_from == TODAY + timedelta(days=1) and b.intensity_from == floor
    assert all(s.day >= floor for s in b.sessions if s.intensity in ("vo2", "threshold"))
    assert any("minimum" in n for n in b.notes)
    first_speed = next(w for w in b.weeks if w["kind"].startswith("Vitesse"))
    assert first_speed["minutes"] < max(w["minutes"] for w in b.weeks)     # semaine de transition modérée


class FakeStrava:
    """Connecteur MCP simulé : liste d'activités au format MCP, pas de flux FC."""
    def __init__(self, acts):
        self.items = [{"id": str(i), "name": a.name, "sport_type": "TrailRun" if a.sport == "Trail" else "Run",
                       "start_local": a.start_utc.replace(tzinfo=None).isoformat(),
                       "summary": {"elapsed_time": a.duration_s, "moving_time": a.duration_s,
                                   "distance": a.distance_m, "elevation_gain": a.dplus_m}}
                      for i, a in enumerate(acts)]

    def call(self, name, args):
        sel = [it for it in self.items if it["start_local"] >= args["range_start"]]
        off = int(args.get("after") or 0)
        page = sel[off: off + args["first"]]
        return {"activities": page, "has_next_page": off + len(page) < len(sel), "end_cursor": str(off + len(page))}

    def batch(self, calls):
        return [None] * len(calls)


class FakeConnector:
    status, error = "idle", None

    def __init__(self):
        self.started_with = None

    def connected(self):
        return self.status == "connected"

    def start(self, redirect_uri, on_done):
        self.started_with, self.status = redirect_uri, "connected"
        on_done({"firstname": "Alex"})
        return {"status": "connected"}

    def callback(self, *a):
        return False


def make(tmp_path, acts):
    p = build_pilot(home=tmp_path, today_fn=lambda: TODAY, settings=dataclasses.replace(ST))
    service = CoachService(p, connector=FakeConnector(), strava_factory=lambda: FakeStrava(acts), background=False)
    c = TestClient(create_app(service, passcode=CODE, secret=SECRET, background=False))
    assert c.post("/api/login", json={"passcode": CODE}).status_code == 200
    c.pilot, c.service = p, service
    return c


def chat(c, text):
    r = c.post("/api/chat", json={"text": text})
    assert r.status_code == 200
    return r.json()


def test_new_user_full_conversation(tmp_path):
    c = make(tmp_path, trail_runner(big_effort_days_ago=12))
    me = c.get("/api/me").json()
    assert me["onboarding"] and me["race_name"] is None                    # aucune course par défaut ne fuit
    assert c.get("/api/onboarding").json()["step"] == "connect"
    r = c.post("/api/strava/connect").json()
    assert r["status"] == "connected"
    assert c.service.connector.started_with == "http://testserver/strava/callback"
    ob = c.get("/api/onboarding").json()
    assert ob["step"] == "chat" and ob["activities"] > 100
    recap = c.get("/api/chat").json()
    text = recap["messages"][-1]["text"]
    assert recap["pending"] and "Salut Alex" in text and "Vitesse peu travaillée" in text
    assert "prochaines échéances" in text and "Ultra" in text                 # la récupération est évoquée
    assert "prochaines échéances" in recap["messages"][-1]["text"]
    # l'échéance : nom seul -> l'application la trouve (catalogue hors ligne) et fait confirmer
    r = chat(c, "La VVX 85 km")
    conf = r["messages"][-1]["text"]
    assert "2750 m D+" in conf and "07/05/2027" in conf and "C'est bien ça" in conf
    assert r["quick"][0] == "Oui, c'est ça" and c.pilot.store.get("profile") is None
    r = chat(c, "Oui, c'est ça")
    qs = r["messages"][-1]["text"]                          # v0.15 : questions sur son quotidien avant le plan
    assert "Renfo" in qs and "repos" in qs and "ceinture" in qs and "Vacances" in qs and "Autres sports" in qs
    assert "aucune séance de renfo sur Strava" in qs and r["quick"] == ["Passe, valeurs par défaut"]
    assert c.pilot.store.get("profile") is None
    r = chat(c, "5 jours. Repos le lundi. Je fais de la muscu en salle. Du vélo le mercredi et du foot le vendredi. "
                "Tout est sur Strava, avec une ceinture cardio. Vacances du 21/12 au 27/12, je ne pourrai pas courir.")
    ask = r["messages"][-1]["text"]                         # v0.49 : le volume maximum par semaine
    assert "volume maximum" in ask and r["quick"] == ["3 h", "5 h", "8 h", "Je ne sais pas"]
    assert c.pilot.store.get("profile") is None
    r = chat(c, "Je ne sais pas")
    prop = r["messages"][-1]["text"]
    assert "volume maximum estimé à" in prop and "1,3 × ta meilleure période récente" in prop
    assert c.service.prefs().max_week_inferred and c.service.prefs().max_week_min
    assert "VVX" in prop and "Semaine 1 — du 26/10 au 01/11" in prop and "Qu'est-ce que tu en dis" in prop
    for bit in ("repos le lundi", "renfo à la salle", "foot le vendredi : pas de séance dure",
                "vélo le mercredi", "ceinture cardio", "vacances du 21/12 au 27/12 : pas de course",
                "toutes tes séances sont sur Strava"):
        assert bit in prop, bit
    assert "de course + renfo 40 + 25 min" in prop and "(foot)" in prop
    pend = c.pilot.store.get("pending_onboarding_plan")["sessions"]
    assert not any(date.fromisoformat(s["day"]).weekday() == 0 for s in pend if s["intensity"] != "rest")
    assert not any(date.fromisoformat(s["day"]).weekday() in (4, 5) for s in pend
                   if s["intensity"] in ("vo2", "threshold"))
    assert r["quick"][0] == "Ça me va"
    assert "VVX" in c.get("/api/me").json()["race_name"]
    assert c.pilot.store.plan() == []                                       # rien n'est écrit pendant la discussion
    # la négociation
    r = chat(c, "Je me sens prêt à reprendre sérieusement dès lundi, et je préfère la sortie longue le samedi")
    upd = r["messages"][-1]["text"]
    assert "plan mis à jour" in upd and "lundi 02/11" in upd and "samedi" in upd and "pris en compte" in upd
    assert "Qu'est-ce que tu en dis" not in upd and upd.rstrip().endswith("Ça te va comme ça ?")
    assert "jeudi 05/11" in upd                                            # plancher J+21 expliqué
    pend = c.pilot.store.get("pending_onboarding_plan")["sessions"]
    assert all(s["day"] >= "2026-11-05" for s in pend if s["intensity"] in ("vo2", "threshold"))
    assert {date.fromisoformat(s["day"]).weekday() for s in pend if s["intensity"] == "long"} == {5}
    r = chat(c, "pourquoi du 30/30 ?")
    assert r["pending"] and "le plan te va" in r["messages"][-1]["text"]
    r = chat(c, "Ça me va")
    assert r["calendar_changed"] and not r["pending"] and "C'est parti" in r["messages"][-1]["text"]
    assert c.pilot.store.plan() and not c.get("/api/me").json()["onboarding"]
    week = c.get("/api/week", params={"start": "2026-11-09"}).json()
    assert week["phase"].startswith("Vitesse") and any(s["title"].startswith("VMA") for d in week["days"]
                                                       for s in d["sessions"])
    assert "Focus : vitesse" in c.pilot.repo.get("x").profile_summary
    # après l'accueil, le chat repasse par le graphe de coaching
    r = chat(c, "j'ai bien dormi")
    assert r["messages"][-1]["role"] == "coach" and not r["quick"]


def test_reset_restarts_onboarding(tmp_path):
    c = make(tmp_path, trail_runner())
    c.post("/api/strava/connect")
    chat(c, "Marathon de Nantes le 26/04/2027")
    chat(c, "oui")
    chat(c, "passe")
    chat(c, "ok")
    assert not c.get("/api/me").json()["onboarding"]
    assert c.post("/api/reset", json={}).status_code == 422
    assert c.post("/api/reset", json={"confirm": "recommencer"}).json()["ok"]
    assert c.get("/api/me").json()["onboarding"] and c.get("/api/chat").json()["messages"] == []
    assert c.get("/api/onboarding").json()["step"] == "connect"


def test_strava_callback_page_without_pending_flow(tmp_path):
    c = make(tmp_path, [])
    r = c.get("/strava/callback", params={"code": "x", "state": "y"})
    assert r.status_code == 200 and "interrompue" in r.text


# --------------------------------------------------------------------------- API Strava (v0.12)
class FakeStravaHTTP:
    """Serveur Strava simulé : OAuth + /athlete/activities + flux."""
    def __init__(self, acts, rate_limit_after=None):
        self.calls, self.acts, self.rate_limit_after = [], acts, rate_limit_after

    def __call__(self, method, url, params=None, data=None, headers=None):
        self.calls.append((method, url, params, data))
        if url.endswith("/oauth/token"):
            if data["grant_type"] == "authorization_code":
                return (200, {"access_token": "A1", "refresh_token": "R1", "expires_at": 4_000_000_000,
                              "athlete": {"id": 7, "firstname": "Alex"}}) if data["code"] == "good" else \
                    (400, {"message": "Bad Request"})
            return 200, {"access_token": "A2", "refresh_token": "R2", "expires_at": 4_000_000_000}
        gets = [c for c in self.calls if c[0] == "GET"]
        if self.rate_limit_after is not None and len(gets) > self.rate_limit_after:
            return 429, {"message": "Rate Limit Exceeded"}
        if url.endswith("/athlete/activities"):
            sel = [a for a in self.acts if params["after"] < a["ts"] < params.get("before", 10 ** 12)]
            page = sel[(params["page"] - 1) * params["per_page"]: params["page"] * params["per_page"]]
            return 200, [a["json"] for a in page]
        if "/streams" in url:
            return 200, {"time": {"data": list(range(0, 3600, 10))}, "heartrate": {"data": [150] * 360}}
        return 404, {"message": "not found"}


def api_acts(acts):
    out = []
    for i, a in enumerate(acts):
        utc = a.start_utc
        local = utc + timedelta(hours=4) if a.name == "Ultra" else utc + timedelta(hours=1)  # course à La Réunion
        out.append({"ts": int(utc.timestamp()), "json": {
            "id": 1000 + i, "name": a.name, "sport_type": "TrailRun" if a.sport == "Trail" else "Run",
            "start_date": utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "start_date_local": local.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "distance": a.distance_m, "moving_time": a.duration_s, "elapsed_time": a.duration_s,
            "total_elevation_gain": a.dplus_m, "has_heartrate": True, "average_heartrate": 145}})
    return out


def test_strava_api_oauth_flow(tmp_path):
    from coach.pilot.strava_api import StravaAPI, StravaAPIConnector
    http = FakeStravaHTTP([])
    con = StravaAPIConnector(tmp_path, "123", "sec", http)
    got = {}
    r = con.start("https://mac.ts.net/strava/callback", got.update)
    q = dict(x.split("=", 1) for x in r["url"].split("?", 1)[1].split("&"))
    assert r["status"] == "redirect" and q["client_id"] == "123" and "activity%3Aread_all" in q["scope"]
    assert not con.callback("good", "forged-state", None, "read,activity:read_all")          # anti-CSRF
    assert not con.callback("good", q["state"], None, "read")                                # portée insuffisante
    assert "activités" in con.error
    r = con.start("https://mac.ts.net/strava/callback", got.update)
    st = dict(x.split("=", 1) for x in r["url"].split("?", 1)[1].split("&"))["state"]
    assert con.callback("good", st, None, "read,activity:read_all")
    assert got["firstname"] == "Alex" and con.connected()
    assert not con.callback("good", st, None, "read,activity:read_all")                      # pas de rejeu
    assert oct((tmp_path / "strava_api.json").stat().st_mode)[-3:] == "600"
    api = StravaAPI(tmp_path, "123", "sec", http)
    tok = (tmp_path / "strava_api.json")
    import json as _j
    d = _j.loads(tok.read_text()); d["expires_at"] = 0; tok.write_text(_j.dumps(d))
    assert api._access_token() == "A2"                                                       # rafraîchi


def test_strava_api_sync_with_rate_limit(tmp_path):
    from coach.pilot.store import PilotStore
    from coach.pilot.strava_api import StravaAPI, sync_strava_api
    acts = trail_runner(big_effort_days_ago=12)
    http = FakeStravaHTTP(api_acts(acts), rate_limit_after=6)
    (tmp_path / "strava_api.json").write_text('{"access_token":"A","refresh_token":"R","expires_at":4000000000}')
    store = PilotStore(tmp_path / "p.db")
    res = sync_strava_api(store, dataclasses.replace(ST), StravaAPI(tmp_path, "1", "s", http), days=270, today=TODAY)
    assert res.partial and "quota" in res.partial                      # 429 sur les flux : on garde la FC moyenne
    assert len(store.activities()) == len({a.key for a in acts})
    ultra = next(a for a in store.activities() if a.name == "Ultra")
    assert ultra.local_day == TODAY - timedelta(days=12) and ultra.load_source != "estimate"
    len(http.calls)
    res2 = sync_strava_api(store, dataclasses.replace(ST), StravaAPI(tmp_path, "1", "s", http), today=TODAY)
    assert res2.listed == 0 and len(store.activities()) == len({a.key for a in acts})    # synchro incrémentale


def test_app_connects_strava_api_end_to_end(tmp_path):
    from coach.pilot.strava_api import StravaAPI, StravaAPIConnector
    acts = trail_runner(big_effort_days_ago=12)
    http = FakeStravaHTTP(api_acts(acts))
    p = build_pilot(home=tmp_path, today_fn=lambda: TODAY, settings=dataclasses.replace(ST))
    service = CoachService(p, connector=StravaAPIConnector(tmp_path, "123", "sec", http),
                           strava_factory=lambda: StravaAPI(tmp_path, "123", "sec", http), background=False)
    c = TestClient(create_app(service, passcode=CODE, secret=SECRET, background=False))
    c.post("/api/login", json={"passcode": CODE})
    r = c.post("/api/strava/connect", json={"origin": "http://testserver"}).json()
    assert r["status"] == "redirect" and "redirect_uri=http%3A%2F%2Ftestserver%2Fstrava%2Fcallback" in r["url"]
    state = dict(x.split("=", 1) for x in r["url"].split("?", 1)[1].split("&"))["state"]
    page = c.get("/strava/callback", params={"code": "good", "state": state, "scope": "read,activity:read_all"})
    assert "Strava connecté" in page.text
    ob = c.get("/api/onboarding").json()
    assert ob["step"] == "chat" and ob["activities"] > 100
    assert "Salut Alex" in c.get("/api/chat").json()["messages"][-1]["text"]
    bad = c.post("/api/strava/connect", json={"origin": "https://evil.example"}).json()     # origine ignorée
    assert "evil" not in bad.get("url", "") and bad["status"] == "connected"


def test_claude_path_multiple_events_and_preferences(tmp_path):
    """Chemin « Claude » (LLM simulé) : 3 échéances (A, B, C), recherche, confirmation, préférences multiples."""
    import json as _j

    from coach.llm import MockLLM
    from coach.onboarding import Busy, EventX, GoalsExtract, OnboardingMessage, OtherSport, ProfileExtract, TurnExtract

    def goal(user):
        d = _j.loads(user)
        if d.get("candidats"):
            return GoalsExtract(confirmed=True, reply="Super.")
        return GoalsExtract(events=[EventX(name="VVX", distance_km=85, role="A"),
                                    EventX(name="Triple des Vallées", role="B", note="pour le plaisir"),
                                    EventX(name="Marathon Cannes-Nice ou Amsterdam 2027", role="C", kind="route",
                                           note="passer sous 3 h")], reply="Belle saison en vue !")

    def turn(user):
        return TurnExtract(intent="modify", resume_from=date(2026, 11, 2), long_day=2, weekend_max_min=60,
                           double_threshold=True, double_days=[0, 1, 3, 4], likes_volume=True,
                           reply="Le double seuil, c'est le modèle norvégien (Casado 2023) : on le garde contrôlé.")

    def profile(user):
        return ProfileExtract(rest_days=[5], strength="gym", other_sports=[OtherSport(name="vélo", days=[2])],
                              strava_complete=True, hr_source="poignet",
                              busy=[Busy(start=date(2026, 12, 21), end=date(2026, 12, 27), kind="off",
                                         label="vacances")], reply="Merci, ça m'aide beaucoup.")

    llm = MockLLM({"onboard_recap": lambda u: OnboardingMessage(message=_j.loads(u)["draft"]),
                   "onboard_goal": goal, "onboard_turn": turn, "onboard_profile": profile})
    p = build_pilot(home=tmp_path, today_fn=lambda: TODAY, settings=dataclasses.replace(ST), llm=llm)
    service = CoachService(p, connector=FakeConnector(), strava_factory=lambda: FakeStrava(
        trail_runner(big_effort_days_ago=12)), background=False)
    c = TestClient(create_app(service, passcode=CODE, secret=SECRET, background=False))
    c.post("/api/login", json={"passcode": CODE})
    c.post("/api/strava/connect")
    conf = chat(c, "salut coach ! la vvx 85km, le triple des vallées pour le plaisir, puis un marathon")
    t = conf["messages"][-1]["text"]
    assert "2750 m D+" in t and "Triple des Vallées" in t and "20/03/2027" in t and "passer sous 3 h" in t
    qs = chat(c, "oui")["messages"][-1]["text"]
    assert qs.startswith("Super.") and "Renfo" in qs
    ask = chat(c, "samedi off, muscu en salle, vélo le mercredi, montre au poignet, vacances à Noël")["messages"][-1]["text"]
    assert ask.startswith("Merci, ça m'aide beaucoup.") and "volume maximum" in ask
    prop = chat(c, "Pas de limite")["messages"][-1]["text"]
    assert "FC au poignet" in prop and "repos le samedi" in prop
    assert "Courses secondaires" not in prop and "📌 Trail des Vallées" in prop and "📌 Marathon" in prop
    upd = chat(c, PY_MSG)["messages"][-1]["text"]
    assert upd.startswith("Le double seuil") and "Ce que j'ai pris en compte" in upd and "sortie longue le mercredi" in upd
    assert "double seuil matin" in upd and "footing du matin" in upd
    assert "fiches : kb_double_seuil_casado2023" in c.get("/api/chat").json()["messages"][-1]["meta"]["trace"]


# --------------------------------------------------------------------------- #
# v0.15 : renfo, repos, autres sports, vacances, Strava/cardio, question finale
# --------------------------------------------------------------------------- #
def _renfo(b):
    return [s for s in b.sessions if s.intensity == "rest"]


def test_strength_sessions_follow_the_rules():
    from coach.onboarding import Prefs as P
    d = diagnose(trail_runner(big_effort_days_ago=12), TODAY)
    b = initial_block(d, PROFILE, TODAY + timedelta(days=1), P())
    ren = _renfo(b)
    assert ren, "le renfo doit être proposé par défaut"
    run_by_day = {}
    for s in b.sessions:
        if s.intensity != "rest":
            run_by_day.setdefault(s.day, []).append(s.intensity)
    for w in b.weeks:
        ws = date.fromisoformat(w["start"])
        wr = [s for s in ren if ws <= s.day < ws + timedelta(days=7)]
        if w["kind"].startswith("Récup"):
            assert len(wr) <= 1 and all(s.description.startswith("Renfo doux") for s in wr)
        elif w["kind"].startswith("Vitesse") and "allégée" not in w["kind"]:
            assert len(wr) == 2
            assert any(s.description.startswith("Force") for s in wr)
        for s in wr:
            nxt = run_by_day.get(s.day + timedelta(days=1), [])
            same = run_by_day.get(s.day, [])
            # jamais la veille d'une séance dure, sauf si c'est déjà un jour de qualité
            assert not set(nxt) & {"vo2", "threshold", "long"} or set(same) & {"vo2", "threshold"}
    # placé sur les jours de qualité en semaine de vitesse
    speed = [s for s in ren if s.description.startswith("Force")]
    assert all(set(run_by_day.get(s.day, [])) & {"vo2", "threshold"} for s in speed)
    # sans matériel par défaut, salle si demandé, rien si refusé
    assert "sans matériel" in speed[0].description
    gym = initial_block(d, PROFILE, TODAY + timedelta(days=1), P(strength="gym"))
    assert any("box squat" in s.description for s in _renfo(gym))
    assert not _renfo(initial_block(d, PROFILE, TODAY + timedelta(days=1), P(strength="none")))


def test_rest_days_hard_sport_and_busy_weeks():
    from coach.onboarding import Busy, OtherSport
    from coach.onboarding import Prefs as P
    d = diagnose(trail_runner(big_effort_days_ago=12), TODAY)
    prefs = P(rest_days=[0, 4], other_sports=[OtherSport(name="foot", days=[2], hard=True)],
              busy=[Busy(start=date(2026, 11, 30), end=date(2026, 12, 6), kind="light", label="déplacement"),
                    Busy(start=date(2026, 12, 14), end=date(2026, 12, 16), kind="off", label="vacances")])
    lay = week_layout(PROFILE, prefs)
    assert not {0, 4} & set(lay["days"]) and 2 not in lay["quality"] and lay["after_sport"] == [3]
    b = initial_block(d, PROFILE, TODAY + timedelta(days=1), prefs)
    assert not any(s.day.weekday() in (0, 4) for s in b.sessions)            # repos : ni course ni renfo
    assert not any(s.day.weekday() == 2 for s in b.sessions if s.intensity in ("vo2", "threshold"))
    assert any("tout près du foot" in n for n in b.notes)
    assert not any(date(2026, 12, 14) <= s.day <= date(2026, 12, 16) for s in b.sessions)
    kinds = {w["start"]: w["kind"] for w in b.weeks}
    assert kinds.get("2026-11-30", "").endswith("(allégée : déplacement)")


def test_profile_fields_in_negotiation_and_defaults():
    t = parse_turn("en fait je fais du foot le jeudi et je veux le lundi en repos, pas de renfo pour moi", TODAY)
    assert t.intent == "modify" and t.rest_days == [0] and t.strength == "none"
    assert [(o.name, o.days, o.hard) for o in t.other_sports] == [("foot", [3], True)]
    p, prefs, taken = apply_turn(t, PROFILE, Prefs(), TODAY)
    assert prefs.strength_per_week == 0 and any("Lauersen" in x for x in taken)
    assert any(x.startswith("foot le jeudi") for x in taken)
    from coach.onboarding import ProfileExtract, apply_profile_fields
    _, prefs, taken = apply_profile_fields(ProfileExtract(skip=True), PROFILE, Prefs(), TODAY, defaults=True)
    assert any("par défaut" in x and "renfo" in x for x in taken)


def test_parse_profile_variants():
    from coach.onboarding import parse_profile
    x = parse_profile("Je fais du renfo à la maison le mardi, montre au poignet pour le cardio, j'oublie parfois "
                      "d'enregistrer sur strava. Semaine du 14/12 chargée au boulot. Natation le samedi.", TODAY)
    assert x.strength == "bodyweight" and x.strength_days == [1] and x.hr_source == "poignet"
    assert x.strava_complete is False and x.other_sports[0].name == "natation" and x.other_sports[0].days == [5]
    assert x.busy[0].start == date(2026, 12, 14) and x.busy[0].kind == "light"
    assert parse_profile("passe", TODAY).skip


def test_knowledge_has_strength_and_injury_cards():
    from coach.knowledge import cards, tags_for
    ids = [c["id"] for c in cards(tags_for("je n'aime pas le renfo, ça sert à quoi pour les blessures ?"))]
    assert ids[0] == "kb_blessures_lauersen2014" and "kb_ischios_vanderhorst2015" in ids
    perf = [c["id"] for c in cards(tags_for("le renfo ça rend plus rapide ? ça améliore l'économie ?"))]
    assert "kb_force_blagrove2018" in perf
    assert "kb_cardio_gillinov2017" in [c["id"] for c in cards(tags_for("montre au poignet ou ceinture cardio ?"))]


def test_what_the_athlete_said_and_the_agreed_plan_are_kept(tmp_path):
    """v0.17 : ce que l'athlète a dit et le plan validé sont enregistrés, affichés, donnés au coach, sauvegardés."""
    c = make(tmp_path, trail_runner(big_effort_days_ago=12))
    c.post("/api/strava/connect")
    chat(c, "La VVX 85 km")
    chat(c, "Oui, c'est ça")
    chat(c, "5 jours. Repos le lundi. Muscu en salle. Foot le vendredi. Ceinture cardio.")
    chat(c, "Pas de limite")
    chat(c, PY_MSG)
    chat(c, "Ça me va")
    mem = c.get("/api/state").json()["memory"]
    said = " | ".join(mem["commitments"])
    for bit in ("VVX", "Repos le lundi", "Renfo à la salle", "Autre sport : foot le vendredi (intense)",
                "FC : ceinture cardio", "Aime le double seuil", "Doubles possibles le lundi, mardi, jeudi, vendredi",
                "Aime le gros volume", "Week-end léger", "sortie longue le mercredi", "Reprise sérieuse souhaitée"):
        assert bit in said, bit
    a = mem["agreed_plan"]
    assert a["sessions"] > 10 and "Semaine 1" in a["plan_text"] and a["commitments"] == mem["commitments"]
    # le coach le reçoit à chaque message
    summary = c.pilot.repo.get("x").profile_summary
    assert "Préférences et contraintes de l'athlète" in summary and "Repos le lundi" in summary and "double seuil" in summary
    # recommencer à zéro : une sauvegarde complète est faite avant d'effacer
    home = Path(c.pilot.store.path).parent
    assert c.post("/api/reset", json={"confirm": "recommencer"}).json()["ok"]
    arch = sorted((home / "backups").glob("coach-*.tar.gz"))
    assert arch, "pas de sauvegarde avant la remise à zéro"
    import sqlite3
    import tarfile
    with tarfile.open(arch[-1]) as tar:
        tar.extract("pilot.db", tmp_path / "r")
    kept = sqlite3.connect(tmp_path / "r" / "pilot.db").execute(
        "select count(*) from kv where key = 'agreed_plan'").fetchone()[0]
    assert kept == 1



# --------------------------------------------------------------------------- #
# v0.18 : historique long (5 ans)
# --------------------------------------------------------------------------- #
def long_runner(today=TODAY):
    """5 ans : 2022-2023 rapide (10 km en 36:00), une coupure de 6 semaines en 2024, des ultras, puis le trail."""
    acts = []
    d0 = today - timedelta(days=5 * 365)
    for i in range(5 * 365 - 200):
        d = d0 + timedelta(days=i)
        if date(2024, 3, 1) <= d <= date(2024, 4, 12):          # coupure (blessure ?)
            continue
        if d.weekday() in (1, 4):
            acts.append(act(d, 11, 55, 30, "Footing", "Course"))
        elif d.weekday() == 6:
            acts.append(act(d, 20, 120, 200, "Sortie longue", "Course"))
    acts.append(act(date(2023, 4, 2), 10, 36.0, 20, "10 km de Nantes"))
    acts.append(act(date(2023, 10, 22), 21.1, 80.0, 40, "Semi de Nantes"))
    acts.append(act(date(2024, 10, 20), 165, 32 * 60, 9800, "Diagonale des Fous", "Trail", hour=4))
    acts.append(act(date(2025, 5, 9), 85, 10 * 60, 2700, "VVX", "Trail"))
    acts += trail_runner(today)
    return sorted(acts, key=lambda a: a.start_utc)


def test_long_history_profile():
    d = diagnose(long_runner(), TODAY)
    h = d.history
    assert h.years >= 4.5 and len(h.per_year) >= 5
    rec = {r.label: r for r in h.records}
    assert rec["10 km"].time_s == 36 * 60 and rec["10 km"].day == date(2023, 4, 2) and "semi" in rec
    assert [u.name for u in h.ultras[:2]] == ["Diagonale des Fous", "VVX"]
    assert any(g.days >= 40 and g.start.year == 2024 for g in h.gaps)
    assert h.peak_week_min > 0 and h.peak_week_12m_min > 0
    # la forme actuelle reste jugée sur le récent ; le passé dit que la vitesse a déjà été là
    assert abs(d.reference.equivalent_10k_s - 38 * 60) < 5
    f = {x.id: x for x in d.findings}
    assert "vitesse_passee" in f and "2023" in f["vitesse_passee"].detail
    from coach.onboarding import history_brief, history_lines
    txt = "\n".join(history_lines(d))
    assert "Ton historique" in txt and "10 km 36:00 (04/2023)" in txt and "Diagonale des Fous (165 km" in txt
    assert "Coupures de 3 semaines ou plus" in txt
    assert "Historique" in history_brief(d) and "10 km 36:00 (2023)" in history_brief(d)
    from coach.onboarding import recap_message
    assert "Ton historique" in recap_message(d, "Alex")
    # court historique : pas de bloc
    assert history_lines(diagnose(trail_runner(), TODAY)) == []


def test_history_lifts_the_volume_cap_only_when_he_likes_volume():
    d = diagnose(long_runner(), TODAY)
    d.typical_week_min, d.history.peak_week_12m_min = 1000, 1000     # gros coureur : 16 h 40 de pic
    dbl = [0, 1, 3, 4]                                   # les doubles absorbent le volume supplémentaire
    hi = initial_block(d, PROFILE, TODAY + timedelta(days=1), Prefs(likes_volume=True, double_days=dbl))
    lo = initial_block(d, PROFILE, TODAY + timedelta(days=1), Prefs(double_days=dbl))
    assert max(w["minutes"] for w in hi.weeks) > max(w["minutes"] for w in lo.weeks)
    assert any("déjà tenu" in n for n in hi.notes)


def test_first_sync_reads_five_years_and_existing_accounts_are_backfilled(tmp_path):
    from coach.pilot.strava_api import PER_PAGE, StravaAPI, backfill_strava_api, sync_strava_api
    acts = long_runner()
    http = FakeStravaHTTP(api_acts(acts))
    p = build_pilot(home=tmp_path, today_fn=lambda: TODAY, settings=dataclasses.replace(ST))
    api = StravaAPI(tmp_path, "1", "s", http)
    api.tokens.write({"refresh_token": "R", "access_token": "A", "expires_at": 4_000_000_000})
    # compte ancien : synchronisé sur 270 jours seulement
    r = sync_strava_api(p.store, p.settings, api, days=270, today=TODAY, max_new=5000)
    n_recent = len(p.store.activities())
    assert r.report.new == n_recent and min(a.local_day for a in p.store.activities()) >= TODAY - timedelta(days=271)
    calls = len(http.calls)
    back = backfill_strava_api(p.store, p.settings, api, today=TODAY)
    assert back.partial is None and back.report.new > 300 and p.store.get("strava_history_years") == 5
    assert min(a.local_day for a in p.store.activities()) <= TODAY - timedelta(days=4 * 365)
    gets = [c for c in http.calls[calls:] if c[0] == "GET"]
    assert gets and all(c[2]["per_page"] == PER_PAGE == 200 and "before" in c[2] for c in gets)
    assert len(gets) <= 6                                   # ~800 sorties : quelques lectures seulement
    again = backfill_strava_api(p.store, p.settings, api, today=TODAY)
    assert again.report.new == 0


def test_backfill_runs_once_from_the_app_sync(tmp_path):
    """Compte déjà en ligne (9 mois synchronisés) : la synchro suivante complète 5 ans, une seule fois."""
    from coach.pilot.strava_api import StravaAPI, StravaAPIConnector
    http = FakeStravaHTTP(api_acts(long_runner()))
    p = build_pilot(home=tmp_path, today_fn=lambda: TODAY, settings=dataclasses.replace(ST))
    service = CoachService(p, connector=StravaAPIConnector(tmp_path, "123", "sec", http),
                           strava_factory=lambda: StravaAPI(tmp_path, "123", "sec", http), background=False)
    p.strava = StravaAPI(tmp_path, "123", "sec", http)
    p.strava.tokens.write({"refresh_token": "R", "access_token": "A", "expires_at": 4_000_000_000})
    from coach.pilot.strava_api import sync_strava_api
    sync_strava_api(p.store, p.settings, p.strava, days=270, today=TODAY)       # état d'un compte v0.17
    assert p.store.get("strava_history_years") is None
    r = service.sync()
    assert r["ok"] and p.store.get("strava_history_years") == 5
    assert min(a.local_day for a in p.store.activities()) <= TODAY - timedelta(days=4 * 365)
    n = len(p.store.activities())
    calls = len(http.calls)
    service.sync()
    assert len(p.store.activities()) == n
    assert not any("before" in (c[2] or {}) for c in http.calls[calls:])        # plus de rattrapage


def test_new_account_first_sync_reads_five_years(tmp_path):
    from coach.pilot.strava_api import StravaAPI, StravaAPIConnector
    http = FakeStravaHTTP(api_acts(long_runner()))
    p = build_pilot(home=tmp_path, today_fn=lambda: TODAY, settings=dataclasses.replace(ST))
    service = CoachService(p, connector=StravaAPIConnector(tmp_path, "123", "sec", http),
                           strava_factory=lambda: StravaAPI(tmp_path, "123", "sec", http), background=False)
    c = TestClient(create_app(service, passcode=CODE, secret=SECRET, background=False))
    c.post("/api/login", json={"passcode": CODE})
    r = c.post("/api/strava/connect", json={"origin": "http://testserver"}).json()
    state = dict(x.split("=", 1) for x in r["url"].split("?", 1)[1].split("&"))["state"]
    c.get("/strava/callback", params={"code": "good", "state": state, "scope": "read,activity:read_all"})
    assert p.store.get("strava_history_years") == 5
    assert min(a.local_day for a in p.store.activities()) <= TODAY - timedelta(days=4 * 365)
    first = [x for x in http.calls if x[0] == "GET" and x[1].endswith("/athlete/activities")][0]
    assert first[2]["per_page"] == 200
    assert "Ton historique" in c.get("/api/chat").json()["messages"][-1]["text"]


# --------------------------------------------------------------------------- #
# v0.19 : onglet Progrès
# --------------------------------------------------------------------------- #
def test_progress_series_and_estimates():
    from coach.progress import goal_time, progress
    from coach.schemas import PlannedSession
    acts = long_runner()
    for a in acts:                                           # FC sur les footings : efficacité qui progresse
        if a.sport == "Course" and 30 * 60 <= a.duration_s <= 120 * 60 and a.distance_m >= 4000:
            a.avg_hr = 140 + (TODAY - a.local_day).days / 100
    plan = [PlannedSession(session_id=f"p{i}", day=TODAY + timedelta(days=i), intensity="z2", duration_min=60,
                           planned_load=60) for i in range(1, 40)]
    goals = [{"name": "VVX", "date": TODAY + timedelta(days=30), "km": 85, "dplus": 2750, "kind": "trail"},
             {"name": "Semi", "date": TODAY + timedelta(days=60), "km": 21.1, "dplus": 50, "kind": "route"}]
    p = progress(acts, plan, goals, TODAY, 182)
    assert len(p["pmc"]) == 182 and p["pmc"][-1][0] == TODAY.isoformat()
    assert len(p["rolling7"]) == 182 and p["rolling7"][-1][1] == p["now"]["km7"]
    # cumul 7 jours = somme des 7 derniers jours de course
    km7 = sum(a.distance_m for a in acts if is_run_(a) and TODAY - timedelta(days=6) <= a.local_day <= TODAY) / 1000
    assert abs(p["now"]["km7"] - km7) < 0.1
    # projection avec le plan jusqu'au plus proche objectif couvert
    assert p["projection"][-1][0] == goals[0]["date"].isoformat() and p["race_form"]["day"] == goals[0]["date"].isoformat()
    # chronos : la référence est le 10 km de ma ville (38:00), marathon corrigé de +4 % par défaut
    e = p["estimates_now"]
    assert e["10 km"] == 38 * 60 and e["ref"]["name"] == "10 km de ma ville" and e["marathon"] > e["semi"] * 2
    vvx = next(g for g in p["goals"] if g["name"] == "VVX")["estimate"]
    assert 8.5 * 3600 < vvx[1] < 9.3 * 3600 and vvx[0] < vvx[1] < vvx[2]            # ~8 h 54 pour 38:00
    semi = next(g for g in p["goals"] if g["name"] == "Semi")["estimate"]
    assert abs(semi[1] - e["semi"]) / e["semi"] < 0.05
    assert any("goal:VVX" in r for r in p["estimates"])
    # semaines : monotonie, régularité
    assert p["weeks"] and p["weeks"][-1]["partial"] and all("monotony" in w for w in p["weeks"])
    # efficacité : l'allure à FC commune s'améliore (FC plus basse à vitesse égale avec le temps)
    eff = p["efficiency"]
    assert len(eff) >= 10 and eff[-1][1] < eff[0][1] and p["efficiency_hr"]
    # le parcours plus dur coûte plus cher
    assert goal_time(e, 85, 5000, "trail")[1] > goal_time(e, 85, 2750, "trail")[1]


def is_run_(a):
    from coach.diagnosis import is_run
    return is_run(a)


def test_progress_api_and_activity_history(tmp_path):
    c = make(tmp_path, trail_runner(big_effort_days_ago=12))
    c.post("/api/strava/connect")
    chat(c, "La VVX 85 km")
    chat(c, "Oui, c'est ça")
    chat(c, "passe")
    chat(c, "Ça me va")
    p = c.get("/api/progress", params={"days": 91}).json()
    assert len(p["pmc"]) == 91 and p["goals"][0]["name"].startswith("Volvic") and p["goals"][0]["estimate"]
    assert p["projection"] and p["now"]["tsb_label"] in ("frais", "neutre", "en charge", "très fatigué")
    assert len(p["profile"]["axes"]) == 7 and p["profile"]["not_measured"]          # v0.20 : radar
    assert c.get("/api/progress", params={"days": 99999}).json()["range_days"] == 365 * 5 + 2
    a1 = c.get("/api/activities", params={"limit": 10}).json()
    assert len(a1["activities"]) == 10 and a1["next"] and "Trail" in a1["sports"]
    a2 = c.get("/api/activities", params={"limit": 10, "before": a1["next"]}).json()
    assert a2["activities"][0]["day"] < a1["next"]
    p5 = c.get("/api/activities", params={"limit": 5}).json()                                  # v0.23
    assert len(p5["activities"]) == 5 and p5["next_offset"] == 5 and p5["total"] > 5
    p6 = c.get("/api/activities", params={"limit": 5, "offset": 5}).json()
    assert p6["activities"][0] != p5["activities"][-1]
    names = {a["name"] for a in c.get("/api/activities", params={"limit": 200}).json()["activities"] if a["name"]}
    word = sorted(names)[0].split()[0]
    found = c.get("/api/activities", params={"q": word.upper(), "limit": 200}).json()
    assert found["total"] and all(word.lower() in a["name"].lower() for a in found["activities"])
    assert c.get("/api/activities", params={"q": "zzzintrouvable"}).json()["activities"] == []
    only = c.get("/api/activities", params={"sport": "Course", "limit": 50}).json()["activities"]
    assert only and all(a["sport"] == "Course" for a in only)
    c.post("/api/logout")
    assert TestClient(c.app).get("/api/progress").status_code == 401


def test_knowledge_v022_sleep_stress_nutrition_contrast():
    """v0.22 : le coach dispose de fiches sur le sommeil, le stress, l'alimentation et le travail par contraste."""
    from coach.knowledge import KNOWLEDGE, cards, tags_for
    first = lambda q: cards(tags_for(q))[0]["id"]
    assert any(c["id"].startswith("kb_sommeil") for c in cards(tags_for("J'ai mal dormi, je fais quand même le seuil ?")))
    assert first("J'ai très mal dormi cette nuit").startswith("kb_sommeil")
    assert first("Gros stress au boulot cette semaine") == "kb_stress_stultskolehmainen2012"
    assert "kb_nutrition_ultra_tiller2019" in [c["id"] for c in cards(tags_for("Combien de gels par heure en trail ?"))]
    assert first("Des sprints juste après mes squats lourds ?") == "kb_contraste_seitz2016"
    assert first("j'ai mal derrière le genou, il faut renforcer les ischios ?") == "kb_ischios_vanderhorst2015"
    assert len({e.id for e in KNOWLEDGE}) == len(KNOWLEDGE) and all(e.text.startswith("Synthèse") for e in KNOWLEDGE)


def test_heuristic_pain_ignores_dates_and_rpe():
    """Le message auto d'un retour de séance (« Séance du 28/10 … RPE 6/10 ») ne doit pas devenir une douleur 10/10."""
    from coach.sandbox import heuristic_signals
    sig = heuristic_signals("Séance du 28/10 (Footing) faite, RPE 6/10. Un peu mal derrière le genou à la fin", TODAY)
    assert sig.pain and sig.pain[0].severity_0_10 == 4
    sig = heuristic_signals("Douleur au genou 7/10 depuis hier", TODAY)
    assert sig.pain[0].severity_0_10 == 7


def test_goal_correction_after_onboarding_replans(tmp_path):
    """v0.25 : « tu as mis le marathon pour la semaine prochaine, mais c'est pour 2027 » corrige l'échéance et
    propose un plan recalculé, au lieu d'être pris pour une indisponibilité."""
    from coach.onboarding import Event
    c = make(tmp_path, long_runner())
    c.post("/api/strava/connect")
    chat(c, "La VVX 85 km"); chat(c, "Oui, c'est ça")
    chat(c, "5 jours. Repos le lundi."); chat(c, "Pas de limite"); chat(c, "Ça me va")
    svc = c.service
    p = svc.profile()
    p.events = [Event(name="Marathon Nice-Cannes", date=TODAY + timedelta(days=12), role="B", kind="route",
                      distance_km=42.2, source="catalogue")]
    svc.store.set("profile", p.model_dump(mode="json"))
    r = chat(c, "tu as mis marathon de cannes nice pour la semaine procahine, mais c'est pour 2027")
    coach = r["messages"][-1]["text"]
    assert r["pending"] and "Marathon Nice-Cannes : 2027, date à préciser" in coach and "Garder l'ancien plan" in r["quick"]
    ev = svc.profile().events[0]
    assert ev.year == 2027 and ev.date is None
    assert c.get("/api/chat").json()["quick"] == ["Ça me va", "Garder l'ancien plan"]
    r = chat(c, "Ça me va")
    assert not r["pending"] and "C'est parti" in r["messages"][-1]["text"]
    # retrait d'une course, puis on garde l'ancien plan
    r = chat(c, "enlève le marathon de nice, je ne le fais plus")
    assert "retiré" in r["messages"][-1]["meta"].get("trace", "") or r["pending"]
    r = chat(c, "Garder l'ancien plan")
    assert not r["pending"] and svc.profile().events == []
    # une question d'entraînement ne déclenche pas la correction d'échéance
    r = chat(c, "Je fais quoi demain avant ma sortie longue ?")
    assert c.service.store.get("ob_state") == "done"


def test_goal_year_is_respected_by_lookup():
    from coach.race_lookup import from_catalog
    f = from_catalog("VVX Volvic Volcanic Experience", 85, TODAY)
    if f:
        assert from_catalog("VVX Volvic Volcanic Experience", 85, TODAY, year=f.date.year + 5) is None


def test_bonus_sessions_are_optional_and_safe():
    """v0.25 : une séance bonus facultative par semaine de vitesse ou de reprise, jamais un jour de repos, ni la
    veille d'une séance clé, charge 0 (hors volume et hors garde-fou) ; désactivable."""
    from coach.onboarding import Prefs, is_bonus, proposal_message
    d = diagnose(trail_runner(), TODAY)
    prefs = Prefs(rest_days=[0])
    b = initial_block(d, PROFILE, TODAY + timedelta(days=1), prefs)
    bonus = [s for s in b.sessions if is_bonus(s)]
    assert bonus and all(s.planned_load == 0 and s.day.weekday() != 0 for s in bonus)
    keys = {s.day for s in b.sessions if s.intensity in ("threshold", "vo2")}
    assert all(s.day + timedelta(days=1) not in keys for s in bonus)
    assert not any(is_bonus(s) for s in initial_block(d, PROFILE, TODAY + timedelta(days=1),
                                                       Prefs(rest_days=[0], bonus=False)).sessions)
    assert "⭐" in proposal_message(d, PROFILE, b, first=True, today=TODAY, prefs=prefs)
