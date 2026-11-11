"""Race Pacer : prévision complète sur un catalogue synthétique (racine injectée), calibration et lecture Strava."""
from __future__ import annotations

import json

import gpxpy.gpx
import numpy as np
import pytest

from coach.pacer import calibration as cal
from coach.pacer import predict
from coach.pacer.athlete_store import AthleteProfile
from coach.pacer.course import load_course_gpx, validate
from coach.pacer.freeze import verify
from coach.pacer.model import fit_athlete
from coach.pacer.strava_io import load_strava_json, track_from_strava
from coach.pacer.tracks import segment
from tests.synth import training_set


@pytest.fixture(scope="module")
def segs():
    return [segment(tr) for tr in training_set()[:6]]


@pytest.fixture(scope="module")
def model(segs):
    return fit_athlete(segs)


def _catalog(root, model):
    races = root / "data" / "races"
    races.mkdir(parents=True)
    g = gpxpy.gpx.GPX()
    t = gpxpy.gpx.GPXTrack()
    g.tracks.append(t)
    s = gpxpy.gpx.GPXTrackSegment()
    t.segments.append(s)
    for i in range(1201):                                       # ~12 km, quelques bosses
        s.points.append(gpxpy.gpx.GPXTrackPoint(-21 + i * 0.00009, 55.4, elevation=100 + 40 * np.sin(i / 60)))
    (races / "mini.gpx").write_text(g.to_xml())
    rep = validate(load_course_gpx(races / "mini.gpx"), 1, 1)
    meta = {"race": "Mini Trail", "year": 2026, "gpx_file": "mini.gpx", "official_km": round(rep.km, 2),
            "official_dplus": round(rep.dplus), "start_local": "2026-11-15T09:00:00+01:00",
            "points": [{"name": "Ravito", "km": rep.km / 2, "services": ["ravito"]},
                       {"name": "Arrivée", "km": rep.km}]}
    (races / "mini-trail-2026.json").write_text(json.dumps(meta))
    ath = root / "data" / "athletes"
    ath.mkdir(parents=True)
    AthleteProfile(model, cal.EffortModel(0.5, 140.0, 0.05), 45, 190, [(3.0, 170.0), (12.0, 150.0)],
                   default_stop_min=2.0).save(ath / "pilote.json")
    return meta


def test_predict_main_on_injected_catalog(tmp_path, model, capsys):
    _catalog(tmp_path, model)
    out = tmp_path / "p.json"
    assert predict.main(["--race", "mini trail", "--sims", "150", "--out", str(out)], root=tmp_path) == 0
    assert "FC tenable" in capsys.readouterr().out                 # FC résolue en point fixe
    d = json.loads(out.read_text())
    assert 0.5 < d["prediction"]["finish_hours"]["p50"] < 4 and verify(out)[0]
    assert predict.main(["--race", "mini trail", "--sims", "100", "--target-hr", "150", "--stop-min", "1",
                         "--out", str(out)], root=tmp_path) == 0


def test_predict_main_missing_or_rejected_course(tmp_path, model, capsys):
    meta = _catalog(tmp_path, model)
    assert predict.main(["--race", "inconnue"], root=tmp_path) == 1
    meta["official_km"] *= 2                                       # chiffres officiels incompatibles : rejet
    (tmp_path / "data" / "races" / "mini-trail-2026.json").write_text(json.dumps(meta))
    assert predict.main(["--race", "mini trail"], root=tmp_path) == 1
    assert "rejeté" in capsys.readouterr().out


def test_effort_fit_and_leave_one_out(segs, model):
    s = segs[0]
    assert cal.mean_hr(s) == pytest.approx(140.0) and cal.moving_hours(s) > 0.2
    c = cal.as_course(s)
    assert c.lat[0] == -21.0 and len(c.lat) == len(s.dx)
    h = cal.predict_hours(model, s, n=50)
    assert h["p10"] <= h["p50"] <= h["p90"]
    e = cal.fit_effort(segs[:3], model)
    assert e.hr_ref == pytest.approx(140.0) and e.resid_sd >= 0
    res = cal.loo_backtest({f"a{i}": x for i, x in enumerate(segs[:5])})
    assert len(res) == 5 and {"err", "covered", "actual_h"} <= set(res[0])


def test_strava_streams_validation_and_json(tmp_path):
    with pytest.raises(ValueError, match="altitude"):
        track_from_strava({"time": [0, 1], "distance": [0, 1]})
    tr = track_from_strava({"time": [0, 10, 20], "distance": [0, 30, 60], "altitude": [5, 6, 7]})
    assert tr.hr is None
    f = tmp_path / "sortie.json"
    f.write_text(json.dumps({"time": [0, 10], "distance": [0, 30], "altitude": [1, 2], "heart_rate": [None, 120]}))
    tr = load_strava_json(f)
    assert tr.name == "sortie" and np.isnan(tr.hr[0])
