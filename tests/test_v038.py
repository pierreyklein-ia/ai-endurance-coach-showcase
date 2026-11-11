"""v0.38 — déplacement sans blocage + réaction du coach en tâche de fond (bug « rien dans le chat »),
alerte « 2 séances dures le même jour », bilan nutrition des sorties longues, fiches nutrition."""
import time
from datetime import date, timedelta

from coach import nutrition as nu
from coach.knowledge import KNOWLEDGE, NUTRITION_V038, cards, tags_for
from coach.nutrition_foods import target_carbs_g_h
from coach.pilot.store import Activity
from coach.sandbox import HeuristicLLM
from coach.schemas import PlannedSession
from tests.test_pilot import TODAY, _major_llm
from tests.test_v037 import planned, ready

SID = "coach-2026-10-01"          # seuil du jeudi 1/10 (TODAY = mercredi 30/09/2026)
LINE = "J'ai déplacé « Seuil 3 × 10 min » de jeudi 1/10 à vendredi 2/10."


class SlowLLM:
    """LLM factice lent (comme Claude en production : 20-60 s, ici quelques dixièmes de seconde)."""

    def __init__(self, inner, delay: float = 0.0, fail: bool = False):
        self.inner, self.delay, self.fail = inner, delay, fail

    def __getattr__(self, k):
        return getattr(self.inner, k)

    def structured(self, **kw):
        if kw.get("node") == "coach":
            time.sleep(self.delay)
            if self.fail:
                raise RuntimeError("API Claude indisponible (529)")
        return self.inner.structured(**kw)


def msgs(c):
    return c.get("/api/chat").json()


# --------------------------------------------------------------------------- 1. le chat après un déplacement
def test_move_returns_fast_and_chat_gets_athlete_line_then_coach_reply(tmp_path):
    c = ready(tmp_path, llm=SlowLLM(HeuristicLLM(TODAY), delay=4.0))
    t0 = time.time()
    r = c.post(f"/api/sessions/{SID}/move", json={"day": "2026-10-02"})
    assert r.status_code == 200 and time.time() - t0 < 3.0       # la requête n'attend pas le LLM (4 s)
    b = r.json()
    assert b["moved"] and b["typing"] and [m["text"] for m in b["messages"]] == [LINE]
    h = msgs(c)                                                     # chat ouvert tout de suite
    assert h["messages"][-1]["text"] == LINE and h["typing"] is True
    assert c.get("/api/week").status_code == 200                    # les vues restent disponibles pendant le tour
    assert c.service.wait_coach(20)
    h = msgs(c)
    assert h["typing"] is False and h["messages"][-2]["text"] == LINE
    assert h["messages"][-1]["role"] == "coach" and "agenda" in h["messages"][-1]["text"]
    assert planned(c)[SID].day == date(2026, 10, 2)


def test_failing_llm_still_posts_a_coach_message_with_warnings(tmp_path):
    c = ready(tmp_path, llm=SlowLLM(HeuristicLLM(TODAY), delay=0.2, fail=True))
    b = c.post(f"/api/sessions/{SID}/move", json={"day": "2026-10-03"}).json()   # seuil samedi, SL dimanche
    assert b["moved"] and any("dures d'affilée" in w for w in b["warnings"])
    assert c.service.wait_coach(10)
    last = msgs(c)["messages"][-1]
    assert last["role"] == "coach" and last["kind"] == "reply" and last["meta"].get("fallback")
    assert last["text"].startswith("C'est noté, ton plan et ton agenda sont à jour.") and "dures d'affilée" in last["text"]
    assert not msgs(c)["typing"]


def test_inline_mode_is_deterministic(tmp_path):
    c = ready(tmp_path)
    c.service.coach_async = False                                   # mode synchrone : pas de thread
    b = c.post(f"/api/sessions/{SID}/move", json={"day": "2026-10-02"}).json()
    assert not b["typing"]
    assert msgs(c)["messages"][-1]["role"] == "coach"


def test_coach_always_answers_even_over_budget(tmp_path):
    c = ready(tmp_path)
    c.pilot.settings.daily_budget_eur = 0.0
    c.post(f"/api/sessions/{SID}/move", json={"day": "2026-10-02"})
    c.service.wait_coach()
    last = msgs(c)["messages"][-1]
    assert last["role"] == "coach" and last["kind"] == "reply" and "agenda" in last["text"]


