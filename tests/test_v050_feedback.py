"""v0.50 — retours du propriétaire : durée cohérente avec le texte, FC cible, « Mes zones », premier jour de la
semaine, récap de la semaine."""
import dataclasses
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from coach.diagnosis import diagnose
from coach.hr_zones import HrZones, coach_brief, estimate, resolve, strava_lthr, stream_stats, target
from coach.onboarding import CLUB_TEXT, Prefs, Profile, initial_block, parse_profile, parse_turn, parse_week_start
from coach.pilot.planner import TaperInputs, taper_plan
from coach.pilot.runtime import build_pilot
from coach.pilot.store import PROFILES_KEY, Activity
from coach.schemas import PlanChange, PlannedSession
from coach.session_content import (
    Workout,
    build,
    check,
    fit,
    harmonize,
    parse,
    resize,
    resize_session,
    short_reps,
)
from coach.web.app import create_app
from coach.web.push import Notifier
from coach.web.service import CoachService
from coach.week_max import WeekMax, fit_plan, fit_week, from_prefs, totals
from coach.weekly_recap import RecapInput, due_week
from coach.weekly_recap import build as build_recap
from tests.fakes_google import Svc
from tests.test_onboarding import TODAY, chat, make, trail_runner
from tests.test_pilot import ST, fill_history
from tests.test_v048_partner import TODAY as T2
from tests.test_v048_partner import club_runner

CODE, SECRET = "pas-de-code-faible", b"s" * 32
OWNER_BUG = "20 min d'échauffement, 3 × 8 min, récup 2 min à 4:00/km, 10 min de retour au calme."


def _profile(**kw):
    base = dict(first_name="Alex", goal_name="Volvic Volcanic Experience 85 km", goal_date=date(2027, 1, 16),
                goal_distance_km=85, goal_dplus_m=2750, days_per_week=5, long_day=6)
    return Profile(**{**base, **kw})


def _inconsistent(sessions) -> list[str]:
    return [f"{s.day} {s.intensity} : {c}" for s in sessions if (c := check(s.description, s.duration_min))]


# =========================================================================== 1. durée et texte cohérents
def test_parse_reads_every_generated_format():
    w = parse(OWNER_BUG)
    assert (w.warmup, w.reps, w.on_s, w.off_s, w.cooldown) == (20, 3, 480, 120, 10) and w.complete
    assert w.minutes() == 58
    w = parse("20 min d'échauffement, 2 × (8 × 30 s rapide / 30 s trot) à 3:30/km, 3 min entre les blocs, "
              "10 min de retour au calme.")
    assert (w.sets, w.reps, w.short, w.set_rest_s) == (2, 8, True, 180) and w.minutes() == pytest.approx(48)
    w = parse("20 min d'échauffement, 10 × 30/30 à 3:18/km, 10 min de retour au calme.")
    assert w.reps == 10 and w.short and w.minutes() == pytest.approx(39.5)
    w = parse("Échauffement 20 min, 3 x 10 min au seuil (récup 3 min), retour au calme")    # ancien affûtage
    assert w.warmup == 20 and w.reps == 3 and w.off_s == 180 and not w.complete
    w = parse("Durabilité en descente : 20 min d'échauffement, 6 × 3-4 min de descente, 10 min de retour au calme.")
    assert w.on_fixed and w.on_s == 210
    long_ = parse("Sortie longue en endurance, dont 2 × 10 min à allure marathon dans le dernier tiers")
    assert long_ and not long_.complete and long_.main_min() == 20
    assert parse("Footing en endurance + 6 lignes droites de 20 s") is None
    assert parse(CLUB_TEXT) is None
    assert short_reps("20 min d'échauffement, 10 × 30/30, 10 min de retour au calme.")
    assert not short_reps(OWNER_BUG)


def test_owner_bug_is_detected_and_the_text_is_rewritten_for_the_duration():
    assert check(OWNER_BUG, 40) and not check(OWNER_BUG, 60)
    s = PlannedSession(session_id="x", day=date(2026, 11, 10), intensity="threshold", duration_min=40,
                       planned_load=40, description=OWNER_BUG)
    h = harmonize(s)
    assert h.duration_min == 40 and not check(h.description, 40)
    assert "2 × 8 min" in h.description and "à 4:00/km" in h.description          # moins de répétitions, même allure
    for minutes in range(30, 100, 5):                                               # raccourcir et allonger
        txt = resize(OWNER_BUG, minutes)
        assert abs(parse(txt).minutes() - minutes) <= 5, (minutes, txt)
        assert parse(txt).minutes() <= minutes + 0.01 or minutes >= 58             # jamais au-delà d'un plafond
    r = resize_session(s.model_copy(update={"duration_min": 60}), 45)
    assert r.duration_min == 45 and not check(r.description, 45) and r.planned_load < 60


