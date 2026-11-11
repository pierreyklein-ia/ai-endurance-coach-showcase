"""Garde-fous : zones ACWR validées, affûtage (Wang 2023), sévérités."""
from datetime import timedelta

from coach.guardrails import PlanContext, apply_changes, check
from coach.load_engine import LoadState
from coach.rules_registry import REGISTRY, registry_hash
from coach.schemas import Pain, PlanChange, PlannedSession, Signals
from tests.conftest import PLAN, RACE, TODAY, d

STEADY = LoadState(ctl=80, atl=80, tsb=0, acwr=1.0, a7=80, c28=80)


def sess(i, load, intensity="z2", sid=None, **kw):
    return PlannedSession(session_id=sid or f"t{i}", day=d(i), intensity=intensity,
                          duration_min=60, planned_load=load, **kw)


def ctx(**kw):
    base = dict(state=STEADY, baseline_week_load=10_000, today=TODAY, race_date=None)
    return PlanContext(**{**base, **kw})


def rules(verdict):
    return {(v.rule_id, v.severity) for v in verdict.violations}


def test_forgotten_hard_session_with_pain_is_blocked():
    easy = PLAN[0].model_copy(update={"intensity": "z1", "planned_load": 25})
    plan_after = apply_changes(PLAN, [PlanChange(action="modify", session=easy, reason="")])
    v = check(plan_after, Signals(pain=[Pain(location="genou", severity_0_10=4)]),
              ctx(state=LoadState(95, 100, -5, 1.05), baseline_week_load=515, race_date=RACE))
    assert not v.allowed and ("R01_pain", "block") in rules(v)


def test_vigilance_zone_blocked_unless_shock_block_declared():
    choc = [sess(i, 160, "long") for i in range(7)]          # 2x la charge chronique -> ACWR ~1,34
    assert ("R03_acwr_vigilance", "block") in rules(check(choc, Signals(), ctx()))
    ok = check(choc, Signals(), ctx(shock_block_active=True))
    assert ok.allowed and not any(r.startswith("R03") for r, _ in rules(ok))


def test_danger_zone_blocked_even_in_shock_block():
    massive = [sess(i, 420, "long") for i in range(7)]      # ~5x la charge chronique -> ACWR ~1,74
    assert ("R03_acwr_danger", "block") in rules(check(massive, Signals(), ctx(shock_block_active=True)))


def test_high_zone_warns_in_shock_block_but_blocks_otherwise():
    big = [sess(i, 280, "long") for i in range(7)]          # 3,5x -> ACWR ~1,58
    assert ("R03_acwr_high", "warn") in rules(check(big, Signals(), ctx(shock_block_active=True)))
    assert check(big, Signals(), ctx(shock_block_active=True)).allowed
    assert ("R03_acwr_vigilance", "block") in rules(check(big, Signals(), ctx()))


def test_undertraining_only_warns():
    v = check([sess(0, 10)], Signals(), ctx())
    assert v.allowed and ("R03_acwr_under", "warn") in rules(v)


def test_race_is_excluded_from_acwr_ceiling():
    race = sess(0, 2500, "race", is_race=True)
    v = check([race], Signals(), ctx())
    assert not any(r.startswith("R03_acwr_d") or r.startswith("R03_acwr_v") for r, _ in rules(v))


def test_placeholder_rule_cannot_block():
    assert REGISTRY["load.tsb_floor"].status == "placeholder"
    tired = LoadState(ctl=60, atl=120, tsb=-60, acwr=1.4, a7=60, c28=60)
    v = check([sess(0, 20, "z1")], Signals(), ctx(state=tired))
    assert ("R04_tsb", "warn") in rules(v)


def test_taper_volume_ceiling_and_intensity():
    race = TODAY + timedelta(days=7)          # dernière semaine avant course
    week = [sess(i, 70, "z2") for i in range(6)]            # 420 = pré-affûtage, 0 qualité
    v = check(week, Signals(), ctx(race_date=race, pre_taper_week_load=420,
                                   pre_taper_sessions_per_week=6))
    assert ("R06_taper_volume", "block") in rules(v)
    assert ("R06_taper_intensity", "warn") in rules(v)

    good = [sess(0, 60, "threshold"), sess(1, 40), sess(2, 40), sess(4, 45, "tempo"), sess(5, 30)]
    v2 = check(good, Signals(), ctx(race_date=race, pre_taper_week_load=420,
                                    pre_taper_sessions_per_week=6))
    assert v2.allowed and not any(r in ("R06_taper_volume", "R06_taper_intensity") for r, _ in rules(v2))


def test_registry_hash_is_stable():
    assert registry_hash() == registry_hash() and len(registry_hash()) == 12


def test_recovery_rules_fire_on_alcohol_and_stress():
    from coach.rules_registry import REGISTRY
    seuil_demain = [sess(0, 80, "threshold"), sess(1, 50, "z2")]
    v = check(seuil_demain, Signals(alcohol="heavy", sleep_quality_1_5=2), ctx())
    expected = "block" if REGISTRY["recovery.no_intensity_hours"].status != "placeholder" else "warn"
    assert ("R07_recovery", expected) in rules(v)
    v2 = check(seuil_demain, Signals(life_stress_1_5=4), ctx())
    assert any(r == "R08_life_stress" for r, _ in rules(v2))
    assert not any(r.startswith("R07") for r, _ in rules(check(seuil_demain, Signals(alcohol="moderate"), ctx())))


# --- v0.27 : pic sur une sortie (Frandsen 2025) -------------------------------------------------
from coach.guardrails import cap_peak_sessions  # noqa: E402


def _long(i, minutes, **kw):
    return PlannedSession(session_id=f"p{i}", day=d(i), intensity=kw.pop("intensity", "long"),
                          duration_min=minutes, planned_load=minutes, **kw)


def _peak(plan, ref=100):
    v = check(plan, Signals(), ctx(longest_run_30d_min=ref, baseline_week_load=10_000))
    return {x.rule_id for x in v.violations}


def test_peak_under_110_percent_passes():
    assert "R09_peak_session" not in _peak([_long(1, 105)])


def test_peak_120_percent_warns_and_is_not_capped():
    assert "R09_peak_session" in _peak([_long(1, 120)])
    ch = [PlanChange(action="add", session=_long(1, 120), reason="")]
    out, notes = cap_peak_sessions(ch, ctx(longest_run_30d_min=100))
    assert out[0].session.duration_min == 120 and notes == []


def test_peak_150_percent_is_capped_to_110():
    ch = [PlanChange(action="add", session=_long(1, 150, elevation_gain_m=1500), reason="")]
    out, notes = cap_peak_sessions(ch, ctx(longest_run_30d_min=100))
    s = out[0].session
    assert s.duration_min == 110 and s.elevation_gain_m == 1100 and len(notes) == 1 and "Frandsen" in notes[0]


def test_peak_not_checked_on_race_bonus_or_short_resume():
    race = _long(1, 400, intensity="race", is_race=True)
    bonus = PlannedSession(session_id="b", day=d(2), intensity="z2", duration_min=300, planned_load=0)
    assert "R09_peak_session" not in _peak([race, bonus])
    ch = [PlanChange(action="add", session=race, reason=""), PlanChange(action="add", session=bonus, reason="")]
    assert cap_peak_sessions(ch, ctx(longest_run_30d_min=100))[1] == []
    # reprise : plus longue sortie de 30 min, séance de 45 min (sous le seuil de 50 min) : pas de contrôle
    assert "R09_peak_session" not in _peak([_long(1, 45, intensity="z2")], ref=30)
    assert "R09_peak_session" not in _peak([_long(1, 300)], ref=None)
