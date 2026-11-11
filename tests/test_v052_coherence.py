"""v0.52 : cohérence de la discussion avec le coach (audit du 9 nov. 2026)."""
import json
from datetime import timedelta

from coach.llm import MockLLM
from coach.onboarding import OtherSport, Prefs, TurnExtract, merge_club_sport, sanitize_turn
from coach.persona import CoachPersona, persona_block
from coach.pilot.runtime import recent_chat
from coach.schemas import CoachDecision, Signals
from coach.web.prefs_repair import KEY, repair_v052, restore_from_commitments
from tests.test_graph import run
from tests.test_pilot import TODAY
from tests.test_v037 import ready
from tests.test_v048_partner import GOAL_TEXT, _client, chat, club_runner

PY_148 = "Je n'avais plus de sortie longue prévu cette semaine il me semble! Sinon on fait pas de double seuil jeudi " \
         "mais une sortie longue avec allure marathon ?"
CLUB_TUE = Prefs(quality_days=[1], club_quality=True, double_threshold=True,
                 other_sports=[OtherSport(name="club de trail", days=[1], hard=True),
                               OtherSport(name="simulateur d'escalier")])


def test_coach_receives_recent_chat_and_preference_rules(make_deps):
    seen = {}

    def coach(u):
        seen.update(json.loads(u))
        return CoachDecision(changes=[], message_to_athlete="ok")
    deps = make_deps(MockLLM(handlers={"extract": lambda u: Signals(), "coach": coach}))
    deps.history = lambda: [{"role": "coach", "text": "Partant pour patienter 7 jours ?"},
                            {"role": "athlete", "text": "Non"}]
    run(deps, "Non, je veux mon double seuil jeudi")
    assert seen["recent_chat"][0]["text"].startswith("Partant")


def test_recent_chat_keeps_the_last_messages(tmp_path):
    c = ready(tmp_path)
    st = c.pilot.store
    for i in range(12):
        st.add_chat("user" if i % 2 else "coach", "text", f"m{i}" + "x" * 900)
    h = recent_chat(st)
    assert len(h) == 8 and h[-1]["role"] == "athlete" and len(h[-1]["text"]) == 700


def test_persona_agrees_with_the_athlete_sex():
    p = CoachPersona(gender="femme", personality="bienveillant", name="Léa", address="tu")
    assert "partant »" in persona_block(p, "expert", "M") and "partante" in persona_block(p, "expert", "F")
    assert "L'athlète est" not in persona_block(p, "expert", None)


def test_sanitize_turn_keeps_club_double_threshold_and_volume():
    bad = TurnExtract(intent="modify", quality_days=[3], club_quality=True, double_threshold=False, volume_factor=0.85)
    t = sanitize_turn(bad, PY_148, CLUB_TUE)
    assert t.quality_days is None and t.double_threshold is None and t.volume_factor is None
    ok = TurnExtract(intent="modify", double_threshold=False, volume_factor=0.85)
    t = sanitize_turn(ok, "Désormais plus de double seuil, et moins de volume", CLUB_TUE)
    assert t.double_threshold is False and t.volume_factor == 0.85
    move = TurnExtract(intent="modify", quality_days=[3], club_quality=True)
    assert sanitize_turn(move, "Le club passe au jeudi", CLUB_TUE).quality_days == [3]


def test_club_sport_is_not_counted_twice():
    p = merge_club_sport(CLUB_TUE)
    assert [o.name for o in p.other_sports] == ["simulateur d'escalier"]
    two = CLUB_TUE.model_copy(update={"other_sports": [OtherSport(name="club de trail", days=[1, 5])]})
    assert merge_club_sport(two).other_sports[0].days == [5]
    assert merge_club_sport(Prefs()) == Prefs()


def test_restore_from_commitments():
    said = ["6 jours de course par semaine, sortie longue le lundi", "Séance d'intensité au club le mardi (1 h 15) : à garder",
            "Aime le double seuil (matin + soir, contrôlé)"]
    p, ch = restore_from_commitments({"quality_days": [3], "club_quality": True, "double_threshold": False,
                                      "volume_factor": 0.85}, said)
    assert p["quality_days"] == [1] and p["double_threshold"] and p["volume_factor"] == 1.0 and len(ch) == 3
    assert restore_from_commitments(p, said)[1] == []


def test_startup_repair_cancels_the_bad_proposal(tmp_path):
    c = ready(tmp_path)
    st = c.pilot.store
    st.set("agreed_plan", {"commitments": ["Séance d'intensité au club le mardi (1 h 15) : à garder",
                                           "Aime le double seuil (matin + soir, contrôlé)"]})
    st.set("prefs", Prefs(quality_days=[3], club_quality=True, volume_factor=0.85).model_dump(mode="json"))
    st.set("ob_state", "replan")
    st.set("pending_onboarding_plan", {"sessions": [], "session_meta": {}, "weeks": {}})
    st.set(KEY, None)
    ch = repair_v052(c.service)
    assert len(ch) == 3 and st.get("ob_state") == "done" and st.get("pending_onboarding_plan") is None
    pr = st.get("prefs")
    assert pr["quality_days"] == [1] and pr["double_threshold"] and pr["volume_factor"] == 1.0
    assert "Correctif" in st.chat(1)[0]["text"]
    assert repair_v052(c.service) == []