def test_builder_derives_text_and_duration_from_one_structure():
    txt, mn = build(Workout(20, 3, 480, 120, cooldown=10, pace=" à 4:00/km"))
    assert mn == 60 and txt == "20 min d'échauffement, 3 × 8 min à 4:00/km, récup 2 min, 10 min de retour au calme."
    txt, mn = build(Workout(20, 3, 480, 120, cooldown=10), target=75)
    assert mn == 75 and not check(txt, 75)
    w = fit(Workout(20, 8, 30, 30, sets=2, set_rest_s=180, cooldown=10, short=True), 40)
    assert w.minutes() <= 40 and w.minutes() >= 35


def _scenarios():
    d1, d2, d3 = diagnose(trail_runner(), TODAY), diagnose(trail_runner(big_effort_days_ago=12), TODAY), \
        diagnose(club_runner(), T2)
    prefs = [Prefs(), Prefs(likes_volume=True), Prefs(double_threshold=True), Prefs(max_week_min=150),
             Prefs(max_week_min=240, max_week_dplus=800), Prefs(weekend_max_min=45), Prefs(week_start=6),
             Prefs(quality_days=[1], quality_minutes=75), Prefs(quality_days=[1], club_quality=True),
             Prefs(volume_factor=0.6, speed_weeks=6)]
    for d, today in ((d1, TODAY), (d2, TODAY), (d3, T2)):
        for pr in prefs:
            for p in (_profile(), _profile(days_per_week=3, long_day=5)):
                yield d, p, today + timedelta(days=1), pr


def test_every_generated_plan_is_consistent_first_plans_caps_specific_phase():
    n, spec = 0, 0
    for d, p, start, pr in _scenarios():
        b = initial_block(d, p, start, pr)
        assert not _inconsistent(b.sessions), (pr, _inconsistent(b.sessions)[:3])
        n += len(b.sessions)
        spec += sum(1 for w in b.weeks if w.get("specific"))
    assert n > 1500 and spec > 0                                         # phase spécifique comprise


def test_taper_and_week_max_trimming_stay_consistent():
    for load in (150, 300, 500, 800):
        for spw in (3, 6):
            plan = taper_plan(date(2026, 11, 22), TaperInputs(load, spw), date(2026, 11, 3), "Course", 600)
            assert not _inconsistent(plan), _inconsistent(plan)
    q = PlannedSession(session_id="q", day=date(2026, 11, 10), intensity="threshold", duration_min=60,
                       planned_load=60, description=OWNER_BUG)
    easy = [PlannedSession(session_id="e", day=date(2026, 11, 12), intensity="z2", duration_min=30,
                           planned_load=30, description="Footing")]
    out, trimmed = fit_week([q] + easy, WeekMax(minutes=80))         # 2 jours : seule la qualité peut raccourcir
    assert trimmed and totals(out, WeekMax(minutes=80))[0] <= 80
    q2 = next(s for s in out if s.intensity == "threshold")
    assert q2.duration_min == 50 and not check(q2.description, 50) and "2 × 8 min" in q2.description


def test_replans_and_coach_changes_are_consistent(tmp_path):
    c = make(tmp_path, trail_runner())
    c.post("/api/strava/connect")
    chat(c, "La VVX 85 km")
    chat(c, "Oui, c'est ça")
    chat(c, "4 jours. Repos le lundi.")
    chat(c, "3 h max")                                                   # plafond bas : beaucoup de raccourcis
    pend = [PlannedSession.model_validate(x) for x in c.pilot.store.get("pending_onboarding_plan")["sessions"]]
    assert pend and not _inconsistent(pend)
    chat(c, "Ça me va")
    r = chat(c, "Je veux l'intensité le mardi et la longue le vendredi")    # nouveau plan (préférences)
    assert r["quick"] == ["Ça me va", "Garder l'ancien plan"]
    pend = [PlannedSession.model_validate(x) for x in c.pilot.store.get("pending_onboarding_plan")["sessions"]]
    assert not _inconsistent(pend)
    chat(c, "Ça me va")
    stored = c.pilot.store.plan()
    assert stored and not _inconsistent(stored)
    # le coach (ou un outil) raccourcit une séance sans réécrire le texte : le stockage harmonise
    q = next(s for s in stored if s.intensity in ("threshold", "vo2", "hills", "tempo") and parse(s.description))
    short = q.model_copy(update={"duration_min": 35})
    c.pilot.deps.calendar.apply("pilote", [PlanChange(action="modify", session=short, reason="fatigue")])
    after = {s.session_id: s for s in c.pilot.store.plan()}[q.session_id]
    assert after.duration_min == 35 and not check(after.description, 35)
    # la carte affiche le texte de la séance (et non l'ancien détail figé à l'adoption)
    v = c.service.session_view(after, c.service._meta(), {})
    assert v["detail"] == after.description


