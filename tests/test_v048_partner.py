"""v0.48 : retours d'une deuxième athlète (coureuse de club, accueil complet sur Android).

1. date de la course : le format exact d'un événement de plusieurs jours (VVX 21 km = samedi 8 mai 2027) ;
2. montre oubliée (6 km « en 24 h ») : ni ultra, ni trophée, ni bilan faussé, charge au temps en mouvement ;
3. premier plan à sa mesure (≤ 115 % de sa moyenne des 4 dernières semaines) ;
4. ses contraintes : club le mardi, longue le vendredi, bonus le week-end, renfo maison hors mardi.
"""
import dataclasses
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from coach.career import Facts, career
from coach.diagnosis import diagnose, is_run
from coach.onboarding import (
    Prefs,
    Profile,
    apply_profile_fields,
    initial_block,
    is_bonus,
    parse_profile,
    parse_turn,
    recent_week_avg,
)
from coach.pilot.runtime import build_pilot
from coach.pilot.store import Activity, PilotStore, effective_duration, sanitize
from coach.race_lookup import from_catalog, lookup
from coach.web.app import create_app
from coach.web.service import CoachService
from tests.test_onboarding import CODE, SECRET, FakeConnector, FakeStrava, chat
from tests.test_pilot import ST

TODAY = date(2026, 11, 4)                    # mercredi
PROFILE_TEXT = ("Je cours au club en trail (souvent du fractionné) le mardi. 1h15 et le vendredi seule souvent sur "
                "route. Quand je peux une sortie le weekend ou un soir en semaine (plus rare en semaine avec les "
                "enfants !) J'aimerais me mettre au renfo de manière régulière à la maison juste avec un tapis sans "
                "matériel. Je ne fais pas d'autres sports. La rando c'est que les vacances. Vacances de Noël c'est "
                "toujours compliqué mais j'espère garder minimum 2 sorties.")
LATER_TEXT = "L'idée est de tjrs garder la séance d'intensité du mardi, la longue le vendredi et le bonus le weekend"
GOAL_TEXT = "Volvic Volcanic Experience – Trail Patrimoine Chemin des Batignolles 21 km"


def run(day: date, km: float, minutes: float, name: str = "Footing", sport: str = "Course", dplus: float = 0,
        moving: float | None = -1, hour: int = 18) -> Activity:
    t = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    mv = minutes * 60 if moving == -1 else (moving * 60 if moving is not None else None)
    return Activity(start_utc=t, local_day=day, sport=sport, duration_s=minutes * 60, distance_m=km * 1000,
                    dplus_m=dplus, avg_hr=None, load=minutes * 0.9, load_source="estimate", name=name, moving_s=mv)


def club_runner(today: date = TODAY, watch_left_on: bool = True) -> list[Activity]:
    """Son historique : mardi club 1 h 15 (2 semaines sur 3), vendredi route ~45 min, un week-end sur quatre ;
    environ 1 h 40 par semaine. Plus une montre oubliée : 6 km « en 24 h »."""
    acts = []
    start = today - timedelta(days=16 * 7)
    for i in range(16 * 7):
        d = start + timedelta(days=i)
        w = i // 7
        if d.weekday() == 1 and w % 3 != 2:
            acts.append(run(d, 10, 75, "Fractionné club", "Trail", 150))
        elif d.weekday() == 4 and w % 5 != 4:
            acts.append(run(d, 7.5, 45, "Footing route"))
        elif d.weekday() == 6 and w % 4 == 1:
            acts.append(run(d, 6.5, 40, "Sortie du dimanche", "Trail", 120, hour=9))
    if watch_left_on:
        acts.append(run(today - timedelta(days=17), 6.1, 24 * 60, "Sortie du dimanche", moving=41, hour=9))
    return sorted(acts, key=lambda a: a.start_utc)


