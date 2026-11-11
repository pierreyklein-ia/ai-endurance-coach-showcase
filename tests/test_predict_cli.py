"""Chaîne complète : calibrage -> profil JSON -> prévision gelée -> vérification d'empreinte."""
import json
from pathlib import Path

import pytest

from coach.pacer import calibrate, predict
from coach.pacer.athlete_store import AthleteProfile
from coach.pacer.freeze import verify
from tests.synth import training_set

DATA = Path(__file__).resolve().parent.parent / "data"
# profil et trace du pilote : données personnelles, absentes du dépôt git (donc de la CI)
pilot_data = pytest.mark.skipif(not (DATA / "athletes" / "pilote.json").exists() or not list((DATA / "races").glob("*.gpx")),
                                reason="données personnelles du pilote absentes (dépôt git)")


@pilot_data
def test_pilot_profile_reproduces_frozen_prediction(tmp_path):
    out = tmp_path / "p.json"
    assert predict.main(["--sims", "400", "--out", str(out)]) == 0
    d = json.loads(out.read_text())
    assert 34 < d["prediction"]["finish_hours"]["p50"] < 38.5      # gelée : 36,2 h (n=3000)
    assert verify(out)[0]


@pilot_data
def test_tampering_is_detected(tmp_path):
    out = tmp_path / "p.json"
    predict.main(["--sims", "200", "--target-hr", "140", "--out", str(out)])
    d = json.loads(out.read_text()); d["prediction"]["finish_hours"]["p50"] = 30.0
    out.write_text(json.dumps(d))
    assert not verify(out)[0]


def test_calibrate_from_folder(tmp_path):
    from datetime import timedelta

    import gpxpy.gpx
    folder = tmp_path / "acts"; folder.mkdir()
    for tr in training_set()[:12]:
        g = gpxpy.gpx.GPX(); t = gpxpy.gpx.GPXTrack(); g.tracks.append(t)
        s = gpxpy.gpx.GPXTrackSegment(); t.segments.append(s)
        for i in range(len(tr.dist)):
            s.points.append(gpxpy.gpx.GPXTrackPoint(47.26 + tr.dist[i] / 111195.0, -1.28, elevation=float(tr.alt[i]),
                                                    time=tr.start_utc + timedelta(seconds=float(tr.t[i]))))
        (folder / f"{tr.name}.gpx").write_text(g.to_xml())
    prof_path = tmp_path / "moi.json"
    assert calibrate.main(["--activities", str(folder), "--out", str(prof_path), "--hr-rest", "45",
                           "--hr-max", "190", "--race-effort", "3:170", "--race-effort", "12:150"]) == 0
    prof = AthleteProfile.load(prof_path)
    assert 2.5 < prof.model.v_flat < 3.3 and prof.hr_curve.hr(30) < 150