def _web(tmp_path, clock=None, llm=None, notifier=True):
    clock = clock or {"d": date(2026, 9, 30)}
    svc = Svc()
    p = build_pilot(home=tmp_path, calendar_service=svc, today_fn=lambda: clock["d"],
                    settings=dataclasses.replace(ST), llm=llm)
    fill_history(p.store)
    from tests.test_web import FakeSender
    sender = FakeSender()
    service = CoachService(p, Notifier(p.store, sender) if notifier else None)
    c = TestClient(create_app(service, passcode=CODE, secret=SECRET, public_key="PUBKEY", background=False))
    assert c.post("/api/login", json={"passcode": CODE}).status_code == 200
    c.pilot, c.service, c.sender, c.gsvc, c.clock = p, service, sender, svc, clock
    return c


def test_stored_plans_are_repaired_at_startup_without_moving_dates(tmp_path):
    c = _web(tmp_path)
    c.pilot.write_plan(c.pilot.repo.propose_taper(date(2026, 9, 30))[0])
    store = c.pilot.store
    bad = PlannedSession(session_id="coach-2026-10-06", day=date(2026, 10, 6), intensity="threshold",
                         duration_min=40, planned_load=45, description=OWNER_BUG)
    store.db.execute("INSERT OR REPLACE INTO plan VALUES (?,?,?)", (bad.session_id, bad.day.isoformat(),
                                                                    bad.model_dump_json()))
    store.db.commit()                                                    # ligne écrite par une ancienne version
    store.set("session_meta", {bad.session_id: {"title": "Seuil", "detail": OWNER_BUG}})
    store.set("repairs_v050_durations", None)
    svc2 = CoachService(c.pilot, None)                                    # redémarrage
    fixed = {s.session_id: s for s in store.plan()}[bad.session_id]
    assert fixed.day == bad.day and fixed.duration_min == 40 and not check(fixed.description, 40)
    assert store.get("session_meta")[bad.session_id]["detail"] == fixed.description
    assert store.get("repairs_v050_durations")["n"] >= 1
    ev = [e for e in c.gsvc.data.values() if e["extendedProperties"]["private"]["coach_session_id"] == bad.session_id]
    assert ev and fixed.description in ev[0]["description"]               # l'agenda suit
    assert not _inconsistent(store.plan())
    assert svc2.repair_plan_texts() == 0                                  # idempotent


# =========================================================================== 2. zones et FC cible
def test_zone_resolution_priority_and_sources():
    base = (199, 40, None)
    z = resolve(base, {}, None, {})
    assert (z.hr_max, z.lthr, z.src["lthr"], z.src["hr_max"]) == (199, 185, "reglage", "reglage")
    z = resolve(base, {}, None, {"hr_max": 190, "lthr": 172})
    assert (z.hr_max, z.lthr, z.src["hr_max"], z.src["lthr"]) == (190, 172, "estimee", "estimee")
    z = resolve(base, {}, None, {"hr_max": 190})                        # pas d'effort de référence : 93 % du max
    assert z.lthr == round(0.93 * 190) and z.src["lthr"] == "estimee"
    strava = {"zones": [{"min": 0, "max": 130}, {"min": 130, "max": 150}, {"min": 150, "max": 165},
                        {"min": 165, "max": 175}, {"min": 175, "max": -1}]}
    assert strava_lthr(strava["zones"]) == 175
    z = resolve(base, {}, strava, {"hr_max": 190, "lthr": 172})
    assert z.lthr == 175 and z.src["lthr"] == "strava"
    z = resolve(base, {"lthr": 168, "hr_max": 186, "hr_rest": 45}, strava, {"hr_max": 190, "lthr": 172})
    assert (z.hr_max, z.hr_rest, z.lthr) == (186, 45, 168) and set(z.src.values()) == {"saisie"}
    zs = z.zones()
    assert [x["zone"] for x in zs] == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    assert zs[0]["lo"] is None and zs[0]["hi"] == round(168 * 0.85) - 1 and zs[3]["lo"] == round(168 * 0.95)
    assert zs[4]["hi"] is None and zs[4]["lo"] == 168