def test_typed_chat_waits_for_the_background_turn(tmp_path):
    """Un message tapé pendant la réaction du coach passe après elle (un seul tour de graphe à la fois)."""
    c = ready(tmp_path, llm=SlowLLM(HeuristicLLM(TODAY), delay=0.6))
    c.post(f"/api/sessions/{SID}/move", json={"day": "2026-10-02"})
    out = c.post("/api/chat", json={"text": "Je me sens bien aujourd'hui"}).json()
    assert out["messages"][-1]["role"] == "coach"
    texts = [(m["role"], m["text"]) for m in msgs(c)["messages"]]
    i = texts.index(("user", LINE))
    assert texts[i + 1][0] == "coach" and texts[i + 2] == ("user", "Je me sens bien aujourd'hui")


# --------------------------------------------------------------------------- 2. jamais de blocage
def test_move_reaction_never_forces_a_lighter_week(tmp_path):
    """Plan déjà « interdit » par les garde-fous après le choix de l'athlète : pas de repli automatique."""
    c = ready(tmp_path)
    before = {k: (v.intensity, v.duration_min) for k, v in planned(c).items()}
    c.post("/api/sessions/coach-2026-10-07/move", json={"day": "2026-10-05"})
    c.service.wait_coach()
    after = {k: (v.intensity, v.duration_min) for k, v in planned(c).items()}
    assert after == before                                          # seul le jour a changé
    assert "fallback" not in msgs(c)["messages"][-1]["meta"].get("nodes", [])


def test_pending_question_keeps_proposal_and_confirmation_keeps_athlete_day(tmp_path):
    c = ready(tmp_path, llm=_major_llm())
    r = c.post("/api/chat", json={"text": "Jeudi soir je n'aurai que 30 minutes, on adapte ?"}).json()
    assert r["pending"]                                             # proposition : seuil du jeudi -> 30 min faciles
    n_llm = len(c.pilot.deps.llm.usage_log)
    b = c.post(f"/api/sessions/{SID}/move", json={"day": "2026-10-02"}).json()
    assert b["moved"] and b["pending"] and not b["typing"]
    assert len(c.pilot.deps.llm.usage_log) == n_llm                 # le texte n'est pas passé par le graphe
    assert "reste en attente" in b["messages"][-1]["text"]
    assert c.pilot.store.get("moved_since_pending") == {SID: "2026-10-02"}
    assert msgs(c)["pending"]
    r = c.post("/api/chat", json={"text": "oui"}).json()
    assert not r["pending"]
    s = planned(c)[SID]
    assert s.intensity == "z1" and s.day == date(2026, 10, 2)       # proposition appliquée, jour de l'athlète gardé
    ev = [e for e in c.gsvc.coach_events() if e["extendedProperties"]["private"]["coach_session_id"] == SID]
    assert len(ev) == 1 and ev[0]["start"]["dateTime"].startswith("2026-10-02") and "30 min" in ev[0]["summary"]
    assert c.pilot.store.get("moved_since_pending") == {}


def test_pending_question_answered_no_clears_the_memory(tmp_path):
    c = ready(tmp_path, llm=_major_llm())
    c.post("/api/chat", json={"text": "Jeudi soir je n'aurai que 30 minutes, on adapte ?"})
    c.post(f"/api/sessions/{SID}/move", json={"day": "2026-10-02"})
    c.post("/api/chat", json={"text": "non"})
    s = planned(c)[SID]
    assert s.intensity == "threshold" and s.day == date(2026, 10, 2)
    assert c.pilot.store.get("moved_since_pending") == {}


def test_rebase_helper_keeps_cancel():
    from coach.schemas import PlanChange
    from coach.web.move_flow import rebase_changes
    s = PlannedSession(session_id="a", day=date(2026, 10, 1), intensity="z1", duration_min=30, planned_load=20)
    out = rebase_changes([PlanChange(action="modify", session=s, reason=""),
                          PlanChange(action="cancel", session=s, reason="")], {"a": "2026-10-03"})
    assert out[0].session.day == date(2026, 10, 3) and out[1].session.day == date(2026, 10, 1)


# --------------------------------------------------------------------------- 3. deux séances dures le même jour
def test_week_flags_a_day_with_two_hard_sessions(tmp_path):
    c = ready(tmp_path)
    days = {d["date"]: d for d in c.get("/api/week").json()["days"]}
    assert all(d["alert"] is None for d in days.values())
    b = c.post(f"/api/sessions/{SID}/move", json={"day": "2026-10-04"}).json()    # seuil sur la sortie longue
    assert any(w.startswith("deux séances dures le dimanche 4/10") for w in b["warnings"])
    assert any(a["level"] == "strong" for a in b["alerts"])
    c.service.wait_coach()
    days = {d["date"]: d for d in c.get("/api/week").json()["days"]}
    al = days["2026-10-04"]["alert"]
    assert al["kind"] == "two_hard" and al["label"] == "2 séances dures le même jour" and SID in al["sessions"]
    assert days["2026-10-01"]["alert"] is None


