"""Ingestion (FIT, GPX, archive Strava) et client de l'API Strava avec un faux serveur HTTP et une horloge injectée."""
from __future__ import annotations

import dataclasses
import json
import zipfile
from datetime import UTC, date, datetime, timedelta

import gpxpy.gpx
import pytest

from coach.pilot import ingest
from coach.pilot import strava_api as sa
from coach.pilot.store import PilotStore
from tests.fitwriter import write_fit
from tests.test_pilot import ST

TODAY = date(2026, 9, 30)


def gpx_file(path, timed=True, n=200):
    g = gpxpy.gpx.GPX()
    t = gpxpy.gpx.GPXTrack()
    g.tracks.append(t)
    s = gpxpy.gpx.GPXTrackSegment()
    t.segments.append(s)
    t0 = datetime(2026, 9, 28, 6, tzinfo=UTC)
    for i in range(n):
        s.points.append(gpxpy.gpx.GPXTrackPoint(47.2 + i * 0.0001, -1.5, elevation=10 + (i % 20),
                                                time=t0 + timedelta(seconds=5 * i) if timed else None))
    path.write_text(g.to_xml())
    return path


# --------------------------------------------------------------------------- fichiers
def test_gpx_with_and_without_timestamps(tmp_path):
    a = ingest.activity_from_file(gpx_file(tmp_path / "s.gpx"), ST)
    assert a.sport == "Course" and a.local_day == date(2026, 9, 28) and a.load_source
    with pytest.raises(ValueError, match="horodatage"):
        ingest.activity_from_file(gpx_file(tmp_path / "trace.gpx", timed=False), ST)
    with pytest.raises(ValueError, match="format non géré"):
        ingest.activity_from_file(tmp_path / "x.tcx", ST)


def test_from_bytes_sanitizes_name(tmp_path):
    data = gpx_file(tmp_path / "s.gpx").read_bytes()
    a = ingest.activity_from_bytes(data, "../../mon fichier é.gpx", ST)
    assert a.name == "mon_fichier__.gpx" and a.origin == "telegram"


@pytest.mark.parametrize("raw,expected", [
    ("Sep 29, 2026, 4:12:34 AM", datetime(2026, 9, 29, 4, 12, 34, tzinfo=UTC)),
    ("29 sept. 2026, 04:12:34", datetime(2026, 9, 29, 4, 12, 34, tzinfo=UTC)),
    ("3 août 2026 18:00:00", datetime(2026, 8, 3, 18, 0, 0, tzinfo=UTC)),
    ("2026-09-29T04:12:34Z", datetime(2026, 9, 29, 4, 12, 34, tzinfo=UTC)),
    ("12 brumaire 2026, 10:00:00", None),
    ("n'importe quoi", None),
])
def test_parse_strava_date(raw, expected):
    assert ingest.parse_strava_date(raw) == expected


@pytest.mark.parametrize("raw,expected", [("1,234.5", 1234.5), ("12,5", 12.5), (" 3 000 ", 3000.0),
                                          ("", None), (None, None), ("abc", None)])
def test_num(raw, expected):
    assert ingest._num(raw) == expected


def test_parse_csv_empty_and_km_only():
    assert ingest.parse_strava_csv("") == []
    rows = ingest.parse_strava_csv("Activity Date,Activity Name,Activity Type,Distance,Elapsed Time\n"
                                   "\"Sep 29, 2026, 4:12:34 AM\",Footing,Run,10.5,3600\nmauvaise date,x,Run,1,1\n")
    assert len(rows) == 1 and rows[0]["distance_m"] == 10500


CSV = ("Activity ID,Activity Date,Activity Name,Activity Type,Elapsed Time,Distance,Moving Time,Average Heart Rate,"
       "Elevation Gain,Filename\n")


def test_archive_falls_back_to_csv_when_fit_is_broken(tmp_path):
    root = tmp_path / "export"
    (root / "activities").mkdir(parents=True)
    (root / "activities" / "1.fit").write_bytes(b"pas un FIT")
    (root / "activities.csv").write_text(CSV + "1,\"Sep 29, 2026, 4:12:34 AM\",Sortie,Run,3600,10000,3500,140,50,"
                                         "activities/1.fit\n2,\"Jan 2, 2020, 4:12:34 AM\",Vieille,Run,3600,1,1,1,1,\n")
    store = PilotStore(tmp_path / "p.db")
    rep = ingest.import_archive(root, store, ST, today=TODAY)
    assert rep.new == 1 and "repli sur le CSV" in rep.skipped[0]
    assert store.activities()[0].name == "Sortie"