def test_stream_stats_and_estimates_from_clean_streams():
    t = list(range(0, 4000))
    hr = [130 + min(40, i / 60) for i in t]                              # monte jusqu'à 170
    hr[1000] = 250                                                       # artefact ignoré
    st = stream_stats(t, hr)
    assert 169 <= st["hr_peak"] <= 171 and st["hr_best30"] > 160 and st["hr_best60"] > 155
    assert stream_stats(t, [None] * len(t)) == {}
    prof = {"20261101T0700": {"hr_peak": 186, "hr_best30": 170, "hr_best60": 166},
            "20261020T0700": {"hr_peak": 180, "hr_best30": 160},
            "20260901T0700": {"hr_peak": 178}}
    e = estimate(prof, date(2026, 11, 8))
    assert e["hr_max"] == 186 and e["lthr"] == round(max(166, 0.98 * 170)) and e["n"] == 3
    assert estimate(dict(list(prof.items())[:2]), date(2026, 11, 8)) == {}          # trop peu de sorties
    assert estimate({"20240101T0700": {"hr_peak": 190}} | prof, date(2026, 11, 8))["hr_max"] == 186   # trop vieux


def test_targets_per_session_type():
    z = HrZones(190, 45, 170, {}, {"seuil": "4:03", "vma": "3:18", "endurance": "5:20–5:50", "marathon": "4:25"})
    t = target("z2", "Footing en endurance", z)
    assert t["hr"] == [136, 153] and t["lead"] == "hr" and t["pace"] == "5:20–5:50/km" and "Z1-Z2" in t["text"]
    t = target("threshold", OWNER_BUG, z)
    assert t["hr"] == [162, 170] and t["pace"] == "4:00/km" and t["lead"] == "hr"   # allure de la séance
    t = target("vo2", "20 min d'échauffement, 10 × 30/30 à 3:18/km, 10 min de retour au calme.", z)
    assert t["lead"] == "pace" and t["short_reps"] and t["text"].startswith("Cible : 3:18/km · effort 9/10")
    assert "en retard" in t["note"]
    t = target("hills", "Côtes : 20 min d'échauffement, 5 × 8 min de montée, récup 4 min, 10 min de retour au calme.", z)
    assert t["lead"] == "effort" and t["pace"] is None and t["text"].startswith("Cible : effort 8/10")
    t = target("vo2", CLUB_TEXT, z)                                      # séance du club : effort d'abord
    assert t["lead"] == "effort" and t["pace"] is None and "club" in t["note"]
    t = target("long", "Sortie longue", z)
    assert t["zone"] == "Z2" and "bpm" in t["text"]
    assert target("rest", "Renfo", z) is None and target("race", "", z) is None
    brief = coach_brief(z.as_dict())
    assert "seuil 170" in brief and "Z4 seuil 162-170 bpm (4:03/km)" in brief and "en retard" in brief