def test_two_hard_alert_ignores_races_and_zero_load():
    from coach.web.move_flow import two_hard_alert
    d = date(2026, 10, 4)
    mk = lambda i, it, load=50.0, race=False: PlannedSession(session_id=i, day=d, intensity=it, duration_min=60,   # noqa: E731
                                                              planned_load=load, is_race=race)
    assert two_hard_alert([mk("a", "vo2"), mk("b", "z2")]) is None
    assert two_hard_alert([mk("a", "vo2"), mk("b", "long", race=True)]) is None
    assert two_hard_alert([mk("a", "vo2"), mk("b", "tempo", load=0)]) is None
    assert two_hard_alert([mk("a", "vo2"), mk("b", "tempo")])["sessions"] == ["a", "b"]


# --------------------------------------------------------------------------- 4. bilan nutrition
KEYS = {"date", "session_id", "activity_id", "duration_h", "items", "water_ml", "carbs_g_h", "sodium_mg_h",
        "fluid_ml_h", "digestion", "symptoms", "comment"}
LONG = "coach-2026-10-04"         # sortie longue du dimanche


def add_run(c, day: date, minutes: int):
    from datetime import datetime
    c.pilot.store.add_activity(Activity(start_utc=datetime(day.year, day.month, day.day, 7, 0), local_day=day,
                                        sport="Trail", duration_s=minutes * 60, distance_m=20000, dplus_m=800,
                                        avg_hr=140, load=150, load_source="hrTSS", name="SL"))


def test_long_sessions_are_eligible_for_a_nutrition_debrief(tmp_path):
    c = ready(tmp_path)
    w = c.get("/api/week", params={"start": "2026-09-28"}).json()
    ss = {s["session_id"]: s for d in w["days"] for s in d["sessions"]}
    assert ss[LONG]["nutrition_eligible"] and ss[LONG]["nutrition"] is None
    short = next(s for s in ss.values() if 0 < s["duration_min"] < 90)
    assert not short["nutrition_eligible"]
    add_run(c, date(2026, 10, 1), 130)                              # durée réelle prioritaire sur la prévue
    w = c.get("/api/week", params={"start": "2026-09-28"}).json()
    thu = next(s for d in w["days"] for s in d["sessions"] if s["session_id"] == SID)
    assert thu["actual_min"] == 130 and thu["nutrition_eligible"]


def test_nutrition_debrief_is_stored_with_exact_shape_and_coach_proposes(tmp_path):
    c = ready(tmp_path)
    add_run(c, date(2026, 10, 4), 130)
    foods = c.get("/api/nutrition/foods").json()
    assert {f["id"] for f in foods["foods"]} >= {"gel", "iso", "compote", "pate_fruits", "soupe"}
    assert [s["id"] for s in foods["symptoms"]] == ["nausee", "ballonnements", "crampes_estomac", "envie_pressante",
                                                    "ecoeurement_sucre", "hypo", "crampes_musculaires"]
    body = {"session_id": LONG, "items": [{"id": "gel", "qty": 3}, {"id": "iso", "qty": 1},
                                          {"id": "pate_fruits", "qty": 2, "carbs_g": 22}],
            "water_ml": 500, "digestion": 4, "symptoms": ["nausee", "ecoeurement_sucre"], "comment": "dur à la fin"}
    r = c.post("/api/nutrition", json=body)
    assert r.status_code == 200, r.text
    b = r.json()
    log = c.pilot.store.get("nutrition_logs")[0]
    assert set(log) == KEYS and log == b["log"]
    assert log["date"] == "2026-10-04" and log["session_id"] == LONG and log["activity_id"]
    assert log["duration_h"] == round(130 / 60, 2) and log["digestion"] == 4
    assert log["items"] == [{"id": "gel", "qty": 3.0, "carbs_g": None}, {"id": "iso", "qty": 1.0, "carbs_g": None},
                            {"id": "pate_fruits", "qty": 2.0, "carbs_g": 22.0}]
    assert log["carbs_g_h"] == round((3 * 25 + 30 + 2 * 22) / (130 / 60))     # 69 g/h
    assert log["fluid_ml_h"] == round(1000 / (130 / 60)) and log["symptoms"] == ["nausee", "ecoeurement_sucre"]
    assert isinstance(log["water_ml"], float) and isinstance(log["sodium_mg_h"], float)
    line = b["messages"][0]["text"]
    assert line.startswith("Sortie longue 2 h 10 : 69 g/h (3 gels, 1 iso, 2 pâtes de fruits), digestion 4/5, "
                           "nausée, écœurement du sucré")
    assert c.service.wait_coach()
    reply = msgs(c)["messages"][-1]
    assert reply["role"] == "coach" and "Pistes pour la prochaine sortie longue" in reply["text"]
    assert "salé" in reply["text"] and "dilue" in reply["text"]                # propositions concrètes
    assert "[Bilan nutrition" not in "".join(m["text"] for m in msgs(c)["messages"])
    # une saisie par séance (corrigée, pas dupliquée) ; résumé et vue État
    c.post("/api/nutrition", json={**body, "digestion": 2, "symptoms": []})
    c.service.wait_coach()
    n = c.get("/api/nutrition").json()
    assert len(n["logs"]) == 1 and n["summary"]["trained_max_g_h"] == 69.0
    assert n["summary"]["tolerated"][:1] == ["gel"] and c.get("/api/state").json()["nutrition"]["count"] == 1
    w = c.get("/api/week", params={"start": "2026-09-28"}).json()
    assert next(s for d in w["days"] for s in d["sessions"] if s["session_id"] == LONG)["nutrition"]["digestion"] == 2
    # le coach voit le résumé dans le profil
    assert "max toléré 69 g/h" in c.pilot.repo.get("pilote").profile_summary