def test_archive_without_csv_reads_files(tmp_path):
    root = tmp_path / "fichiers"
    root.mkdir()
    write_fit(root / "a.fit", datetime(2026, 9, 29, 6, tzinfo=UTC), 1800, lambda i: 140)
    gpx_file(root / "b.gpx")
    (root / "c.gpx").write_text("<gpx>cassé")
    (root / "notes.txt").write_text("rien")
    write_fit(root / "vieux.fit", datetime(2025, 1, 1, 6, tzinfo=UTC), 1800, lambda i: 140)
    store = PilotStore(tmp_path / "p.db")
    rep = ingest.import_archive(root, store, ST, today=TODAY)
    assert rep.new == 2 and len(rep.skipped) == 1 and rep.skipped[0].startswith("c.gpx")


def test_zip_archive_extracts_only_activities(tmp_path):
    z = tmp_path / "export.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("export_1/activities.csv", CSV + "1,\"Sep 29, 2026, 4:12:34 AM\",Z,Run,3600,10000,3500,140,50,\n")
        zf.writestr("export_1/profile.csv", "secret")
    rep = ingest.import_archive(z, PilotStore(tmp_path / "p.db"), ST, today=TODAY)
    assert rep.new == 1


# --------------------------------------------------------------------------- API Strava
class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, params=None, data=None, headers=None):
        self.calls.append((method, url, params, data))
        for key, resp in self.routes.items():
            if url.endswith(key):
                return resp(params, data) if callable(resp) else resp
        return 404, {"message": "not found"}


def connected(tmp_path, expires_at=4_000_000_000):
    (tmp_path / "strava_api.json").write_text(json.dumps({"access_token": "A", "refresh_token": "R",
                                                          "expires_at": expires_at}))


def test_access_token_refresh_with_injected_clock(tmp_path):
    connected(tmp_path, expires_at=1000)
    http = FakeHttp({"/oauth/token": (200, {"access_token": "A2", "refresh_token": "R2", "expires_at": 9000})})
    api = sa.StravaAPI(tmp_path, "1", "s", http, clock=lambda: 500.0)
    assert api._access_token() == "A" and http.calls == []          # encore valide à t=500
    api.clock = lambda: 950.0
    assert api._access_token() == "A2" and api._access_token() == "A2"
    bad = sa.StravaAPI(tmp_path, "1", "s", FakeHttp({"/oauth/token": (400, {})}), clock=lambda: 10 ** 10)
    with pytest.raises(sa.StravaAuthError, match="expirée"):
        bad._access_token()
    (tmp_path / "vide").mkdir()
    with pytest.raises(sa.StravaAuthError, match="pas connecté"):
        sa.StravaAPI(tmp_path / "vide", "1", "s", http)._access_token()


@pytest.mark.parametrize("status,exc", [(429, sa.StravaRateLimited), (401, sa.StravaAuthError), (500, RuntimeError)])
def test_get_errors(tmp_path, status, exc):
    connected(tmp_path)
    api = sa.StravaAPI(tmp_path, "1", "s", FakeHttp({"/athlete": (status, {"message": "x"})}))
    with pytest.raises(exc):
        api.athlete()


def test_to_streams_shapes():
    assert sa.to_streams(None) is None and sa.to_streams({"time": {"data": [1]}}) is None
    s = sa.to_streams({"time": {"data": [0, 1]}, "heartrate": {"data": [1, 2]}, "distance": {"data": [0, 1]},
                       "altitude": {"data": [1]}})
    assert set(s) == {"time", "heart_rate", "distance"}


def act(i, day, minutes=100, hr=True):
    start = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=7)
    return {"id": i, "name": f"Sortie {i}", "sport_type": "Run", "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "start_date_local": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "distance": minutes * 180,
            "moving_time": minutes * 60, "elapsed_time": minutes * 60, "total_elevation_gain": 10,
            "has_heartrate": hr, "average_heartrate": 140}


def streams(n=600):
    return 200, {"time": {"data": list(range(0, n * 10, 10))}, "heartrate": {"data": [140 + (i % 5) for i in range(n)]},
                 "distance": {"data": [i * 30.0 for i in range(n)]}, "altitude": {"data": [10.0] * n}}


def test_durability_backfill_records_long_runs_once(tmp_path):
    connected(tmp_path)
    store = PilotStore(tmp_path / "p.db")
    acts = [act(1, TODAY - timedelta(days=3)), act(2, TODAY - timedelta(days=2), minutes=40)]
    http = FakeHttp({"/athlete/activities": (200, acts), "/streams": streams()})
    api = sa.StravaAPI(tmp_path, "1", "s", http)
    assert sa.durability_backfill(store, api, today=TODAY) == 1
    assert "1" in store.get("decoupling") and store.get("decoupling_backfilled")
    failing = sa.StravaAPI(tmp_path, "1", "s", FakeHttp({"/athlete/activities": (429, {})}))
    assert sa.durability_backfill(PilotStore(tmp_path / "q.db"), failing, today=TODAY) == 0