# ------------------------------------------------------------------------ 1. date de la course
def test_vvx_21_km_is_saturday_8_may_not_the_events_first_day():
    for name in (GOAL_TEXT, "VVX 21 km", "la VVX, le trail patrimoine", "Batignolles"):
        f = from_catalog(name, None, TODAY)
        assert f and not f.ambiguous, name
        assert f.date == date(2027, 5, 8) and f.date.weekday() == 5, name
        assert f.distance_km == 21.5 and f.dplus_m == 500 and f.start.startswith("8 h"), name
    assert from_catalog("VVX", 21, TODAY).date == date(2027, 5, 8)
    # la distance donnée ne tombe jamais sur un autre format
    assert from_catalog("VVX 85 km", None, TODAY).date == date(2027, 5, 7)
    assert from_catalog("VVX Volvic Volcanic Experience", 85, TODAY).dplus_m == 2750
    assert from_catalog("VVX pierre de lave", None, TODAY).date == date(2027, 5, 6)
    assert from_catalog("VVX 45 km", None, TODAY).name.endswith("L'Impluvium 43-46 km")
    assert from_catalog("Chaîne des Puys VVX", None, TODAY).start == "4 h"
    # l'événement seul : on demande le format, jamais le premier jour par défaut
    amb = from_catalog("VVX", None, TODAY)
    assert amb.ambiguous and len(amb.options) == 6
    assert lookup("VVX", None, TODAY).ambiguous
    # les autres courses du catalogue restent trouvées
    assert from_catalog("Trail des Vallées", None, TODAY).date == date(2027, 3, 20)


class _WebSearchSaysFirstDay:
    """Recherche web Claude qui renvoie le 1er jour de l'événement (le bug observé : « jeu. 6 mai »)."""

    def __init__(self):
        self.calls = 0
        outer = self

        class Messages:
            def create(self, **kw):
                outer.calls += 1
                from types import SimpleNamespace as NS
                txt = ('{"found": true, "name": "VVX Trail Patrimoine 21 km", "date": "2027-05-06", '
                       '"distance_km": 21, "dplus_m": 500, "kind": "trail", "url": "https://www.volvic-vvx.com"}')
                return NS(content=[NS(type="text", text=txt)],
                          usage=NS(input_tokens=1000, output_tokens=100, server_tool_use=None))
        self.messages = Messages()


def test_catalog_beats_web_search_and_web_result_is_cross_checked():
    from types import SimpleNamespace as NS
    web = _WebSearchSaysFirstDay()
    llm = NS(client=web)
    f = lookup(GOAL_TEXT, 21, TODAY, llm)
    assert f.date == date(2027, 5, 8) and web.calls == 0          # catalogue vérifié d'abord (0 €)
    from coach.race_lookup import Found, _cross_check
    g = _cross_check(Found("VVX 21", date(2027, 5, 6), 21), "Volvic Volcanic Experience 21 km", 21, TODAY, None)
    assert g.date == date(2027, 5, 8)


def _client(tmp_path, acts):
    p = build_pilot(home=tmp_path, today_fn=lambda: TODAY, settings=dataclasses.replace(ST))
    svc = CoachService(p, connector=FakeConnector(), strava_factory=lambda: FakeStrava(acts), background=False)
    c = TestClient(create_app(svc, passcode=CODE, secret=SECRET, background=False))
    assert c.post("/api/login", json={"passcode": CODE}).status_code == 200
    c.pilot, c.service = p, svc
    return c


def test_onboarding_goal_with_her_exact_phrasing(tmp_path):
    c = _client(tmp_path, club_runner())
    c.post("/api/strava/connect")
    r = chat(c, GOAL_TEXT)
    conf = r["messages"][-1]["text"]
    assert "samedi 08/05/2027" in conf and "500 m D+" in conf and "départ 8 h" in conf, conf
    assert "06/05" not in conf
    r = chat(c, "Oui, c'est ça")
    assert "Renfo" in r["messages"][-1]["text"]


