"""Mode pilote : ingestion, synchro Strava (faux client), affûtage, Google Agenda (faux service), bot."""
import dataclasses
import gzip
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest

from coach.adapters.calendar import GoogleCalendarAdapter
from coach.adapters.telegram_bot import TelegramAPI
from coach.guardrails import QUALITY_INTENSITIES
from coach.pilot.bot import PilotBot
from coach.pilot.config import PilotSettings
from coach.pilot.ingest import activity_from_file, import_archive, load_from_avg_hr, parse_strava_csv
from coach.pilot.repo import load_state, taper_inputs
from coach.pilot.runtime import build_pilot
from coach.pilot.store import Activity, PilotStore
from coach.pilot.strava_mcp import activity_from_mcp, sync_strava
from coach.schemas import PlanChange, PlannedSession
from tests.fakes_google import Svc
from tests.fitwriter import write_fit

TODAY = date(2026, 9, 30)          # J-15
RACE = date(2026, 10, 15)
ST = PilotSettings(tz="Europe/Paris")

# Semaine type d'avant-affûtage : (jour, minutes, FC moyenne, D+)
WEEK = {1: (70, 165, 0), 2: (60, 145, 150), 3: (70, 160, 600), 4: (45, 135, 0),
        5: (200, 145, 1600), 6: (75, 145, 250)}                      # lundi repos


def fill_history(store: PilotStore, days: int = 70, until: date = TODAY - timedelta(days=1)):
    for i in range(days):
        d = until - timedelta(days=i)
        if d.weekday() not in WEEK:
            continue
        m, hr, dp = WEEK[d.weekday()]
        load, src = load_from_avg_hr(hr, m * 60, "Trail", ST)
        store.add_activity(Activity(datetime(d.year, d.month, d.day, 6, tzinfo=UTC), d, "Trail",
                                    m * 60, m * 160, dp, hr, load, src, "test"))


@pytest.fixture
def pilot(tmp_path):
    svc = Svc()
    p = build_pilot(home=tmp_path, calendar_service=svc, today_fn=lambda: TODAY,
                    settings=dataclasses.replace(ST))
    p.svc = svc
    return p


# --------------------------------------------------------------------------- ingestion
def test_fit_hrtss_is_100_for_one_hour_at_threshold(tmp_path):
    f = tmp_path / "seuil.fit"
    write_fit(f, datetime(2026, 9, 29, 17, tzinfo=UTC), 3600, lambda i: ST.threshold_hr)
    a = activity_from_file(f, ST)
    assert a.load_source == "hrTSS" and a.load == pytest.approx(100, abs=1)
    gz = tmp_path / "seuil.fit.gz"
    gz.write_bytes(gzip.compress(f.read_bytes()))
    assert activity_from_file(gz, ST).load == pytest.approx(a.load)


def test_store_dedupes_and_upgrades_better_source(tmp_path):
    s = PilotStore(tmp_path / "p.db")
    base = dict(start_utc=datetime(2026, 9, 29, 17, tzinfo=UTC), local_day=date(2026, 9, 29),
                sport="Trail", duration_s=3600, distance_m=9000, dplus_m=300, avg_hr=150)
    assert s.add_activity(Activity(**base, load=48, load_source="csv_avg_hr")) == "new"
    assert s.add_activity(Activity(**base, load=48, load_source="csv_avg_hr")) == "duplicate"
    assert s.add_activity(Activity(**base, load=52, load_source="hrTSS")) == "upgraded"
    assert [a.load for a in s.activities()] == [52]


CSV = ('Activity ID,Activity Date,Activity Name,Activity Type,Elapsed Time,Distance,Filename,'
       'Elapsed Time,Moving Time,Distance,Elevation Gain,Average Heart Rate\n'
       '1,"Sep 27, 2026, 6:00:00 AM",Longue,Trail Run,12000,30.5,activities/1.fit.gz,12000,11500,30500.0,1600.0,146\n'
       '2,"Sep 28, 2026, 5:00:00 PM",Récup,Run,2700,7.1,,2700,2650,7100.0,40.0,\n'
       '3,"Jan 2, 2025, 5:00:00 PM",Vieux,Run,2700,7.1,,2700,2650,7100.0,40.0,140\n')