def test_sync_api_streams_errors_and_injected_now(tmp_path):
    connected(tmp_path)
    store = PilotStore(tmp_path / "p.db")
    acts = [act(1, TODAY - timedelta(days=3)), act(2, TODAY - timedelta(days=2))]

    def stream_route(params, data):
        return streams() if len([c for c in http.calls if "/streams" in c[1]]) == 1 else (404, {"message": "manuel"})

    http = FakeHttp({"/athlete/activities": lambda p, d: (200, acts if p["page"] == 1 and "before" not in p else []),
                     "/streams": stream_route})
    msgs = []
    res = sa.sync_strava_api(store, dataclasses.replace(ST), sa.StravaAPI(tmp_path, "1", "s", http), today=TODAY,
                             progress=msgs.append, now=lambda: datetime(2026, 9, 30, 12, 0))
    assert res.report.new == 2 and res.partial is None and store.get("strava_last_sync") == "2026-09-30T12:00"
    assert any("Fréquence cardiaque" in m for m in msgs)


def test_sync_api_rate_limited_on_streams_keeps_average_hr(tmp_path):
    connected(tmp_path)
    store = PilotStore(tmp_path / "p.db")
    acts = [act(1, TODAY - timedelta(days=3))]
    http = FakeHttp({"/athlete/activities": lambda p, d: (200, acts if p["page"] == 1 else []),
                     "/streams": (429, {})})
    res = sa.sync_strava_api(store, dataclasses.replace(ST), sa.StravaAPI(tmp_path, "1", "s", http), today=TODAY)
    assert res.partial and res.report.new == 1


def test_refresh_names_survives_errors(tmp_path):
    connected(tmp_path)
    api = sa.StravaAPI(tmp_path, "1", "s", FakeHttp({"/athlete/activities": (500, {})}))
    assert sa.refresh_recent_names(PilotStore(tmp_path / "p.db"), api, TODAY) == 0


def test_backfill_edges(tmp_path):
    connected(tmp_path)
    store = PilotStore(tmp_path / "p.db")
    api = sa.StravaAPI(tmp_path, "1", "s", FakeHttp({}))
    assert sa.backfill_strava_api(store, ST, api, today=TODAY).listed == 0          # rien d'importé encore
    from coach.pilot.strava_mcp import activity_from_mcp
    store.add_activity(activity_from_mcp(sa.to_item(act(9, date(2020, 1, 1))), None, ST))
    assert sa.backfill_strava_api(store, ST, api, years=1, today=TODAY).listed == 0  # déjà assez ancien
    assert store.get("strava_history_years") == 1


def test_connector_expires_pending_and_handles_exchange_errors(tmp_path):
    t = [0.0]
    done = []

    def boom(*a):
        raise OSError("certificat")

    con = sa.StravaAPIConnector(tmp_path, "1", "s", boom, clock=lambda: t[0])
    st1 = con.start("https://app/cb", done.append)["url"].split("state=")[1]
    t[0] = 1000.0
    st2 = con.start("https://app/cb", done.append)["url"].split("state=")[1]
    assert st1 not in con._pending and st2 in con._pending                 # autorisation de plus de 15 min oubliée
    assert not con.callback(None, st2, "access_denied") and "refusée" in con.error
    st3 = con.start("https://app/cb", done.append)["url"].split("state=")[1]
    assert not con.callback("c", st3, None, "activity:read_all") and "certificat" in con.error
    con.http = FakeHttp({"/oauth/token": (401, {"message": "Bad"})})
    st4 = con.start("https://app/cb", done.append)["url"].split("state=")[1]
    assert not con.callback("c", st4, None) and "Client Secret" in con.error and "Bad" in con.error

    def on_done(_athlete):
        raise RuntimeError("après connexion")

    con.http = FakeHttp({"/oauth/token": (200, {"refresh_token": "R", "access_token": "A", "athlete": {"id": 3}})})
    st5 = con.start("https://app/cb", on_done)["url"].split("state=")[1]
    assert con.callback("c", st5, None) and con.status == "connected" and con.connected()


def test_urllib_http_wraps_network_errors(monkeypatch):
    import httpx

    def fail(*a, **k):
        raise httpx.ConnectError("dns")

    monkeypatch.setattr(httpx, "request", fail)
    with pytest.raises(RuntimeError, match="injoignable"):
        sa.urllib_http("GET", "https://x")

    class R:
        status_code = 204

        def json(self):
            raise ValueError

    monkeypatch.setattr(httpx, "request", lambda *a, **k: R())
    assert sa.urllib_http("GET", "https://x") == (204, None)