def test_zones_api_overrides_update_the_load_engine(tmp_path):
    c = _web(tmp_path)
    v = c.get("/api/zones").json()
    assert v["hr_max"]["value"] == 199 and v["lthr"]["source"] == "réglage" and len(v["zones"]) == 5
    assert v["model"].startswith("Friel") and any(t["intensity"] == "vo2_short" for t in v["targets"])
    r = c.post("/api/zones", json={"hr_max": 188, "lthr": 171, "hr_rest": 47})
    assert r.status_code == 200
    v = r.json()
    assert v["lthr"]["value"] == 171 and v["lthr"]["source"] == "saisie" and v["hr_max"]["source"] == "saisie"
    st = c.pilot.settings
    assert (st.hr_max, st.lthr, st.hr_rest, st.threshold_hr) == (188, 171, 47, 171)   # moteur de charge
    from coach.session_type import profile_from_streams
    prof = profile_from_streams(list(range(0, 1200, 5)), [170] * 240, lthr=st.threshold_hr)
    assert prof["zones"][3] > 15                                          # 170 bpm = Z4 avec un seuil à 171
    assert c.post("/api/zones", json={"lthr": 195}).status_code == 422     # seuil au-dessus du max
    assert c.post("/api/zones", json={"hr_max": 300}).status_code == 422
    v = c.post("/api/zones", json={"lthr": None}).json()                  # retour à l'automatique
    assert v["lthr"]["source"] != "saisie" and v["hr_max"]["value"] == 188
    # estimation d'après les flux : valeurs « estimées » quand rien n'est saisi
    c.post("/api/zones", json={"hr_max": None, "hr_rest": None})
    c.pilot.store.set(PROFILES_KEY, {f"202609{d:02d}T0700": {"hr_peak": 183, "hr_best60": 165} for d in (1, 5, 9)})
    v = c.get("/api/zones").json()
    assert v["hr_max"] == {**v["hr_max"], "value": 183, "source": "estimée"} and v["lthr"]["value"] == 165
    assert c.pilot.settings.hr_max == 183


class _ApiStrava:
    kind = "api"

    def __init__(self, scope):
        self.scope, self.calls = scope, 0

    def scopes(self):
        return set(self.scope.split(","))

    def zones(self):
        self.calls += 1
        return {"heart_rate": {"custom_zones": True, "zones": [{"min": 0, "max": 128}, {"min": 128, "max": 148},
                                                               {"min": 148, "max": 160}, {"min": 160, "max": 172},
                                                               {"min": 172, "max": -1}]}}


def test_strava_zones_need_profile_scope_and_feed_the_threshold(tmp_path):
    from coach.pilot.strava_api import SCOPE, ZONES_SCOPE
    assert ZONES_SCOPE in SCOPE.split(",")
    c = _web(tmp_path)
    c.pilot.strava = _ApiStrava("read,activity:read_all")                # connecté avant la v0.50
    v = c.get("/api/zones").json()["strava"]
    assert v["needs_reconnect"] and not v["scope_ok"]
    assert not c.service.refresh_strava_zones() and c.pilot.strava.calls == 0
    c.pilot.strava = _ApiStrava("read,activity:read_all,profile:read_all")
    assert c.service.refresh_strava_zones() and not c.service.refresh_strava_zones()   # une fois par jour
    c.service.apply_hr_settings()
    v = c.get("/api/zones").json()
    assert v["lthr"]["value"] == 172 and v["lthr"]["source"] == "Strava" and not v["strava"]["needs_reconnect"]
    assert v["strava"]["zones"][4]["min"] == 172 and c.pilot.settings.lthr == 172


def test_targets_on_cards_calendar_and_coach_context(tmp_path):
    c = _web(tmp_path)
    c.post("/api/zones", json={"lthr": 170, "hr_max": 190})
    c.pilot.write_plan(c.pilot.repo.propose_taper(date(2026, 9, 30))[0])
    week = c.get("/api/week", params={"start": "2026-10-05"}).json()
    sessions = [s for d in week["days"] for s in d["sessions"]]
    runs = [s for s in sessions if s["intensity"] in ("z1", "z2", "tempo", "threshold", "long")]
    assert runs and all(s["target"] and "bpm" in s["target"]["text"] for s in runs)
    thr = next(s for s in sessions if s["intensity"] == "tempo")
    assert thr["target"]["zone"] == "Z3" and thr["target"]["hr"] == [153, 162]
    descs = [e["description"] for e in c.gsvc.data.values() if "Tempo" in e["summary"]]
    assert descs and all("Cible : FC 153-162 bpm (Z3)" in d for d in descs)
    assert "Fréquences cardiaques de l'athlète" in c.pilot.repo.get("x").profile_summary
    assert "seuil 170 (saisie)" in c.pilot.repo.get("x").profile_summary


# =========================================================================== 3. premier jour de la semaine
def test_week_start_is_asked_and_parsed():
    assert parse_week_start("ma semaine commence le dimanche") == 6
    assert parse_week_start("Semaine du dimanche au samedi") == 6
    assert parse_week_start("je compte du samedi au vendredi") == 5
    assert parse_week_start("5 jours, repos le lundi") is None
    assert parse_profile("5 jours. Repos le lundi. Ma semaine commence le dimanche.", TODAY).week_start == 6
    t = parse_turn("En fait ma semaine commence le dimanche", TODAY)
    assert t.intent == "modify" and t.week_start == 6
    from coach.onboarding import profile_questions
    assert "Ta semaine commence quel jour ? Lundi par défaut" in profile_questions(diagnose(trail_runner(), TODAY))