def test_onboarding_event_only_asks_the_format(tmp_path):
    c = _client(tmp_path, club_runner())
    c.post("/api/strava/connect")
    r = chat(c, "La VVX")
    ask = r["messages"][-1]["text"]
    assert "plusieurs formats" in ask and "Trail Patrimoine" in ask and "sam. 8 mai" in ask
    assert any("Batignolles" in q for q in r["quick"])
    r = chat(c, "le 21 km")
    conf = r["messages"][-1]["text"]
    assert "samedi 08/05/2027" in conf and "Batignolles" in conf, conf


def test_stored_goal_on_the_events_first_day_is_repaired_at_startup(tmp_path):
    p = build_pilot(home=tmp_path, today_fn=lambda: TODAY, settings=dataclasses.replace(ST))
    p.store.set("profile", Profile(goal_name="Volvic Volcanic Experience (VVX) — Trail Patrimoine Chemin des "
                                   "Batignolles 21 km", goal_date=date(2027, 5, 6), goal_distance_km=21).model_dump(
                                       mode="json"))
    svc = CoachService(p, background=False)
    assert svc.profile().goal_date == date(2027, 5, 8)
    assert "samedi 8 mai 2027" in svc.store.chat(1)[-1]["text"]
    svc.store.set("profile", {**svc.store.get("profile"), "goal_date": "2027-05-06"})
    CoachService(p, background=False)                                       # une seule fois
    assert svc.profile().goal_date == date(2027, 5, 6)


# ------------------------------------------------------------------------ 2. montre oubliée
def test_watch_left_running_uses_moving_time_and_suspect_runs_are_excluded():
    assert effective_duration(24 * 3600, 41 * 60) == 41 * 60
    assert effective_duration(5 * 3600, 3600) == 3600                       # plus de 3 h de pause
    assert effective_duration(80 * 60, 70 * 60) == 80 * 60                  # pauses normales : temps écoulé
    a = sanitize(run(TODAY, 6.1, 24 * 60, moving=41))
    assert a.duration_s == 41 * 60 and not a.suspect and a.elapsed_s == 24 * 3600
    assert abs(a.load - 41 * 0.9) < 0.2
    b = sanitize(run(TODAY, 6.0, 22 * 60, moving=None))                     # pas de temps en mouvement
    assert b.suspect and not is_run(b) and b.duration_s <= 6 * 7 * 60
    c = sanitize(run(TODAY, 25, 10 * 60, moving=9 * 60))                    # 9 h « en mouvement » pour 25 km
    assert c.suspect
    ok = sanitize(run(TODAY, 160, 30 * 60, "Ultra", "Trail", moving=28 * 60))
    assert not ok.suspect and ok.duration_s == 30 * 3600                    # un vrai ultra reste un ultra
    assert sanitize(sanitize(run(TODAY, 6.1, 24 * 60, moving=41))).duration_s == 41 * 60   # idempotent


def test_stored_activities_are_repaired_when_read(tmp_path):
    st = PilotStore(tmp_path / "p.db")
    st.add_activity(run(TODAY - timedelta(days=3), 6.1, 24 * 60, moving=41))
    st.add_activity(run(TODAY - timedelta(days=2), 6.0, 22 * 60, moving=None, hour=7))
    acts = st.activities()
    assert acts[0].duration_s == 41 * 60 and acts[1].suspect
    loads = st.daily_loads(TODAY - timedelta(days=3), TODAY - timedelta(days=2))
    assert loads[0] < 40 and loads[1] < 40                                  # pas 1300 de charge


def test_no_ultra_in_diagnosis_history_or_trophies():
    acts = club_runner()
    acts.append(run(TODAY - timedelta(days=30), 6.0, 22 * 60, moving=None, hour=6))
    st_acts = [sanitize(dataclasses.replace(a)) for a in acts]
    d = diagnose(st_acts, TODAY)
    assert d.history.ultras == [] and d.recent_big_effort is None
    assert d.typical_week_min < 150 and all(r.minutes < 120 for r in d.recent_runs)
    tro = {t["id"]: t for t in career(Facts(st_acts, today=TODAY))["trophies"]}
    assert all(tro[f"duree_{h}"]["day"] is None for h in (3, 6, 12, 24))