def test_refusing_the_new_plan_restores_previous_settings(tmp_path):
    c = _client(tmp_path, club_runner())
    c.post("/api/strava/connect")
    chat(c, GOAL_TEXT)
    chat(c, "Oui, c'est ça")
    chat(c, "5 jours, sortie longue le dimanche")
    chat(c, "Pas de limite")
    chat(c, "Ça me va")
    svc = c.service
    before = svc.prefs().model_dump()
    r = chat(c, "Tous les mardis soir j'ai ma séance avec le club")
    assert r["pending"] and svc.prefs().quality_days == [1]
    r = chat(c, "Garder l'ancien plan")
    assert "réglages d'avant" in r["messages"][-1]["text"]
    assert svc.prefs().model_dump() == before


def test_pending_yes_no_question_goes_to_the_coach_first(tmp_path, monkeypatch):
    c = ready(tmp_path)
    svc = c.service
    monkeypatch.setattr(svc, "_graph_pending", lambda: True)
    called = {}
    monkeypatch.setattr(svc, "_prefs_edit_turn", lambda *a: called.setdefault("prefs", True))
    monkeypatch.setattr(svc, "_coach_turn", lambda user, text, **k: {"messages": [user], "pending": False})
    svc.chat("Tous les mardis j'ai club, et pas de double seuil jeudi")
    assert "prefs" not in called


def test_burst_of_moves_gets_one_reaction(tmp_path):
    c = ready(tmp_path)
    svc = c.service
    import time
    svc._typing.append({"what": "move", "since": time.monotonic() + 100})    # un déplacement plus récent attend
    ran = []
    svc._spawn_job("move", lambda: ran.append(1), lambda: ran.append("err"))
    assert ran == []
    svc._typing.clear()
    svc._spawn_job("move", lambda: ran.append(1), lambda: ran.append("err"))
    assert ran == [1]
    s = next(x for x in c.pilot.store.plan(TODAY + timedelta(days=1)) if x.intensity != "rest")
    assert c.post(f"/api/sessions/{s.session_id}/move", json={"day": (s.day + timedelta(days=1)).isoformat()}).status_code == 200


def test_refusal_made_before_v052_is_repaired(tmp_path):
    from coach.web.prefs_repair import KEY_REFUSED, OLD_REFUSAL, repair_v0521
    c = ready(tmp_path)
    st = c.pilot.store
    st.set("agreed_plan", {"adopted_at": "2000-01-01T08:52",
                           "commitments": ["Séance d'intensité au club le mardi (1 h 15) : à garder",
                                           "Aime le double seuil (matin + soir, contrôlé)"]})
    st.set("prefs", Prefs(quality_days=[3], club_quality=True, volume_factor=0.85).model_dump(mode="json"))
    st.set(KEY_REFUSED, None)
    assert repair_v0521(c.service) == []                                   # pas de refus : rien
    st.set(KEY_REFUSED, None)
    st.add_chat("coach", "reply", f"D'accord, je garde le plan actuel ({OLD_REFUSAL} et serviront pour le prochain bloc).")
    assert len(repair_v0521(c.service)) == 3
    assert st.get("prefs")["quality_days"] == [1] and "Correctif" in st.chat(1)[0]["text"]
    assert repair_v0521(c.service) == []


def test_club_session_accepts_any_quality_content():
    from coach.onboarding import CLUB_TEXT
    from coach.web.auto_validate import evaluate
    from tests.test_v042_auto import run as act
    from tests.test_v042_auto import sess
    from tests.test_v043_session_type import EASY, SEUIL
    s = sess("club", TODAY, 75, "vo2").model_copy(update={"description": CLUB_TEXT})
    a = act(TODAY, 75)
    ev = evaluate(s, a, False, {a.key: SEUIL})
    assert ev["status"] == "done" and ev["type"]["verdict"] == "conforme"
    assert evaluate(s, a, False, {a.key: EASY})["type"]["verdict"] == "plus_facile"


def test_sync_every_30_min_and_on_app_open(tmp_path, monkeypatch):
    import time as _t

    from coach.web.service import CoachService
    c = ready(tmp_path)
    svc = c.service
    calls = []
    monkeypatch.setattr(svc, "sync", lambda *a, **k: calls.append(1) or {"ok": True, "new": 0})
    monkeypatch.setattr(svc.pilot, "strava", object())
    monkeypatch.setattr(svc, "needs_onboarding", lambda: False)
    svc.background = False
    svc.last_sync = _t.time() - 3600
    r = c.get("/api/today").json()
    assert r["syncing"] is True and calls == [1]
    assert c.get("/api/today").json()["syncing"] is False and calls == [1]     # trop récent : rien
    monkeypatch.setenv("COACH_SYNC_MIN", "5")
    assert CoachService(svc.pilot, svc.notifier).sync_every_s == 600           # au moins 10 min
    monkeypatch.delenv("COACH_SYNC_MIN")
    assert CoachService(svc.pilot, svc.notifier).sync_every_s == 1800


def test_threshold_reps_with_a_fast_last_one_are_threshold_not_vma():
    from coach.session_type import detect
    from tests.test_v043_session_type import VMA, WARM, prof
    # séance du club du 10/11 ramenée à une FC seuil de 170 : 4 × 4' en zone 4, la 5e à fond (zone 5)
    p = prof(WARM + [(240, 163, 4.2, 0), (90, 145, 3.0, 0)] * 2 + [(240, 167, 4.3, 0), (90, 150, 3.0, 0)] * 2
             + [(240, 178, 4.7, 0), (600, 135, 2.8, 0)])
    assert detect(p) == "seuil", p
    assert detect(VMA) == "vma"


def test_club_target_gives_both_threshold_and_vma_cues():
    from coach.hr_zones import HrZones, target
    from coach.onboarding import CLUB_TEXT
    t = target("vo2", CLUB_TEXT, HrZones(199, 40, 180, {}, {}))
    assert t["zone"] == "Club" and t["hr"] == [171, 180] and "seuil" in t["text"] and "VMA" in t["text"]