def test_plan_weeks_and_caps_follow_the_athlete_week():
    d = diagnose(trail_runner(), TODAY)
    p = _profile()
    b = initial_block(d, p, TODAY + timedelta(days=1), Prefs(week_start=6, max_week_min=240))
    assert all(date.fromisoformat(w["start"]).weekday() == 6 for w in b.weeks)
    assert {s.day.weekday() for s in b.sessions if s.intensity == "long"} == {6}      # ses jours restent ses jours
    wm = from_prefs(Prefs(week_start=6, max_week_min=240), d)
    assert wm.week_start == 6
    for w in b.weeks:
        ws = date.fromisoformat(w["start"])
        wk = [s for s in b.sessions if ws <= s.day < ws + timedelta(days=7)]
        assert totals(wk, wm)[0] <= 240                                   # plafond sur dimanche-samedi
    s1 = [PlannedSession(session_id=f"s{i}", day=date(2026, 11, 7) + timedelta(days=i), intensity="z2",
                         duration_min=60, planned_load=40, description="Footing") for i in range(4)]
    # sam. 7, dim. 8, lun. 9, mar. 10 (1 h chacun, plafond 2 h) : lundi-dimanche = 2 h + 2 h, rien à couper ;
    # dimanche-samedi = 1 h + 3 h : la semaine du dimanche 8 est raccourcie
    _, touched_mon = fit_plan(s1, WeekMax(minutes=120), date(2026, 11, 7))
    out, touched_sun = fit_plan(s1, WeekMax(minutes=120, week_start=6), date(2026, 11, 7))
    assert touched_mon == [] and touched_sun == [date(2026, 11, 8)]
    assert sum(s.duration_min for s in out if s.day >= date(2026, 11, 8)) <= 120


def test_week_view_and_settings_follow_the_week_start(tmp_path):
    c = _web(tmp_path)
    c.pilot.write_plan(c.pilot.repo.propose_taper(date(2026, 9, 30))[0])
    w = c.get("/api/week").json()
    assert date.fromisoformat(w["start"]).weekday() == 0 and w["week_start"] == 0
    assert c.post("/api/week-start", json={"day": 6}).json()["label"] == "dimanche"
    assert c.get("/api/week-start").json() == {"day": 6, "label": "dimanche", "end_label": "samedi",
                                               "days": c.get("/api/week-start").json()["days"]}
    w = c.get("/api/week").json()
    assert w["start"] == "2026-09-27" and [d["date"] for d in w["days"]][-1] == "2026-10-03"
    w2 = c.get("/api/week", params={"start": "2026-10-07"}).json()
    assert w2["start"] == "2026-10-04"
    assert c.post("/api/week-start", json={"day": 7}).status_code == 422


def test_onboarding_answer_sets_the_week_start(tmp_path):
    c = make(tmp_path, trail_runner())
    c.post("/api/strava/connect")
    chat(c, "La VVX 85 km")
    r = chat(c, "Oui, c'est ça")
    assert "Ta semaine commence quel jour" in r["messages"][-1]["text"]
    chat(c, "5 jours. Repos le lundi. Ma semaine commence le dimanche.")
    r = chat(c, "6 h max")
    prop = r["messages"][-1]["text"]
    assert c.service.prefs().week_start == 6 and "semaine du dimanche au samedi" in prop
    weeks = c.pilot.store.get("pending_onboarding_plan")["weeks"]
    assert all(date.fromisoformat(k).weekday() == 6 for k in weeks)
    chat(c, "Ça me va")
    assert "Semaine du dimanche au samedi" in " | ".join(c.get("/api/state").json()["memory"]["commitments"])
    w = c.get("/api/week").json()
    assert date.fromisoformat(w["start"]).weekday() == 6 and w["phase"]


# =========================================================================== 4. récap de la semaine
def _act(d: date, minutes: float, km: float, dplus: float = 0, suspect=False):
    a = Activity(datetime(d.year, d.month, d.day, 7, tzinfo=UTC), d, "Course", minutes * 60, km * 1000, dplus, 150,
                 minutes, "estimate", name="Sortie", moving_s=minutes * 60)
    a.suspect = suspect
    return a


