import numpy as np

from coach.pacer.calibration import EffortModel
from coach.pacer.strava_io import track_from_strava
from coach.pacer.tracks import _still_mask, segment


def test_strava_streams_with_missing_hr():
    tr = track_from_strava({"time": [0, 1, 2], "distance": [0, 2, 4], "altitude": [10, 10, 11],
                            "heart_rate": [120, None, 122]})
    assert np.isnan(tr.hr[1]) and tr.dist[-1] == 4


def test_quantized_distance_is_not_a_stop():
    """Distance mise à jour toutes les 3 s (cas Suunto réel) : ce n'est PAS un arrêt."""
    t = np.arange(0, 600, 1.0)
    d = np.floor(t / 3) * 3 * 0.8                 # 0,8 m/s, marches de 3 s
    assert _still_mask(t, d, 0.15, 30).mean() < 0.05
    seg = segment(type("T", (), {"dist": d, "alt": np.zeros_like(d), "t": t, "hr": None,
                                 "lat": None, "lon": None})())
    assert abs(seg.dt[seg.moving].sum() - 600) < 20


def test_effort_factor_monotonic():
    e = EffortModel(gamma=0.8, hr_ref=132, resid_sd=0.04)
    assert e.factor(150)[0] > 1 > e.factor(120)[0]


def test_hr_duration_curve_decreases_with_duration():
    from coach.pacer.calibration import fit_hr_duration
    c = fit_hr_duration([(3.3, 176.8), (13.1, 155.8)], hr_rest=40, hr_max=199)
    assert abs(c.hr(13.1) - 155.8) < 0.1 and c.hr(35) < c.hr(13.1)
    assert 0.5 < c.hrr_pct(35) < 0.75
