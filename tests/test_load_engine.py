import numpy as np

from coach.load_engine import clean_hr, hr_tss, pmc, session_load, time_in_zones
from coach.schemas import Route
from coach.triage import triage


def test_spike_is_removed():
    hr = np.full(600, 140.0)
    hr[300] = 215                   # pic artefact
    res = clean_hr(hr)
    assert res.rejected_mask[300]
    assert abs(res.hr[300] - 140) < 1


def test_cadence_lock_detected():
    hr = np.full(300, 150.0)
    cad = np.full(300, 172.0)
    hr[100:200] = 172               # la FC "colle" à la cadence pendant 100 s
    res = clean_hr(hr, cadence_spm=cad)
    assert res.cadence_lock_s >= 60
    assert res.quality < 0.8


def test_one_hour_at_threshold_is_100_hrtss():
    hr = np.full(3600, 165.0)
    assert abs(hr_tss(hr, hr_rest=50, hr_max=190, lthr=165) - 100) < 0.5


def test_bad_hr_falls_back_to_srpe():
    hr = np.full(3600, 250.0)       # capteur HS
    load, src = session_load(hr, hr_rest=50, hr_max=190, lthr=165,
                             duration_min=60, rpe=6, srpe_to_tss=0.2)
    assert src == "sRPE_fallback" and abs(load - 72) < 1e-6


def test_zones_sum_to_duration():
    hr = np.linspace(120, 180, 3600)
    assert abs(sum(time_in_zones(hr, lthr=165)) - 60) < 0.1


def test_pmc_steady_state():
    states = pmc([80.0] * 300)
    assert abs(states[-1].ctl - 80) < 0.5 and abs(states[-1].acwr - 1) < 0.01


def test_triage_never_drops_life_fatigue():
    assert triage("Ma fille a été malade toute la nuit, je suis épuisé") != Route.NOISE
    assert triage("ok") == Route.NOISE
    assert triage("super merci à toi 🙏") == Route.NOISE
    assert triage("pas bien") == Route.COACHING
    assert triage("oui mais j'ai mal au genou") == Route.RED_FLAG