def test_strava_csv_handles_duplicate_columns():
    rows = parse_strava_csv(CSV)
    assert rows[0]["distance_m"] == 30500 and rows[0]["moving_s"] == 11500 and rows[0]["avg_hr"] == 146
    assert rows[1]["avg_hr"] is None and rows[0]["start"].tzinfo is not None


def test_import_archive_zip_prefers_fit_file(tmp_path):
    fit = tmp_path / "1.fit"
    write_fit(fit, datetime(2026, 9, 27, 6, tzinfo=UTC), 1200, lambda i: 150)
    z = tmp_path / "export_123.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("export_123/activities.csv", CSV)
        zf.writestr("export_123/activities/1.fit.gz", gzip.compress(fit.read_bytes()))
        zf.writestr("export_123/profile.csv", "données inutiles")
    store = PilotStore(tmp_path / "p.db")
    rep = import_archive(z, store, ST, days=180, today=TODAY)
    assert rep.new == 2                                  # l'activité de 2025 est hors fenêtre
    assert rep.by_source == {"hrTSS": 1, "estimate": 1}  # FIT lu ; sans FC -> forfait
    assert {a.name for a in store.activities()} == {"Longue", "Récup"}


# --------------------------------------------------------------------------- Strava MCP
ITEM = {"id": "42", "name": "Séance VHS", "sport_type": "Run", "start_local": "2026-09-29T19:32:43",
        "summary": {"distance": 6559, "moving_time": 2095, "elapsed_time": 2375, "elevation_gain": 139}}


def test_activity_from_mcp_uses_heart_rate_stream():
    streams = {"time": list(range(0, 2375, 5)), "heart_rate": [150] * len(range(0, 2375, 5))}
    a = activity_from_mcp(ITEM, streams, ST)
    assert a.load_source == "hrTSS" and a.start_utc.hour == 17        # 19:32 Paris = 17:32 UTC
    assert a.dplus_m == 139 and a.local_day == date(2026, 9, 29)
    assert activity_from_mcp(ITEM, {}, ST).load_source == "estimate"


class FakeStrava:
    def __init__(self, items, fail_at=None):
        self.items, self.fail_at, self.calls = items, fail_at, []

    def call(self, name, args):
        self.calls.append((name, args))
        start = args["range_start"]
        return {"activities": [i for i in self.items if i["start_local"] >= start], "has_next_page": False}

    def batch(self, calls, pause_s=0.3):
        out = []
        for k, (_name, _args) in enumerate(calls):
            if self.fail_at is not None and k >= self.fail_at:
                out.append(RuntimeError("429 quota"))
                break
            out.append({"time": [0, 600, 1200, 1800, 2375], "heart_rate": [140, 150, 155, 150, 145]})
        return out


def test_sync_strava_is_incremental_and_resumes_after_quota(tmp_path):
    store = PilotStore(tmp_path / "p.db")
    items = [dict(ITEM, id=str(i), start_local=f"2026-09-{20 + i}T07:00:00") for i in range(5)]
    res = sync_strava(store, ST, FakeStrava(items, fail_at=3), today=TODAY)
    assert res.report.new == 3 and res.partial and "429" in res.partial
    res2 = sync_strava(store, ST, FakeStrava(items), today=TODAY)       # reprend après la 3e
    assert res2.listed == 2 and res2.report.new == 2
    assert len(store.activities()) == 5


# --------------------------------------------------------------------------- affûtage
def test_taper_plan_from_real_load_passes_guardrails(pilot):
    fill_history(pilot.store)
    ti = taper_inputs(pilot.store, RACE)
    assert 5.5 <= ti.sessions_per_week <= 6.5 and ti.pre_taper_week_load > 300
    plan, verdict, _ = pilot.repo.propose_taper()
    assert verdict.allowed and not verdict.blocking
    race = [s for s in plan if s.is_race]
    assert len(race) == 1 and race[0].day == RACE
    pre = [s for s in plan if not s.is_race and s.day < RACE]
    w1 = sum(s.planned_load for s in pre if (RACE - s.day).days >= 9)
    w2 = sum(s.planned_load for s in pre if (RACE - s.day).days <= 8)
    # semaine 1 (6 jours ici, départ J-14) puis semaine 2 : réduction progressive dans -41/-60 % en moyenne
    assert w2 < w1
    avg_red = 1 - (w1 * 7 / 6 + w2) / (2 * ti.pre_taper_week_load)
    assert 0.35 <= avg_red <= 0.60
    assert any(s.intensity in QUALITY_INTENSITIES for s in pre if (RACE - s.day).days >= 9)
    assert any(s.intensity in QUALITY_INTENSITIES for s in pre if (RACE - s.day).days <= 8)


