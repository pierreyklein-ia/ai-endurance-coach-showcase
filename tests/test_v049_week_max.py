"""v0.49 — volume maximum par semaine : question de l'accueil, préférences, plans plafonnés, garde-fou R10."""
import dataclasses
from datetime import date, timedelta

from coach.diagnosis import diagnose
from coach.guardrails import PlanContext, check
from coach.load_engine import LoadState
from coach.onboarding import Prefs, Profile, initial_block, looks_like_prefs_edit, parse_turn
from coach.rules_registry import REGISTRY, can_block
from coach.schemas import CoachDecision, PlanChange, PlannedSession, Signals
from coach.week_max import (
    WeekMax,
    best_4w_avg_min,
    excess_minutes,
    fit_week,
    infer,
    looks_like_week_max,
    parse_week_max,
    totals,
)
from tests.conftest import PLAN, FakeRepo, d
from tests.test_onboarding import TODAY, chat, make, trail_runner

TOL = 1.05


# --------------------------------------------------------------------------- lecture des réponses
def test_parse_week_max_phrasings():
    x = parse_week_max("6 h max")
    assert x.hours == 6 and not x.km and not x.unknown
    x = parse_week_max("pas plus de 50 km et 1500 m D+")
    assert x.km == 50 and x.dplus_m == 1500 and x.hours is None
    assert parse_week_max("6h30 par semaine, 1 500 m de dénivelé").hours == 6.5
    assert parse_week_max("6h30 par semaine, 1 500 m de dénivelé").dplus_m == 1500
    assert parse_week_max("5 h").hours == 5 and parse_week_max("8").hours == 8
    assert parse_week_max("300 min").hours == 5
    assert parse_week_max("8 heures et 60 km").km == 60
    assert parse_week_max("2000D+ max").dplus_m == 2000
    for t in ("Je ne sais pas", "aucune idée", "j'sais pas trop", "à toi de voir"):
        x = parse_week_max(t)
        assert x.unknown and not x.hours, t
    assert parse_week_max("Pas de limite").no_limit
    assert not parse_week_max("bof").hours and not parse_week_max("bof").unknown


def test_chat_correction_is_a_ceiling_not_a_target():
    assert looks_like_week_max("je peux monter à 7 h par semaine")
    assert looks_like_week_max("pas plus de 60 km par semaine")
    assert not looks_like_week_max("environ 7 h par semaine")          # une cible, pas un plafond
    assert looks_like_prefs_edit("je peux monter à 7 h par semaine")
    t = parse_turn("Je peux monter à 7 h par semaine maximum", TODAY)
    assert t.intent == "modify" and t.max_week_hours == 7 and t.weekly_hours is None
    t = parse_turn("Je veux faire environ 7 h par semaine", TODAY)
    assert t.weekly_hours == 7 and t.max_week_hours is None


def test_infer_is_1_3_times_best_recent_4_weeks():
    acts = trail_runner()
    best = best_4w_avg_min(acts, TODAY)
    mins, b = infer(acts, TODAY)
    assert b == best and best > 200
    assert abs(mins - 1.3 * best) <= 8 and mins % 15 == 0
    assert infer([], TODAY)[0] == 300                                   # pas d'historique : 5 h


# --------------------------------------------------------------------------- application au plan
def _week_totals(sessions, wm):
    weeks = {}
    for s in sessions:
        weeks.setdefault(s.day - timedelta(days=s.day.weekday()), []).append(s)
    return {m: totals(v, wm) for m, v in weeks.items()}


def _profile(**kw):
    base = dict(first_name="Alex", goal_name="Volvic Volcanic Experience 85 km", goal_date=date(2027, 1, 16),
                goal_distance_km=85, goal_dplus_m=2750, days_per_week=5, long_day=6)
    return Profile(**{**base, **kw})