def test_mcp_import_uses_moving_time():
    from coach.pilot.strava_mcp import activity_from_mcp
    a = activity_from_mcp({"start_local": "2026-10-18T09:00:00", "sport_type": "Run", "name": "Sortie",
                           "summary": {"elapsed_time": 86400, "moving_time": 2460, "distance": 6100}}, None, ST)
    assert a.duration_s == 2460 and a.load < 60


def test_startup_refreshes_a_stored_diagnosis(tmp_path):
    c = _client(tmp_path, club_runner())
    svc = c.service
    acts = svc.store.activities()
    from coach.diagnosis import Diagnosis
    bad = diagnose([dataclasses.replace(a, duration_s=a.elapsed_s or a.duration_s, suspect=False) for a in acts],
                   TODAY)
    svc.store.set("diag_full", bad.model_dump(mode="json"))
    svc.store.set("repairs_v048", None)
    svc.startup_repairs()
    d = Diagnosis.model_validate(svc.store.get("diag_full"))
    assert d.history.ultras == [] and d.recent_big_effort is None


# ------------------------------------------------------------------------ 3-4. son premier plan
def test_her_profile_text_is_understood():
    x = parse_profile(PROFILE_TEXT, TODAY)
    assert x.quality_days == [1] and x.club_quality and x.quality_minutes == 75
    assert x.long_day == 4 and x.days_per_week == 2
    assert x.bonus and x.bonus_days == [5, 6]
    assert x.strength == "bodyweight" and x.no_other_sports and x.other_sports == []
    assert x.discipline == "mixte"
    noel = x.busy[0]
    assert (noel.start, noel.end, noel.kind, noel.min_runs) == (date(2026, 12, 19), date(2027, 1, 3), "light", 2)
    t = parse_turn(LATER_TEXT, TODAY)
    assert t.intent == "modify" and t.quality_days == [1] and t.long_day == 4 and t.bonus_days == [5, 6]


def _her_plan():
    acts = [sanitize(dataclasses.replace(a)) for a in club_runner()]
    d = diagnose(acts, TODAY)
    p = Profile(goal_name="VVX 21 km", goal_date=date(2027, 5, 8), goal_distance_km=21.5, goal_dplus_m=500)
    p, prefs, taken = apply_profile_fields(parse_profile(PROFILE_TEXT, TODAY), p, Prefs(), TODAY, defaults=True)
    return d, p, prefs, taken, initial_block(d, p, TODAY + timedelta(days=1), prefs)


def test_first_plan_respects_her_week_and_her_volume():
    d, p, prefs, taken, b = _her_plan()
    assert p.days_per_week == 2 and p.long_day == 4 and prefs.strength_with_quality is False
    assert any("club le mardi" in t for t in taken)
    avg = recent_week_avg(d, TODAY + timedelta(days=1))
    assert 80 <= avg <= 130, avg
    runs = [s for s in b.sessions if s.intensity not in ("rest", "race")]
    for w in b.weeks:
        ws = date.fromisoformat(w["start"])
        wk = [s for s in runs if ws <= s.day < ws + timedelta(days=7)]
        fixed = [s for s in wk if not is_bonus(s)]
        assert len(wk) <= 3 and len(fixed) <= 2
        assert all(s.day.weekday() in (1, 4) for s in fixed)
        assert all(s.day.weekday() in (5, 6) for s in wk if is_bonus(s))
        tue = [s for s in fixed if s.day.weekday() == 1]
        assert all(s.duration_min == 75 and "club" in s.description for s in tue if s.intensity == "vo2")
        assert all(s.intensity == "long" for s in fixed if s.day.weekday() == 4 and ws >= b.intensity_from - timedelta(days=7))
    full = [w for w in b.weeks if date.fromisoformat(w["start"]) > TODAY]
    assert full[0]["minutes"] <= 1.2 * avg + 1, (full[0]["minutes"], avg)
    assert all(w["minutes"] <= 1.2 * avg * 1.1 ** k + 5 for k, w in enumerate(full))
    assert max(w["minutes"] for w in b.weeks) < 170                         # pas 3 h
    ren = [s for s in b.sessions if s.intensity == "rest"]
    assert ren and all(s.day.weekday() not in (1, 4) for s in ren)          # jamais avec le club ni la longue
    assert all("haltère" not in s.description and "barre" not in s.description for s in ren)