def test_nutrition_validation(tmp_path):
    c = ready(tmp_path)
    assert c.post("/api/nutrition", json={"session_id": LONG, "digestion": 6}).status_code == 422
    assert c.post("/api/nutrition", json={"session_id": "nope", "digestion": 2}).status_code == 404
    assert c.post("/api/nutrition", json={"digestion": 2}).status_code == 422          # ni séance ni date
    r = c.post("/api/nutrition", json={"day": "2026-09-27", "duration_min": 150, "digestion": 1,
                                       "items": [{"id": "inconnu", "qty": 2}, {"id": "gel", "qty": 6}]})
    assert r.status_code == 200 and r.json()["log"]["items"] == [{"id": "gel", "qty": 6.0, "carbs_g": None}]
    assert r.json()["log"]["session_id"] is None


def test_nutrition_while_pending_does_not_answer_the_question(tmp_path):
    c = ready(tmp_path, llm=_major_llm())
    c.post("/api/chat", json={"text": "Jeudi soir je n'aurai que 30 minutes, on adapte ?"})
    b = c.post("/api/nutrition", json={"session_id": LONG, "digestion": 5, "symptoms": ["hypo"],
                                       "items": [{"id": "gel", "qty": 1}]}).json()
    assert b["pending"] and "reste en attente" in b["messages"][-1]["text"] and msgs(c)["pending"]


def L(g, dig=2, sy=(), items=(("gel", 2),), h=2.0, d="2026-10-01", water=0.0, na=200.0, fl=500.0):
    return {"date": d, "session_id": None, "activity_id": None, "duration_h": h,
            "items": [{"id": i, "qty": q, "carbs_g": None} for i, q in items], "water_ml": water,
            "carbs_g_h": g, "sodium_mg_h": na, "fluid_ml_h": fl, "digestion": dig, "symptoms": list(sy), "comment": ""}


def test_advice_rules():
    a = nu.advice(L(30, sy=["hypo"]))
    assert any("+10 g/h" in x and "~40 g/h" in x for x in a)
    assert any("redescends" in x for x in nu.advice(L(100)))
    assert not any("redescends" in x for x in nu.advice(L(100), [L(85)]))       # tolérance déjà éprouvée
    assert any("salé" in x for x in nu.advice(L(60, 4, ["nausee"])))
    assert any("fruits secs" in x and "fructose" in x for x in nu.advice(L(60, 4, ["envie_pressante"],
                                                                         (("fruits_secs", 2),))))
    assert any("sodium" in x for x in nu.advice(L(60, 2, ["crampes_musculaires"])))
    assert any("nouveau maximum" in x for x in nu.advice(L(75), [L(60)]))
    assert any("hyponatrémie" in x for x in nu.advice(L(60, fl=1100)))


