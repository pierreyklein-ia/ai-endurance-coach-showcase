import json

import numpy as np

from coach.pacer.freeze import backtest, freeze
from coach.pacer.model import Checkpoint, RaceAssumptions, fit_athlete, prior_speed, simulate
from coach.pacer.tracks import elevation_gain, segment
from tests.synth import RACE_START, TRUE, diag_like_course, training_set


def _fit():
    return fit_athlete([segment(t) for t in training_set()])


def test_parameters_are_recovered():
    m = _fit()
    assert abs(m.v_flat / TRUE["v_flat"] - 1) < 0.05
    assert abs(m.beta - TRUE["beta"]) < 0.03


def test_stops_are_removed_from_moving_speed():
    seg = segment(training_set()[0])
    assert seg.speed[seg.moving].min() > 0.25


def test_prediction_covers_truth_on_diag_like_course():
    m = _fit()
    course = segment(diag_like_course())
    stops = [Checkpoint("mi-course", 90, stop_min=30, stop_sd_min=10),
             Checkpoint("arrivée", course.x0[-1] / 1000 + 0.1)]
    no_noise = RaceAssumptions(night_factor=(1.0, 0.0), terrain_factor=(1.0, 0.0))
    pred = simulate(course, m, RACE_START, stops, no_noise, n=400)

    # Vérité : paramètres vrais, sans bruit
    t = 0.0
    for dx, g in zip(course.dx, course.grade):
        v = prior_speed(np.array([g]), TRUE["v_flat"], TRUE["down_cap"])[0]
        t += dx / (v * (1 + t / 3600) ** (-TRUE["beta"]))
    truth_h = t / 3600 + 0.5
    f = pred.finish_hours
    assert f["p10"] <= truth_h <= f["p90"]
    assert abs(f["p50"] / truth_h - 1) < 0.08
    assert elevation_gain(course) > 5000


def test_night_slows_down():
    m = _fit()
    course = segment(diag_like_course())
    cps = [Checkpoint("arrivée", course.x0[-1] / 1000 + 0.1)]
    day = simulate(course, m, RACE_START, cps, RaceAssumptions(night_factor=(1.0, 0.0)), n=200)
    night = simulate(course, m, RACE_START, cps, RaceAssumptions(night_factor=(0.85, 0.0)), n=200)
    assert night.finish_hours["p50"] > day.finish_hours["p50"]
    assert 0.2 < night.moving_share_night < 0.7     # départ 22 h, course de ~2 nuits


def test_freeze_and_backtest(tmp_path):
    m = _fit()
    course = segment(diag_like_course())
    cps = [Checkpoint("km90", 90), Checkpoint("arrivée", course.x0[-1] / 1000 + 0.1)]
    pred = simulate(course, m, RACE_START, cps, n=200)
    frozen = freeze(pred, m, RaceAssumptions(), course, "test", tmp_path / "p.json")
    assert len(frozen["sha256"]) == 64
    reloaded = json.loads((tmp_path / "p.json").read_text())
    bt = backtest(reloaded, {"km90": reloaded["prediction"]["checkpoints"][0]["p50"]})
    assert bt["rows"][0]["err_pct"] == 0.0 and bt["coverage_p10_p90"] == 1.0
