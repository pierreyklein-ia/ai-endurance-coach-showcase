import json

import numpy as np

from coach.pacer.course import CatalogSource, CoursePoint, RaceCourse, TraceDeTrailSource, resolve, validate
from coach.pacer.tracks import Track


def flat_track(km=100.0, dplus=5000.0):
    x = np.linspace(0, km * 1000, 20001)
    z = (dplus / 20) * (1 - np.cos(np.linspace(0, 20 * 2 * np.pi, len(x)))) / 2 * 2  # 20 bosses
    return Track("t", x, z, lat=np.full(len(x), -21.0), lon=np.full(len(x), 55.4))


def test_validation_flags_wrong_race():
    tr = flat_track()
    assert validate(tr, 100, tr.alt.max() * 20, tol_dplus=0.2).ok
    assert not validate(tr, 180, 10000).ok


def test_single_average_stop_only_at_aid_stations():
    tr = flat_track(km=90)
    c = RaceCourse("x", tr, [CoursePoint("A", 30, services=("ravito",)), CoursePoint("B", 45),
                             CoursePoint("C", 100, services=("base_vie",))], 100, 1, "test", False)
    cps = c.checkpoints(avg_stop_min=4)
    assert [cp.stop_min for cp in cps] == [4, 0, 4]
    assert abs(cps[-1].km - 90) < 1e-6              # km officiels recalés sur la distance du GPX


def test_resolver_skips_sources_without_commercial_rights(tmp_path):
    class FakeSearch:
        def search(self, q, domains): return ["https://tracedetrail.fr/fr/trace/1"]
    course, log = resolve("Diag", 2026, [TraceDeTrailSource(FakeSearch())], 180, 10000, commercial=True)
    assert course is None and "non autorisé" in log[0]
    course, log = resolve("Diag", 2026, [TraceDeTrailSource(FakeSearch())], 180, 10000, commercial=False)
    assert course is None and "accord pro" in log[0]


def test_catalog_source_roundtrip(tmp_path):
    import gpxpy.gpx
    g = gpxpy.gpx.GPX(); t = gpxpy.gpx.GPXTrack(); g.tracks.append(t); s = gpxpy.gpx.GPXTrackSegment(); t.segments.append(s)
    for i in range(2001):
        s.points.append(gpxpy.gpx.GPXTrackPoint(-21 + i * 0.00045, 55.4, elevation=100 + 50 * np.sin(i / 50)))
    (tmp_path / "course.gpx").write_text(g.to_xml())
    (tmp_path / "ma-course-2026.json").write_text(json.dumps({"gpx_file": "course.gpx", "points": [
        {"name": "Ravito", "km": 50, "services": ["ravito"]}]}))
    src = CatalogSource(tmp_path, commercial_use_allowed=True)
    track, _, _ = src.fetch(str(tmp_path / "ma-course-2026.json"))
    assert track.t is None                                   # horodatage factice supprimé
    rep = validate(track, 1, 1)
    course, log = resolve("ma course", 2026, [src], official_km=rep.km, official_dplus=rep.dplus)
    assert course is not None and course.points[0].is_aid and "OK" in log[0]
    course, log = resolve("ma course", 2026, [src], official_km=rep.km * 1.2, official_dplus=rep.dplus)
    assert course is None and "rejeté" in log[0]
