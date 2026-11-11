"""Simulateur de course côté service (GPX privé, ravitos manuels) et profil de course (race_specific) : cas limites."""
from __future__ import annotations

from datetime import date

import pytest

from coach import race_sim as rs
from coach import race_specific as rsp
from coach.onboarding import Profile
from tests.test_v038_sim import RACE, VVX, vvx_client
from tests.test_web import make

GPX_HEAD = '<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'


def gpx(n=10, step=0.0001, ele=True):
    pts = "".join(f'<trkpt lat="{45 + i * step:.6f}" lon="3.0">' + ("<ele>100</ele>" if ele else "") + "</trkpt>"
                  for i in range(n))
    return (GPX_HEAD + pts + "</trkseg></trk></gpx>").encode()


def test_gpx_upload_rejections(tmp_path):
    c = make(tmp_path)
    svc = c.service
    with pytest.raises(ValueError, match="vide"):
        svc.upload_race_gpx(b"")
    with pytest.raises(ValueError, match="pas un fichier GPX"):
        svc.upload_race_gpx(b"PK\x03\x04 zip")
    with pytest.raises(LookupError):
        svc.upload_race_gpx(gpx())                                     # pas d'objectif principal
    svc.store.set("profile", Profile(goal_name="Trail du Morvan", goal_date=date(2027, 6, 12),
                                     goal_distance_km=60, goal_dplus_m=1800).model_dump(mode="json"))
    with pytest.raises(ValueError, match="illisible"):
        svc.upload_race_gpx(b"<?xml version='1.0'?><gpx><trk><trkseg><trkpt lat='x'")
    with pytest.raises(ValueError, match="trop court"):
        svc.upload_race_gpx(gpx(10))
    assert not list((tmp_path / "private").glob("*.tmp")) and not list((tmp_path / "private").glob("tmp*.gpx"))


def test_race_points_rules(tmp_path):
    c = make(tmp_path)
    with pytest.raises(LookupError):
        c.service.set_race_points([{"name": "R", "km": 5}])
    (tmp_path / "vvx").mkdir()
    c2 = vvx_client(tmp_path / "vvx")
    with pytest.raises(ValueError, match="déjà connus"):
        c2.service.set_race_points([{"name": "R", "km": 5}])


def test_sim_without_distance_and_with_corrupted_gpx(tmp_path, monkeypatch):
    c = make(tmp_path)
    c.service.store.set("profile", Profile(goal_name="Course mystère", goal_date=date(2027, 6, 12)).model_dump(mode="json"))
    b = c.get("/api/race/sim").json()
    assert not b["available"] and b["need"] == "distance"
    (tmp_path / "vvx").mkdir()
    c2 = vvx_client(tmp_path / "vvx")
    key = rs.goal_key(VVX, RACE)
    path = c2.service._gpx_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("pas un gpx")
    c2.service.store.set("race_gpx", {"key": key, "name": "x.gpx", "uploaded": "2026-10-01T10:00:00", "km": 85,
                                      "dplus": 2750, "issues": [], "warning": ""})
    b = c2.get("/api/race/sim").json()
    assert b["available"] and b["mode"] == "approx" and "illisible" in b["gpx_error"]


def test_progress_estimate_failure_is_logged_not_raised(tmp_path, monkeypatch):
    c = vvx_client(tmp_path)
    import coach.progress as pg
    monkeypatch.setattr(pg, "estimates_at", lambda *a: (_ for _ in ()).throw(ValueError("x")))
    assert c.service._progress_estimate({"km": 85, "dplus": 2750}) is None


def test_shape_for_edges(monkeypatch):
    assert rsp.shape_for("Marathon", None, 42.2, 0, kind="route") is None
    assert rsp.shape_for("Course inconnue", None, None, None) is None
    sh = rsp.shape_for("Course inconnue", None, 30, 900)
    assert sh.dpk == 30 and sh.climb is None and rsp._climb_txt(sh) == "comme les montées de Course inconnue"
    assert rsp._vam_txt(sh) == "" and rsp._pace(None) is None and rsp._pace(355.4) == "5:55"
    full = rsp.shape_for(VVX, RACE, 85, 2750)
    assert full.short == "VVX" and full.race_pace and full.climb
    assert "comme la montée" in rsp._climb_txt(full) and "m/h" in rsp._vam_txt(full)
    monkeypatch.setattr(rs, "build_course", lambda meta: (_ for _ in ()).throw(RuntimeError("profil")))
    broken = rsp.shape_for(VVX, RACE, 85, 2750)
    assert broken.climb is None and broken.race_h is None
    monkeypatch.setattr(rs, "find_course", lambda *a: (_ for _ in ()).throw(OSError("catalogue")))
    assert rsp.shape_for(VVX, RACE, 85, 2750).name == VVX                 # repli sur km et D+