def test_first_plan_respects_time_km_and_dplus_caps():
    d = diagnose(trail_runner(), TODAY)
    start = TODAY + timedelta(days=1)
    free = initial_block(d, _profile(), start, Prefs(likes_volume=True))
    wm_free = WeekMax(kmh=8, dph=300)
    peak = max(t[0] for t in _week_totals(free.sessions, wm_free).values())
    assert peak > 330
    # temps
    b = initial_block(d, _profile(), start, Prefs(likes_volume=True, max_week_min=300))
    for m, t in _week_totals(b.sessions, WeekMax(minutes=300)).items():
        assert t[0] <= 300, (m, t)
    assert any("volume maximum" in n for n in b.notes) or peak <= 300
    # km (estimés avec sa vitesse habituelle)
    from coach.week_max import from_prefs
    pk = Prefs(likes_volume=True, max_week_km=35)
    wm = from_prefs(pk, d)
    b = initial_block(d, _profile(), start, pk)
    assert all(t[1] <= 35 + 0.5 for t in _week_totals(b.sessions, wm).values())
    # D+ (phase spécifique comprise : « ~32 m D+/km » des sorties vallonnées)
    pd = Prefs(likes_volume=True, max_week_dplus=1200)
    wm = from_prefs(pd, d)
    b = initial_block(d, _profile(), start, pd)
    assert any(w.get("specific") for w in b.weeks)
    assert all(t[2] <= 1200 * TOL for t in _week_totals(b.sessions, wm).values())
    # jamais sous des planchers raisonnables
    for s in b.sessions:
        if s.intensity in ("z1", "z2") and not s.session_id.endswith("-bonus"):
            assert s.duration_min >= 30
        if s.intensity == "long":
            assert s.duration_min >= 45


def test_cap_and_first_week_115_percent_take_the_min():
    from coach.onboarding import FIRST_PLAN_CAP, recent_week_avg
    from tests.test_v048_partner import TODAY as T2
    from tests.test_v048_partner import club_runner
    d = diagnose(club_runner(), T2)
    start = T2 + timedelta(days=5)                                     # un lundi : semaine entière
    p = _profile(goal_name="Trail", goal_date=date(2027, 5, 8), goal_distance_km=21, goal_dplus_m=500,
                 days_per_week=3, long_day=4)
    avg = recent_week_avg(d, start)
    huge = initial_block(d, p, start, Prefs(max_week_min=900))
    none = initial_block(d, p, start, Prefs())
    w1 = lambda b: sum(s.duration_min for s in b.sessions if start <= s.day < start + timedelta(days=7)   # noqa: E731
                       and s.intensity not in ("rest", "race") and not s.session_id.endswith("-bonus"))
    assert w1(huge) == w1(none) <= FIRST_PLAN_CAP * avg + 5           # le 115 % l'emporte sur un grand plafond
    small = initial_block(d, p, start, Prefs(max_week_min=90))
    assert w1(small) <= 90                                             # le plafond l'emporte s'il est plus bas


def test_fit_week_trims_easy_runs_first_then_the_long_run():
    mk = lambda i, inten, mn: PlannedSession(session_id=f"x{i}", day=date(2026, 11, 2) + timedelta(days=i),  # noqa: E731
                                             intensity=inten, duration_min=mn, planned_load=mn)
    wk = [mk(0, "z2", 60), mk(1, "threshold", 70), mk(3, "z2", 60), mk(6, "long", 150)]
    out, t = fit_week(wk, WeekMax(minutes=300))
    by = {s.session_id: s.duration_min for s in out}
    assert t and sum(by.values()) <= 300
    assert by["x6"] == 150 and by["x1"] == 70 and by["x0"] + by["x3"] == 80      # footings d'abord
    out, _ = fit_week(wk, WeekMax(minutes=200))
    by = {s.session_id: s.duration_min for s in out}
    assert sum(by.values()) <= 200 and by["x1"] == 70 and by["x6"] < 150         # puis la sortie longue
    assert all(v >= 30 for v in by.values())
    assert excess_minutes(totals(out, WeekMax(minutes=200)), WeekMax(minutes=200)) <= 0


# --------------------------------------------------------------------------- garde-fou R10
STEADY = LoadState(ctl=80, atl=80, tsb=0, acwr=1.0, a7=80, c28=80)


