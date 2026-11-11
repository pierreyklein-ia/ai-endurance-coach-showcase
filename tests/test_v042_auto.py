"""v0.42 : validation automatique des séances prévues par les sorties synchronisées."""
from datetime import UTC, date, datetime, timedelta

from coach.career import Facts, career
from coach.pilot.store import Activity
from coach.schemas import PlannedSession
from coach.web.auto_validate import AUTO_KEY, match_sessions
from tests.test_pilot import TODAY
from tests.test_v037 import ready


def run(day: date, minutes: float, sport: str = "Course", hour: int = 7, name: str = "Footing", km: float = 10):
    return Activity(start_utc=datetime(day.year, day.month, day.day, hour, tzinfo=UTC), local_day=day, sport=sport,
                    duration_s=minutes * 60, distance_m=km * 1000, dplus_m=50, avg_hr=140, load=minutes,
                    load_source="estimate", name=name, moving_s=minutes * 60)


def sess(sid: str, day: date, minutes: int, intensity: str = "z2", race: bool = False):
    return PlannedSession(session_id=sid, day=day, intensity=intensity, duration_min=minutes, planned_load=minutes,
                          description="", is_race=race)


Y = TODAY - timedelta(days=1)


def test_match_rules():
    sessions = [sess("a", Y, 60), sess("b", Y, 30), sess("r", Y, 0, "rest"), sess("f", TODAY + timedelta(days=1), 50),
                sess("v", Y - timedelta(days=1), 60), sess("race", Y - timedelta(days=2), 300, "race", True)]
    acts = [run(Y, 58, hour=7), run(Y, 31, hour=18), run(Y - timedelta(days=1), 25, name="Raccourci"),
            run(Y - timedelta(days=2), 300)]
    m = {x["session"].session_id: x for x in match_sessions(sessions, acts, {}, {}, TODAY)}
    assert set(m) == {"a", "b", "v"}                          # ni repos, ni futur, ni course
    assert m["a"]["status"] == "done" and m["a"]["activity"].duration_s == 58 * 60
    assert m["b"]["activity"].duration_s == 31 * 60           # une activité ne valide qu'une séance
    assert m["v"]["status"] == "modified" and m["v"]["ratio"] < 0.7


def test_already_validated_and_non_run_activities_are_ignored():
    s = [sess("a", Y, 60), sess("b", Y, 60)]
    acts = [run(Y, 60, sport="Vélo", km=30), run(Y, 60)]
    m = match_sessions(s, acts, {}, {"a": {"status": "skipped"}}, TODAY)
    assert [x["session"].session_id for x in m] == ["b"]


def test_strength_session_matches_a_renfo_activity():
    s = [sess("renfo", Y, 40, "rest")]
    assert match_sessions(s, [run(Y, 45, sport="Renfo", km=0)], {"renfo": {"kind": "renfo"}}, {}, TODAY)
    assert not match_sessions(s, [run(Y, 45)], {"renfo": {"kind": "renfo"}}, {}, TODAY)


def test_service_validates_yesterday_from_synced_run_and_athlete_can_complete(tmp_path):
    c = ready(tmp_path)
    store = c.pilot.store
    store.upsert_session(sess("hier", Y, 50))                # séance prévue hier, sortie courue sans validation
    s = next(x for x in store.plan(Y, Y) if x.session_id == "hier")
    store.add_activity(run(Y, s.duration_min or 50))
    done = c.service.auto_validate()
    assert [d["session_id"] for d in done] == [s.session_id]
    fb = store.feedback_for([s.session_id])[s.session_id]
    assert fb["status"] == "done" and fb["rpe"] is None and fb["note"] == ""
    w = c.get(f"/api/week?start={Y - timedelta(days=Y.weekday())}").json()
    got = next(x for d in w["days"] for x in d["sessions"] if x["session_id"] == s.session_id)
    assert got["feedback"]["auto"]["minutes"] == (s.duration_min or 50)
    assert c.service.auto_validate() == []                    # idempotent
    # l'athlète complète son ressenti : la validation devient la sienne
    r = c.post(f"/api/sessions/{s.session_id}/feedback", json={"status": "done", "rpe": 6, "note": "bonnes jambes"})
    assert r.status_code == 200
    assert (store.get(AUTO_KEY) or {})[s.session_id]["confirmed"]       # v0.44 : le lien est gardé, confirmé


def test_auto_validation_gives_session_xp_but_not_feedback_trophies(tmp_path):
    c = ready(tmp_path)
    store = c.pilot.store
    s = next(x for x in store.plan() if x.intensity != "rest")
    fb = [{"session_id": s.session_id, "day": s.day.isoformat(), "status": "done", "rpe": None, "note": "", "at": ""}]
    facts = Facts([], fb, store.plan(), {}, today=TODAY)
    assert not any(t["id"].startswith("retours_") and t.get("day") for t in career(facts)["trophies"])
