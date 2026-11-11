"""v0.24 : carrière (XP, niveaux, trophées, avatar)."""
from datetime import timedelta

from coach.career import CATALOG, Facts, career, clean_avatar, level_for, one_rm, total_for_level
from tests.test_onboarding import TODAY, act, chat, long_runner, make


def test_each_level_costs_a_bit_more_and_level_20_is_reachable():
    # v0.34 : 380 XP pour le niveau 2, puis +58 XP par niveau ; niveau 7 inchangé (3 150 XP)
    assert [total_for_level(n) for n in range(1, 6)] == [0, 380, 818, 1314, 1868]
    assert total_for_level(7) == 3150 and 5000 < total_for_level(10) < 6000 and total_for_level(20) < 20_000
    needs = [level_for(total_for_level(n))["need"] for n in range(1, 25)]
    assert all(b > a for a, b in zip(needs, needs[1:]))                  # de plus en plus long, sans saut
    assert level_for(0)["level"] == 1 and level_for(379)["level"] == 1 and level_for(380)["level"] == 2
    assert level_for(190)["pct"] == 50 and level_for(total_for_level(24))["rank"] == "Légende ★4"
    assert level_for(total_for_level(7))["rank"] == "Ultra-traileur"


def test_first_5k_gives_10_xp_and_level_up_after_more():
    d0 = TODAY - timedelta(days=30)
    c = career(Facts([act(d0, 5.1, 28)], today=TODAY))
    first = next(t for t in c["trophies"] if t["id"] == "dist_5")
    assert first["day"] == d0.isoformat() and first["xp"] == 10 and c["xp"] >= 10
    c2 = career(Facts([act(d0, 5.1, 28), act(d0 + timedelta(days=7), 10.2, 55), act(d0 + timedelta(days=14), 21.2, 115)],
                      today=TODAY))
    assert c2["xp"] >= 50 and c2["level"] == 1 and c2["levels"] == []                 # v0.34 : 380 XP pour le niveau 2


def test_speed_needs_flat_full_distance_and_plausible_speed():
    d = TODAY - timedelta(days=10)
    fast_hilly = act(d, 10.0, 38, dplus=400)                   # 40 m/km : pas un chrono de référence
    gps_glitch = act(d, 10.0, 20)                               # 30 km/h : GPS aberrant
    c = career(Facts([fast_hilly, gps_glitch], today=TODAY))
    assert not next(t for t in c["trophies"] if t["id"] == "vit_10000_2400")["day"]
    c = career(Facts([act(d, 10.0, 38)], today=TODAY))
    assert next(t for t in c["trophies"] if t["id"] == "vit_10000_2400")["day"] == d.isoformat()


def test_retroactive_history_and_no_reward_for_daily_streaks():
    c = career(Facts(long_runner(), today=TODAY))
    assert c["count"] > 20 and c["level"] >= 5 and c["timeline"]
    assert not any("jours" in t.label for t in CATALOG)        # pas de série de jours d'affilée
    assert len({t.id for t in CATALOG}) == len(CATALOG)


def test_strength_ratio_from_declared_sets():
    assert round(one_rm(170, 5)) == 198
    f = Facts([], strength=[{"day": "2026-10-20", "exercise": "box_squat", "kg": 170, "reps": 5, "bodyweight": 82}],
              today=TODAY)
    c = career(f)
    got = {t["id"] for t in c["trophies"] if t["day"]}
    assert {"squat_1", "squat_1.5", "squat_2"} <= got and "squat_2.5" not in got


def test_avatar_gear_locked_by_level():
    a = clean_avatar({"skin": 4, "hair": "chignon", "gear": ["bandeau", "laurier"], "hack": 1}, level=3)
    assert a["skin"] == 4 and a["hair"] == "chignon" and a["gear"] == ["bandeau"] and "hack" not in a
    assert clean_avatar({}, 1)["gear"] is None                   # auto : tout l'équipement débloqué


def test_career_api(tmp_path):
    c = make(tmp_path, long_runner())
    c.post("/api/strava/connect")
    chat(c, "La VVX 85 km"); chat(c, "Oui, c'est ça"); chat(c, "5 jours. Repos le lundi. Muscu en salle."); chat(c, "Pas de limite"); chat(c, "Ça me va")
    r = c.get("/api/career").json()
    assert r["first_visit"] and r["level"] >= 2 and "_done" not in r and r["avatar"]["gear"] is None
    assert c.post("/api/career/seen").json()["ok"]
    r2 = c.get("/api/career").json()
    assert not r2["first_visit"] and r2["new"] == [] and not r2["level_up"]
    xp0 = r2["xp"]
    r3 = c.post("/api/career/strength", json={"exercise": "box_squat", "kg": 170, "reps": 5, "bodyweight": 82}).json()
    assert r3["xp"] > xp0 and {t["id"] for t in r3["new"]} >= {"squat_2"}
    assert c.post("/api/career/strength", json={"exercise": "curl", "kg": 10, "reps": 5, "bodyweight": 82}).status_code == 422
    a = c.post("/api/career/avatar", json={"skin": 1, "hair": "long", "beard": True, "gear": ["frontale"]}).json()["avatar"]
    assert a["hair"] == "long" and a["beard"] and (a["gear"] == ["frontale"]) == (r3["level"] >= 10)
    # séance du plan validée : XP
    sid = c.get("/api/week").json()["days"]
    sid = next(s for d in sid for s in d["sessions"])["session_id"]
    c.post(f"/api/sessions/{sid}/feedback", json={"status": "done", "rpe": 5})
    assert c.get("/api/career").json()["session_xp"] >= 3


def test_strava_like_streak_counts_any_activity_and_weeks_with_3_runs():
    acts = []
    for w in range(60):                                       # 60 semaines : 1 sortie + 1 vélo, parfois 3 sorties
        d = TODAY - timedelta(days=7 * w + 2)
        acts.append(act(d, 8, 45))
        acts.append(act(d - timedelta(days=1), 30, 60, sport="Vélo"))
        if w % 2 == 0:
            acts += [act(d - timedelta(days=2), 8, 45), act(d - timedelta(days=3), 8, 45)]
    c = career(Facts(acts, today=TODAY))
    got = {t["id"]: t for t in c["trophies"]}
    assert got["serie_52"]["day"] and not got["serie_104"]["day"]          # série « à la Strava »
    assert got["sem3_25"]["day"] and not got["sem3_100"]["day"]
    assert "série en cours" in got["serie_104"]["progress"]


def test_bonus_session_gives_xp_and_skipping_costs_nothing():
    from coach.schemas import PlannedSession
    d = TODAY - timedelta(days=3)
    s = PlannedSession(session_id=f"start-{d.isoformat()}-bonus", day=d, intensity="z1", duration_min=30, planned_load=0)
    done = career(Facts([], [{"session_id": s.session_id, "day": d.isoformat(), "status": "done"}], [s], today=TODAY))
    skip = career(Facts([], [{"session_id": s.session_id, "day": d.isoformat(), "status": "skipped"}], [s], today=TODAY))
    assert done["session_xp"] == 4 and skip["session_xp"] == 0


def test_strava_sport_types_are_relabeled(tmp_path):
    from coach.pilot.ingest import sport_label
    from coach.pilot.store import PilotStore
    st = PilotStore(tmp_path / "p.db")
    a = act(TODAY, 0, 45, sport="Weighttraining")
    st.add_activity(a)
    assert st.relabel_sports(sport_label) == 1 and st.activities()[0].sport == "Renfo"
    assert sport_label("WeightTraining") == "Renfo" and sport_label("TrailRun") == "Trail"