def test_r10_blocks_a_proposal_over_the_cap_and_is_registered():
    assert can_block("week.max_tolerance") and REGISTRY["week.max_tolerance"].status == "validated"
    week = sum(s.duration_min for s in PLAN if s.intensity != "rest")
    ctx = PlanContext(state=STEADY, baseline_week_load=10_000, today=d(0), week_max=WeekMax(minutes=week + 10))
    assert check(PLAN, Signals(), ctx, plan_before=PLAN).allowed
    extra = PlannedSession(session_id="x", day=d(2), intensity="z2", duration_min=60, planned_load=50)
    v = check(PLAN + [extra], Signals(), ctx, plan_before=PLAN)
    assert not v.allowed and ("R10_week_max", "block") in {(x.rule_id, x.severity) for x in v.violations}
    # un plan déjà au-delà ne fait pas refuser une proposition qui n'ajoute rien (ex. un échange de jours)
    tight = dataclasses.replace(ctx, week_max=WeekMax(minutes=300))
    assert check(PLAN, Signals(), tight, plan_before=PLAN).allowed
    # km et D+ aussi
    v = check(PLAN, Signals(), dataclasses.replace(ctx, week_max=WeekMax(dplus=1000, dph=300)), plan_before=[])
    assert ("R10_week_max", "block") in {(x.rule_id, x.severity) for x in v.violations}
    # pas de plafond : rien ne change
    assert not any(x.rule_id == "R10_week_max" for x in check(PLAN + [extra], Signals(), dataclasses.replace(
        ctx, week_max=None), plan_before=PLAN).violations)


def test_coach_proposal_over_the_cap_is_blocked_then_fallback(make_deps):
    from coach.llm import MockLLM
    from tests.test_graph import run
    week = sum(s.duration_min for s in PLAN if s.intensity != "rest")

    class CappedRepo(FakeRepo):
        def get(self, athlete_id):
            return dataclasses.replace(super().get(athlete_id), week_max=WeekMax(minutes=week + 10))
    extra = PlannedSession(session_id="x1", day=d(2), intensity="z1", duration_min=45, planned_load=20)
    llm = MockLLM(handlers={
        "extract": lambda u: Signals(fatigue_1_5=2),
        "coach": lambda u: CoachDecision(changes=[PlanChange(action="add", session=extra, reason="volume")],
                                         message_to_athlete="J'ajoute un footing mercredi."),
    })
    deps = make_deps(llm)
    deps.repo = CappedRepo()
    (out,) = run(deps, "Je me sens en forme, tu peux ajouter un footing ?")
    assert llm.calls == ["extract", "coach", "coach"]
    assert "plan tel quel" in out["reply"] and "volume maximum" in out["reply"]


# --------------------------------------------------------------------------- parcours complet
def _onboard(c, volume_answer: str):
    c.post("/api/strava/connect")
    chat(c, "La VVX 85 km")
    chat(c, "Oui, c'est ça")
    r = chat(c, "5 jours. Repos le lundi. Muscu en salle.")
    assert r["quick"] == ["3 h", "5 h", "8 h", "Je ne sais pas"] and "volume maximum" in r["messages"][-1]["text"]
    return chat(c, volume_answer)


def _pending_weeks(c, wm):
    pend = [PlannedSession.model_validate(x) for x in c.pilot.store.get("pending_onboarding_plan")["sessions"]]
    return _week_totals(pend, wm)


def test_onboarding_answer_is_stored_and_the_plan_stays_under_it(tmp_path):
    c = make(tmp_path, trail_runner())
    r = _onboard(c, "4 h max, pas plus de 40 km et 1500 m D+")
    prop = r["messages"][-1]["text"]
    assert "volume maximum : 4 h, 40 km et 1500 m D+ par semaine" in prop
    pr = c.service.prefs()
    assert (pr.max_week_min, pr.max_week_km, pr.max_week_dplus, pr.max_week_inferred) == (240, 40, 1500, False)
    wm = c.pilot.repo.week_max()
    for t in _pending_weeks(c, wm).values():
        assert t[0] <= 240 and t[1] <= 40 * TOL and t[2] <= 1500 * TOL
    # API des réglages
    v = c.get("/api/week-max").json()
    assert v["minutes"] == 240 and v["km"] == 40 and v["dplus"] == 1500 and "4 h" in v["text"]
    # le coach le sait
    chat(c, "Ça me va")
    summary = c.pilot.repo.get("x").profile_summary
    assert "Volume maximum que l'athlète peut faire : 4 h, 40 km et 1500 m D+ par semaine" in summary
    assert "Volume maximum : 4 h" in " | ".join(c.get("/api/state").json()["memory"]["commitments"])