def test_her_later_message_updates_prefs_and_replaces_sessions(tmp_path):
    c = _client(tmp_path, club_runner())
    c.post("/api/strava/connect")
    chat(c, GOAL_TEXT)
    chat(c, "Oui, c'est ça")
    chat(c, "5 jours, sortie longue le dimanche")                           # 1re réponse : le plan par défaut
    chat(c, "Pas de limite")                                                 # v0.49 : volume maximum
    chat(c, "Ça me va")
    svc = c.service
    assert svc.profile().days_per_week == 5
    r = chat(c, LATER_TEXT)
    assert r["pending"] and r["quick"] == ["Ça me va", "Garder l'ancien plan"], r["messages"][-1]["text"]
    prefs, prof = svc.prefs(), svc.profile()
    assert prefs.quality_days == [1] and prof.long_day == 4 and prefs.bonus_days == [5, 6]
    pend = svc.store.get("pending_onboarding_plan")["sessions"]
    days = {date.fromisoformat(s["day"]).weekday() for s in pend if s["intensity"] not in ("rest", "race")
            and not s["session_id"].endswith("-bonus")}
    assert days <= {1, 4}
    assert all(date.fromisoformat(s["day"]).weekday() in (5, 6) for s in pend if s["session_id"].endswith("-bonus"))
    r = chat(c, "Ça me va")
    assert not r["pending"]
    plan = [s for s in svc.store.plan(since=TODAY + timedelta(days=1)) if s.intensity not in ("rest", "race")]
    assert plan and all(s.day.weekday() in (1, 4, 5, 6) for s in plan)
    # une question d'entraînement ne déclenche rien
    r = chat(c, "Je fais quoi demain avant ma sortie longue ?")
    assert svc.store.get("ob_state") == "done"


def test_club_habit_without_instruction_verb_is_saved(tmp_path):
    """v0.51.1 : « tous les mardis soir j'ai ma séance avec le club » (sans « garde », « mets »…) partait au coach,
    qui répondait sans rien enregistrer : le mardi restait en repos dans les semaines suivantes."""
    from coach.onboarding import CLUB_TEXT, looks_like_prefs_edit
    assert looks_like_prefs_edit("Tous les mardis soir j'ai ma séance avec le club")
    assert not looks_like_prefs_edit("Mardi j'ai repos sur mon entraînement de la semaine")
    c = _client(tmp_path, club_runner())
    c.post("/api/strava/connect")
    chat(c, GOAL_TEXT)
    chat(c, "Oui, c'est ça")
    chat(c, "5 jours, sortie longue le dimanche")
    chat(c, "Pas de limite")
    chat(c, "Ça me va")
    svc = c.service
    r = chat(c, "Tous les mardis soir j'ai ma séance avec le club")
    assert r["pending"] and r["quick"] == ["Ça me va", "Garder l'ancien plan"], r["messages"][-1]["text"]
    assert svc.prefs().quality_days == [1] and svc.prefs().club_quality
    chat(c, "Ça me va")
    tue = [s for s in svc.store.plan(since=TODAY + timedelta(days=1)) if s.day.weekday() == 1]
    assert tue and any(s.description == CLUB_TEXT for s in tue)
