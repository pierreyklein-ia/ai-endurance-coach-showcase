"""v0.50.1 : une séance transformée par le coach (seuil -> footing) garde un titre juste, et si l'athlète a fait
la séance d'origine, la validation automatique la compte « faite » (bug du seuil 3 × 8 min « modifié »)."""
from datetime import timedelta

from coach.schemas import PlanChange
from coach.session_meta import orig_kind, stale, sync
from coach.session_type import detect
from coach.web.auto_validate import AUTO_KEY, evaluate, match_sessions
from tests.test_pilot import TODAY
from tests.test_v037 import ready
from tests.test_v042_auto import run, sess
from tests.test_v043_session_type import EASY, SEUIL, WARM, prof

Y = TODAY - timedelta(days=1)
FOOTING_TXT = "Footing facile en endurance, allure tranquille, sans fractionné."


def eased(sid="s1", day=Y):
    return sess(sid, day, 40, "z2").model_copy(update={"description": FOOTING_TXT})


def test_sync_follows_intensity_and_keeps_origin():
    meta = {"s1": {"title": "Seuil", "kind": "threshold", "key": True, "detail": "20' + 3 × 8'"}}
    old = sess("s1", Y, 60, "threshold")
    assert sync(meta, old, eased())
    m = meta["s1"]
    assert m["kind"] == "z2" and m["title"] != "Seuil" and not m["key"] and m["detail"] == FOOTING_TXT
    assert m["orig"] == {"title": "Seuil", "kind": "threshold"}
    assert orig_kind(meta, eased()) == "threshold"
    assert sync(meta, eased(), old) and "orig" not in meta["s1"]          # retour à la séance d'origine
    assert not sync(meta, old, old)
    renfo = {"r": {"kind": "renfo", "title": "Renfo"}}
    assert not sync(renfo, sess("r", Y, 40, "z2"), sess("r", Y, 40, "z1"))
    carnet = {"c": {"kind": "seuil", "title": "Seuil club"}}                # type du carnet, pas une intensité
    assert not stale(carnet, sess("c", Y, 50, "threshold"))
    assert sync(carnet, sess("c", Y, 50, "threshold"), sess("c", Y, 50, "z2"))
    assert carnet["c"]["orig"]["kind"] == "threshold"


def test_original_session_done_counts_as_done():
    a = run(Y, 45)
    meta = {"s1": {"title": "Seuil", "kind": "threshold"}}                # données d'avant le correctif
    m = match_sessions([eased()], [a], meta, {}, TODAY, {a.key: SEUIL})[0]
    assert m["status"] == "done" and m["type"]["verdict"] == "origine" and m["type"]["ok"]
    m = match_sessions([eased()], [a], {}, {}, TODAY, {a.key: SEUIL})[0]  # sans origine : plus dur que prévu
    assert m["status"] == "modified" and m["type"]["verdict"] == "plus_dur"
    assert evaluate(eased(), a, False, {a.key: EASY}, "threshold")["type"]["verdict"] == "conforme"


def test_seuil_3x8_with_hr_lag_is_seuil():
    p = prof(WARM + [(300, 160, 4.0, 0), (180, 166, 4.0, 0), (120, 140, 3.0, 0)] * 3 + [(600, 130, 3.0, 0)])
    assert 8 <= p["zones"][3] + p["zones"][4] < 12
    assert detect(p) == "seuil"


def test_coach_change_updates_title(tmp_path):
    c = ready(tmp_path)
    st = c.pilot.store
    st.upsert_session(sess("k", TODAY + timedelta(days=1), 60, "threshold"))
    st.set("session_meta", {**st.get("session_meta"), "k": {"title": "Seuil", "kind": "threshold", "key": True}})
    new = sess("k", TODAY + timedelta(days=1), 40, "z2").model_copy(update={"description": FOOTING_TXT})
    c.pilot.deps.calendar.apply("pilote", [PlanChange(action="modify", session=new, reason="fatigue")])
    m = st.get("session_meta")["k"]
    assert m["kind"] == "z2" and m["orig"]["title"] == "Seuil"
    w = c.get(f"/api/week?start={TODAY - timedelta(days=TODAY.weekday())}").json()
    v = next(x for d in w["days"] for x in d["sessions"] if x["session_id"] == "k")
    assert v["orig_title"] == "Seuil" and not v["key"]


def test_startup_repair_fixes_title_and_verdict(tmp_path):
    c = ready(tmp_path)
    st = c.pilot.store
    st.upsert_session(eased())
    st.set("session_meta", {**st.get("session_meta"), "s1": {"title": "Seuil", "kind": "threshold", "key": True}})
    a = run(Y, 59, name="3x8'")
    a.profile = SEUIL
    st.add_activity(a)
    st.set("session_meta", {**st.get("session_meta"), "s1": {"title": "Seuil", "kind": "threshold", "key": True}})
    # état d'avant le correctif : validée « modifiée », plus dur que prévu
    st.set_feedback("s1", Y, "modified", None, "")
    st.set(AUTO_KEY, {**(st.get(AUTO_KEY) or {}), "s1": {
        "activity": "3x8'", "start": a.start_utc.isoformat(), "day": Y.isoformat(), "minutes": 59, "km": 12.3,
        "ratio": 1.48, "status": "modified", "manual": False,
        "type": {"verdict": "plus_dur", "ok": False, "detected": "seuil", "label": "seuil", "text": "plus dur que prévu"}}})
    r = c.service.repair_session_meta()
    assert r == {"meta": 1, "feedback": 1}
    assert st.feedback_for(["s1"])["s1"]["status"] in ("done", "modified")
    info = st.get(AUTO_KEY)["s1"]
    assert info["type"]["verdict"] == "origine" and info["type"]["ok"]
    assert st.get("session_meta")["s1"]["orig"]["title"] == "Seuil"
    assert c.service.repair_session_meta() == {"meta": 0, "feedback": 0}  # idempotent