def test_onboarding_i_dont_know_infers_and_says_so(tmp_path):
    c = make(tmp_path, trail_runner())
    r = _onboard(c, "Je ne sais pas")
    prop = r["messages"][-1]["text"]
    pr = c.service.prefs()
    assert pr.max_week_inferred and pr.max_week_min and "volume maximum estimé à" in prop
    assert "Carrière › Réglages" in prop
    assert c.get("/api/week-max").json()["inferred"]


def test_chat_correction_updates_the_cap_and_replans(tmp_path):
    c = make(tmp_path, trail_runner())
    _onboard(c, "Pas de limite")
    assert c.service.prefs().max_week_min is None
    chat(c, "Ça me va")
    r = chat(c, "En fait je peux monter à 4 h par semaine maximum")
    assert c.service.prefs().max_week_min == 240
    assert r["quick"] == ["Ça me va", "Garder l'ancien plan"] and "volume maximum : 4 h par semaine" in r["messages"][-1]["text"]
    for t in _pending_weeks(c, c.pilot.repo.week_max()).values():
        assert t[0] <= 240
    r = chat(c, "Ça me va")
    assert not r["pending"]


def test_settings_update_replans_only_when_the_plan_goes_over(tmp_path):
    c = make(tmp_path, trail_runner())
    _onboard(c, "Pas de limite")
    chat(c, "Ça me va")
    r = c.post("/api/week-max", json={"hours": 20}).json()
    assert r["ok"] and not r["replan"] and "20 h par semaine" in r["text"]
    r = c.post("/api/week-max", json={"hours": 3.5, "km": None, "dplus": 1000}).json()
    assert r["replan"] and r["quick"] == ["Ça me va", "Garder l'ancien plan"]
    v = c.get("/api/week-max").json()
    assert v["minutes"] == 210 and v["dplus"] == 1000 and v["km"] is None
    for t in _pending_weeks(c, c.pilot.repo.week_max()).values():
        assert t[0] <= 210 and t[2] <= 1000 * TOL
    r = c.post("/api/week-max", json={}).json()                         # tout vide : plus de plafond
    assert c.service.prefs().max_week_min is None and r["ok"]
    assert c.post("/api/week-max", json={"hours": 99}).status_code == 422


def test_existing_account_without_cap_keeps_todays_behaviour_and_is_asked_once(tmp_path):
    c = make(tmp_path, trail_runner())
    _onboard(c, "Pas de limite")
    chat(c, "Ça me va")
    assert c.pilot.repo.week_max() is None
    s1 = c.pilot.repo.get("x").profile_summary
    assert "jamais demandé" in s1
    chat(c, "j'ai bien dormi")
    assert "jamais demandé" not in c.pilot.repo.get("x").profile_summary


# --------------------------------------------------------------------------- déplacement par l'athlète
def test_athlete_move_over_the_cap_is_a_warning_not_a_refusal(tmp_path):
    from tests.test_pilot import TODAY as T0
    from tests.test_v037 import planned, ready
    c = ready(tmp_path)
    plan = c.pilot.store.plan()
    window = [s for s in plan if 0 <= (s.day - T0).days < 7 and s.intensity != "rest" and not s.is_race]
    mins = sum(s.duration_min for s in window)
    moved = next(s for s in plan if s.session_id == "coach-2026-10-07")
    assert moved.duration_min > 0.1 * mins
    c.pilot.store.set("prefs", Prefs(max_week_min=mins + 5).model_dump(mode="json"))
    r = c.post("/api/sessions/coach-2026-10-07/move", json={"day": "2026-10-05"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["moved"] and planned(c)["coach-2026-10-07"].day == date(2026, 10, 5)
    assert any(a.get("rule") == "R10_week_max" and a["would_block"] for a in b["alerts"])
    assert any("volume maximum" in w for w in b["warnings"])
    assert c.service.wait_coach(30)