def test_no_taper_without_enough_history(pilot):
    fill_history(pilot.store, days=10)
    with pytest.raises(LookupError):
        pilot.repo.propose_taper()


def test_load_state_reflects_history(pilot):
    fill_history(pilot.store)
    state, hist = load_state(pilot.store, TODAY)
    assert hist >= 60 and 0.8 < state.acwr < 1.3 and state.ctl > 40


# --------------------------------------------------------------------------- Google Agenda
def sess(sid, day, k="z2", m=60):
    return PlannedSession(session_id=sid, day=day, intensity=k, duration_min=m, planned_load=50)


def test_google_adapter_only_touches_tagged_events():
    svc = Svc()
    perso = svc.add_personal("Dîner chez Marie")
    g = GoogleCalendarAdapter(svc, "primary", "Europe/Paris", 7,
                              race_start=datetime.fromisoformat("2026-10-15T22:00:00+04:00"),
                              race_tz="Indian/Reunion")
    g.apply("a", [PlanChange(action="add", session=sess("s1", TODAY), reason=""),
                  PlanChange(action="add", session=sess("s2", TODAY, "rest", 0), reason=""),
                  PlanChange(action="add", session=PlannedSession(
                      session_id="r", day=RACE, intensity="race", duration_min=2172, planned_load=0,
                      is_race=True, description="Diagonale des Fous — objectif"), reason="")])
    ev = {e["extendedProperties"]["private"]["coach_session_id"]: e for e in svc.coach_events()}
    assert set(ev) == {"s1", "r"}                                  # pas d'événement « Repos »
    assert ev["s1"]["start"]["dateTime"].endswith("07:00:00") and ev["s1"]["transparency"] == "transparent"
    assert ev["r"]["start"] == {"dateTime": "2026-10-15T22:00:00", "timeZone": "Indian/Reunion"}
    g.apply("a", [PlanChange(action="modify", session=sess("s1", TODAY, "z1", 40), reason="")])
    assert len(svc.coach_events()) == 2 and "40 min" in ev["s1"]["summary"]
    assert g.clear_all() == 2 and list(svc.data) == [perso]       # l'événement perso reste


# --------------------------------------------------------------------------- bot
class FakeHTTP:
    def __init__(self, files=None):
        self.sent, self.files = [], files or {}

    def __call__(self, url, params, timeout):
        if url.endswith("/sendMessage"):
            self.sent.append(params["text"])
            return {"ok": True, "result": {}}
        if url.endswith("/getFile"):
            return {"ok": True, "result": {"file_path": params["file_id"]}}
        return {"ok": True, "result": []}

    def get(self, url, timeout):
        return self.files[url.rsplit("/", 1)[-1]]


ME = 111


def make_bot(pilot, files=None):
    http = FakeHTTP(files)
    return PilotBot(TelegramAPI("T", http, http.get), {ME}, pilot), http


def upd(text=None, **extra):
    m = {"chat": {"id": ME}, **extra}
    if text is not None:
        m["text"] = text
    return {"update_id": 1, "message": m}


def test_bot_plan_then_yes_writes_google_calendar(pilot):
    fill_history(pilot.store)
    bot, http = make_bot(pilot)
    bot.handle_update(upd("/plan"))
    assert "Garde-fous : ✅" in http.sent[-1] and "(oui / non)" in http.sent[-1]
    assert pilot.svc.coach_events() == []                         # rien d'écrit avant le « oui »
    bot.handle_update(upd("oui"))
    assert "Google Agenda ✅" in http.sent[-1]
    evs = pilot.svc.coach_events()
    assert any(e["summary"].startswith("🏁") for e in evs) and len(evs) >= 10
    assert pilot.repo.get("pilote").baseline_week_load > 0         # le plan devient la référence
    bot.handle_update(upd("/plan"))
    bot.handle_update(upd("oui"))                                   # régénérer : pas de doublon
    assert len(pilot.svc.coach_events()) == len(evs)