def _ps(sid, d, inten, mn, load=50, desc=""):
    return PlannedSession(session_id=sid, day=d, intensity=inten, duration_min=mn, planned_load=load, description=desc)


def test_recap_content_is_deterministic_and_complete():
    w0 = date(2026, 11, 2)                                                # lundi
    plan = [_ps("a", w0 + timedelta(days=1), "threshold", 60), _ps("b", w0 + timedelta(days=3), "vo2", 55),
            _ps("c", w0 + timedelta(days=4), "z2", 45), _ps("d", w0 + timedelta(days=6), "long", 120)]
    nxt = [_ps("n1", date(2026, 11, 9), "long", 110), _ps("n2", date(2026, 11, 10), "threshold", 60),
           _ps("n3", date(2026, 11, 11), "vo2", 55), _ps("n4", date(2026, 11, 13), "z2", 45)]
    x = RecapInput(week0=w0, today=date(2026, 11, 8), plan=plan, next_plan=nxt,
                   titles={"a": "Seuil", "b": "VMA courte", "c": "Footing", "d": "Sortie longue", "n1": "Sortie longue",
                           "n2": "Seuil", "n3": "VMA"},
                   feedback={"a": {"status": "done"}, "d": {"status": "modified"}},
                   acts=[_act(w0 + timedelta(days=1), 62, 12), _act(w0 + timedelta(days=6), 100, 18, 600),
                         _act(w0 + timedelta(days=5), 900, 5, suspect=True)],
                   last_sync="2026-11-08T19:42", now=datetime(2026, 11, 8, 20, 30),
                   wm=WeekMax(minutes=240), xp_week=21, trophies=["Régulier 4 semaines"],
                   levels=[{"level": 8, "rank": "Coureur confirmé"}],
                   event={"label": "Noël", "days_left": 16, "xp": 50}, long_day=6,
                   races=[("Trail des Lavandes", date(2026, 11, 15))], first_name="Alex")
    r = build_recap(x)
    t = r["text"]
    assert r["week_start"] == "2026-11-02" and t.startswith("📋 Alex, voici le récap de ta semaine du lun. 02/11")
    assert "Dernière synchro Strava : dim. 08/11 à 19:42." in t and "3 activité(s) importée(s)" in t
    assert "2 séance(s) sans sortie liée : à valider ou à lier ? (jeu. 05/11 VMA courte, ven. 06/11 Footing)" in t
    assert "1 sortie(s) à la durée douteuse" in t
    assert "Seuil (mar. 03/11, 1 h) : faite ✓" in t and "VMA courte (jeu. 05/11, 55 min) : pas de retour" in t
    assert "Sortie longue (dim. 08/11, 2 h) : modifiée" in t
    assert "Total : 2 h 40 de course, 30 km, 600 m D+ (prévu 4 h 40" in t and "ton maximum : 4 h" in t
    assert "+21 XP" in t and "Régulier 4 semaines" in t and "Niveau 8 atteint" in t and "Noël (dans 16 jours" in t
    assert "Semaine prochaine (du lun. 09/11 au dim. 15/11)" in t
    assert "deux jours durs d'affilée : lun. 09/11 et mar. 10/11" in t
    assert "ta sortie longue est lundi (1 h 50), ça te va ?" in t
    assert "Trail des Lavandes dimanche 15/11" in t and "au-delà de ton volume maximum" in t
    assert r["data"]["keys"] == {"done": 1, "modified": 1, "skipped": 0, "missing": 1}
    assert r["data"]["unlinked"] == ["b", "c"]


def test_due_week_evening_of_last_day_and_morning_catch_up():
    assert due_week(date(2026, 11, 8), 20, 0) == (date(2026, 11, 2), False)       # dimanche 20 h
    assert due_week(date(2026, 11, 8), 19, 0) is None
    assert due_week(date(2026, 11, 9), 8, 0) == (date(2026, 11, 2), True)         # rattrapage lundi matin
    assert due_week(date(2026, 11, 9), 13, 0) is None
    assert due_week(date(2026, 11, 7), 21, 6) == (date(2026, 11, 1), False)       # semaine dimanche-samedi
    assert due_week(date(2026, 11, 8), 21, 6) is None