def test_repair_keeps_athlete_feedback(tmp_path):
    c = ready(tmp_path)
    st = c.pilot.store
    st.upsert_session(eased())
    st.set("session_meta", {**st.get("session_meta"), "s1": {"title": "Seuil", "kind": "threshold"}})
    a = run(Y, 45)
    a.profile = SEUIL
    st.add_activity(a)
    st.set_feedback("s1", Y, "modified", 7, "dur")
    st.set(AUTO_KEY, {"s1": {"start": a.start_utc.isoformat(), "status": "modified",
                             "type": {"verdict": "plus_dur", "ok": False}}})
    assert c.service.repair_session_meta()["feedback"] == 0
    assert st.feedback_for(["s1"])["s1"]["status"] == "modified"


# --------------------------------------------------------------------------- repli des garde-fous
def test_fallback_keeps_the_plan_without_risk_and_eases_only_48h_with_risk():
    from coach.guardrails import safe_fallback
    from coach.schemas import Pain, Signals
    plan = [sess("t", TODAY, 60, "threshold"), sess("e", TODAY + timedelta(days=1), 50),
            sess("v", TODAY + timedelta(days=1), 50, "vo2"), sess("l", TODAY + timedelta(days=4), 120, "long")]
    assert safe_fallback(plan, Signals(), TODAY) == []
    ch = safe_fallback(plan, Signals(pain=[Pain(location="genou", severity_0_10=6)]), TODAY)
    assert [c.session.session_id for c in ch] == ["t", "v"] and all(c.session.intensity == "z1" for c in ch)


def test_preexisting_load_violation_no_longer_blocks_but_pain_still_does():
    from coach.guardrails import tolerate_preexisting
    from coach.schemas import GuardrailVerdict, Violation
    r05 = Violation(rule_id="R05_catch_up", detail="semaine")
    pain = Violation(rule_id="R01_pain", detail="douleur")
    before = GuardrailVerdict(allowed=False, violations=[r05, pain])
    v = tolerate_preexisting(GuardrailVerdict(allowed=False, violations=[r05]), before)
    assert v.allowed and v.violations[0].severity == "warn"
    v = tolerate_preexisting(GuardrailVerdict(allowed=False, violations=[r05, pain]), before)
    assert not v.allowed and [x.rule_id for x in v.blocking] == ["R01_pain"]
    assert tolerate_preexisting(GuardrailVerdict(allowed=False, violations=[pain]), before, {"*"}).allowed
    new = tolerate_preexisting(GuardrailVerdict(allowed=False, violations=[r05]), GuardrailVerdict(allowed=True))
    assert not new.allowed                                               # nouvelle violation : bloque toujours


def test_restore_week_from_latest_clean_backup(tmp_path):
    from datetime import datetime
    from pathlib import Path

    from coach.backup import snapshot
    from coach.guardrails import safe_fallback
    from coach.schemas import Pain, Signals
    from coach.web.fallback_repair import KEY, repair
    c = ready(tmp_path)
    st = c.pilot.store
    st.upsert_session(sess("w1", TODAY + timedelta(days=2), 60, "threshold"))
    st.set("session_meta", {**st.get("session_meta"), "w1": {"title": "Seuil 3 × 8 min", "kind": "threshold"}})
    home = Path(st.path).parent
    st.db.commit()
    snapshot(home, home / "backups", datetime(2026, 9, 29, 3, 17))
    # ancien repli : toute la semaine en footing (simulé avec le repli actuel sur signal de douleur, 7 jours)
    plan = st.plan(TODAY)
    ch = safe_fallback(plan, Signals(pain=[Pain(location="genou", severity_0_10=6)]), TODAY, days=7)
    c.pilot.deps.calendar.apply("pilote", ch)
    w = next(s for s in st.plan(TODAY) if s.session_id == "w1")
    assert w.intensity == "z1" and st.get("session_meta")["w1"]["orig"]["title"] == "Seuil 3 × 8 min"
    snapshot(home, home / "backups", datetime(2026, 9, 30, 23, 0))     # sauvegarde d'après le repli : ignorée
    st.set(KEY, None)
    n = repair(c.service)
    assert n == len(ch)
    w = next(s for s in st.plan(TODAY) if s.session_id == "w1")
    assert w.intensity == "threshold" and w.duration_min == 60
    assert st.get("session_meta")["w1"] == {"title": "Seuil 3 × 8 min", "kind": "threshold"}
    assert "Correctif" in c.get("/api/chat").json()["messages"][-1]["text"]
    assert repair(c.service) == 0                                         # une seule fois