def test_bot_imports_fit_sent_from_phone(pilot, tmp_path):
    f = tmp_path / "x.fit"
    write_fit(f, datetime(2026, 9, 30, 5, tzinfo=UTC), 1800, lambda i: 150)
    bot, http = make_bot(pilot, files={"FILE1": f.read_bytes()})
    bot.handle_update(upd(document={"file_id": "FILE1", "file_name": "Suunto_trail.fit", "file_size": 999}))
    assert "Sortie importée" in http.sent[-1] and "30 min" in http.sent[-1]
    assert len(pilot.store.activities()) == 1
    bot.handle_update(upd(document={"file_id": "FILE1", "file_name": "photo.jpg"}))
    assert "fichier FIT" in http.sent[-1]


def test_bot_coaching_goes_through_graph_and_calendar(pilot):
    fill_history(pilot.store)
    pilot.write_plan(pilot.repo.propose_taper(TODAY)[0])
    bot, http = make_bot(pilot)
    bot.handle_update(upd("J'ai fait une soirée avec beaucoup d'alcool hier soir, je m'entraîne comment demain ?"))
    assert "pas d'intensité" in http.sent[-1]
    near = list(pilot.store.plan(TODAY, TODAY + timedelta(days=1)))
    assert all(s.intensity in ("z1", "rest") for s in near)
    summaries = [e["summary"] for e in pilot.svc.coach_events()]
    assert not any("Seuil" in s for s in summaries[:2])


def test_bot_daily_budget_cap(pilot):
    pilot.settings.daily_budget_eur = 0.0
    bot, http = make_bot(pilot)
    bot.handle_update(upd("Je me sens bien"))
    assert "Budget Claude du jour atteint" in http.sent[-1] and pilot.deps.llm.usage_log == []


def _major_llm():
    from coach.llm import MockLLM
    from coach.schemas import CoachDecision, Signals
    moved = sess("coach-2026-10-01", date(2026, 10, 1), "z1", 30)
    return MockLLM(handlers={
        "extract": lambda u: Signals(unavailable_dates=[date(2026, 10, 1)]),
        "coach": lambda u: CoachDecision(changes=[PlanChange(action="modify", session=moved, reason="douleur")],
                                         message_to_athlete="Je remplace le seuil de jeudi par 30 min faciles.",
                                         is_major_change=True)})


def test_pending_confirmation_survives_bot_restart(tmp_path):
    svc = Svc()
    p1 = build_pilot(home=tmp_path, calendar_service=svc, today_fn=lambda: TODAY, settings=ST, llm=_major_llm())
    fill_history(p1.store)
    p1.write_plan(p1.repo.propose_taper(TODAY)[0])
    bot1, http1 = make_bot(p1)
    bot1.handle_update(upd("Jeudi soir je n'aurai que 30 minutes, on adapte ?"))
    assert "(oui / non)" in http1.sent[-1]
    # redémarrage du bot (Mac en veille, Ctrl+C...) : la question en attente est conservée
    p2 = build_pilot(home=tmp_path, calendar_service=svc, today_fn=lambda: TODAY, settings=ST, llm=_major_llm())
    bot2, http2 = make_bot(p2)
    bot2.handle_update(upd("oui"))
    assert p2.store.plan(date(2026, 10, 1), date(2026, 10, 1))[0].intensity == "z1"
    ev = [e for e in svc.coach_events()
          if e["extendedProperties"]["private"]["coach_session_id"] == "coach-2026-10-01"]
    assert "30 min" in ev[0]["summary"]


def test_pending_plan_survives_bot_restart(pilot):
    fill_history(pilot.store)
    bot, http = make_bot(pilot)
    bot.handle_update(upd("/plan"))
    bot2, http2 = make_bot(pilot)
    bot2.handle_update(upd("oui"))
    assert "Google Agenda ✅" in http2.sent[-1]