def test_tick_posts_the_recap_once_with_a_push_and_never_during_onboarding(tmp_path):
    clock = {"d": date(2026, 9, 30)}
    c = _web(tmp_path, clock)
    c.pilot.write_plan(c.pilot.repo.propose_taper(date(2026, 9, 30))[0])
    c.pilot.store.add_push_sub({"endpoint": "https://push/1", "keys": {}})
    clock["d"] = date(2026, 10, 4)                                        # dimanche
    assert "recap" not in c.service.tick(datetime(2026, 10, 4, 19, 59))
    assert "recap" in c.service.tick(datetime(2026, 10, 4, 20, 5))
    msgs = [m for m in c.get("/api/chat").json()["messages"] if (m["meta"] or {}).get("recap")]
    assert len(msgs) == 1 and "récap de ta semaine du lun. 28/09 au dim. 04/10" in msgs[0]["text"]
    assert "Semaine prochaine (du lun. 05/10 au dim. 11/10)" in msgs[0]["text"]
    assert any(p[1]["title"] == "Récap de la semaine" and p[1]["tag"] == "recap" for p in c.sender.sent)
    assert "recap" not in c.service.tick(datetime(2026, 10, 4, 21, 0))          # idempotent
    clock["d"] = date(2026, 10, 5)
    assert "recap" not in c.service.tick(datetime(2026, 10, 5, 8, 0))           # déjà posté : pas de rattrapage
    r = c.get("/api/recap").json()
    assert r["last"]["week_start"] == "2026-09-28" and r["last"]["sections"][0]["id"] == "sync"
    assert r["preview"]["week_start"] == "2026-10-05"
    assert c.get("/api/week").json()["recap"]["last"] == "2026-09-28"
    # pendant l'accueil : jamais
    (tmp_path / "ob").mkdir()
    c2 = make(tmp_path / "ob", trail_runner())
    c2.post("/api/strava/connect")
    clock2 = date(2026, 11, 1)                                           # dimanche
    c2.service.pilot.deps.today = lambda: clock2
    assert c2.service.maybe_weekly_recap(datetime(2026, 11, 1, 21, 0)) is None
    assert not (c2.pilot.store.get("weekly_recaps") or {})


def test_recap_follows_the_week_start_and_catches_up_the_next_morning(tmp_path):
    clock = {"d": date(2026, 9, 30)}
    c = _web(tmp_path, clock, notifier=False)
    c.pilot.write_plan(c.pilot.repo.propose_taper(date(2026, 9, 30))[0])
    c.post("/api/week-start", json={"day": 6})
    clock["d"] = date(2026, 10, 3)                                        # samedi = dernier jour
    m = c.service.maybe_weekly_recap(datetime(2026, 10, 3, 20, 30))
    assert m and "semaine du dim. 27/09 au sam. 03/10" in m["text"]
    clock["d"] = date(2026, 10, 11)                                       # dimanche matin : samedi soir manqué
    m = c.service.maybe_weekly_recap(datetime(2026, 10, 11, 9, 0))
    assert m and "semaine du dim. 04/10 au sam. 10/10" in m["text"]


class _SentenceLLM:
    """Faux LLM : une phrase d'encouragement (injecté par build_pilot)."""

    def __init__(self):
        self.usage_log, self.failures, self.calls = [], 0, []

    def structured(self, *, node, tier, system, user, schema):
        self.calls.append(node)
        return schema(sentence="Belle régularité cette semaine, garde ce rythme !")


def test_recap_adds_an_llm_sentence_only_within_budget(tmp_path):
    clock = {"d": date(2026, 9, 30)}
    llm = _SentenceLLM()
    c = _web(tmp_path, clock, llm=llm, notifier=False)
    c.pilot.write_plan(c.pilot.repo.propose_taper(date(2026, 9, 30))[0])
    clock["d"] = date(2026, 10, 4)
    m = c.service.maybe_weekly_recap(datetime(2026, 10, 4, 20, 30))
    assert m["text"].endswith("Belle régularité cette semaine, garde ce rythme !") and llm.calls == ["weekly_recap"]
    clock["d"] = date(2026, 10, 11)
    c.pilot.store.set("spent:2026-10-11", c.pilot.settings.daily_budget_eur)       # budget du jour épuisé
    m = c.service.maybe_weekly_recap(datetime(2026, 10, 11, 20, 30))
    assert m and "Belle régularité" not in m["text"] and llm.calls == ["weekly_recap"]