def test_summary_and_brief():
    logs = [L(55, 2, d="2026-09-01", items=(("gel", 2), ("compote", 1))),
            L(70, 1, d="2026-09-15", items=(("gel", 3), ("iso", 1))),
            L(80, 5, ["nausee"], d="2026-09-29", items=(("gel_hd", 3), ("gel", 1)))]
    s = nu.summary(logs)
    assert s["trained_max_g_h"] == 70 and s["tolerated"][0] == "gel" and s["avoid"] == ["gel_hd"]
    assert [x["date"] for x in s["last"]] == ["2026-09-29", "2026-09-15", "2026-09-01"]
    assert "max toléré 70 g/h" in nu.prompt_brief(logs) and nu.prompt_brief([]) == ""
    assert target_carbs_g_h(3, s["trained_max_g_h"]) == (60, 80)


def test_athlete_line_format():
    log = nu.build_log(date="2026-10-04", session_id="x", activity_id=None, duration_h=130 / 60,
                       items=[{"id": "gel", "qty": 3}, {"id": "iso", "qty": 1}, {"id": "pate_fruits", "qty": 2}],
                       water_ml=0, digestion=2, symptoms=["nausee"], comment="")
    assert nu.athlete_line(log) == "Sortie longue 2 h 10 : 65 g/h (3 gels, 1 iso, 2 pâtes de fruits), digestion 2/5, nausée"


# --------------------------------------------------------------------------- 5. fiches nutrition
def top(q):
    return [c["id"] for c in cards(tags_for(q))]


def test_nutrition_questions_route_to_the_new_cards():
    assert "kb_glucides_jeukendrup2014" in top("combien de gels par heure ?")
    assert "kb_palette_aliments_projet" in top("je mange quoi au ravito ?")
    assert top("j'ai eu mal au ventre pendant ma sortie longue")[:2] == ["kb_estomac_costa2017", "kb_digestif_costa2017"]
    assert "kb_cafeine_guest2021" in top("un gel à la caféine la nuit ?")
    assert "kb_hyponatremie_hewbutler2015" in top("je dois boire combien d'eau par heure ?")
    assert set(NUTRITION_V038) <= {e.id for e in KNOWLEDGE} and set(NUTRITION_V038.values()) == {"a_relire"}
    assert all(e.text.startswith("Synthèse") for e in KNOWLEDGE if e.id in NUTRITION_V038)


def test_graph_tolerance_only_for_preexisting_rules(make_deps):
    """Deps.tolerated : une violation déjà présente ne bloque pas, une nouvelle bloque toujours."""
    from coach.app import handle_inbound
    from coach.graph import build_graph
    from coach.llm import MockLLM
    from coach.schemas import CoachDecision, InboundMessage, PlanChange, Signals
    from tests.conftest import PLAN
    extra = [PlanChange(action="add", reason="x", session=PlannedSession(
        session_id=f"x{i}", day=PLAN[0].day + timedelta(days=i + 1), intensity="vo2", duration_min=90,
        planned_load=200)) for i in range(3)]
    for changes, tol, fallback in (([], {"*"}, False), (extra, {"*"}, True)):
        llm = MockLLM(handlers={"extract": lambda u: Signals(),
                                "coach": lambda u, ch=changes: CoachDecision(changes=ch, message_to_athlete="ok")})
        deps = make_deps(llm)
        deps.tolerated = lambda t=tol: t
        trace = []
        from langgraph.checkpoint.memory import MemorySaver
        handle_inbound(build_graph(deps, MemorySaver()), InboundMessage(athlete_id="a", channel="web", text="J'ai déplacé x"), trace)
        assert ("fallback" in [n for n, _ in trace]) is fallback


def test_turns_are_served_in_message_order_even_if_thread_starts_late(tmp_path):
    """v0.39.1 (CI) : le thread de la réaction peut démarrer après le message tapé ; l'ordre reste celui des messages."""
    import threading
    c = ready(tmp_path, llm=SlowLLM(HeuristicLLM(TODAY), delay=0.3))
    real_start = threading.Thread.start
    def late_start(self):                                           # simule un runner lent
        if not self.name.startswith("coach-"):
            return real_start(self)
        t = threading.Timer(0.5, lambda: real_start(self))
        real_start(t)
    threading.Thread.start = late_start
    try:
        c.post(f"/api/sessions/{SID}/move", json={"day": "2026-10-02"})
    finally:
        threading.Thread.start = real_start
    c.post("/api/chat", json={"text": "Je me sens bien aujourd'hui"})
    assert c.service.wait_coach(10)
    texts = [(m["role"], m["text"]) for m in msgs(c)["messages"]]
    i = texts.index(("user", LINE))
    assert texts[i + 1][0] == "coach" and texts[i + 2] == ("user", "Je me sens bien aujourd'hui")
