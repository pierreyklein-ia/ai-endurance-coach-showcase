"""v0.43 : le type de séance couru (footing, tempo, seuil, VMA, côtes) est comparé au type prévu."""
from datetime import timedelta

import numpy as np

from coach.pilot.store import PROFILES_KEY
from coach.session_type import compare, detect, profile_from_streams
from coach.web.auto_validate import match_sessions
from tests.test_pilot import TODAY
from tests.test_v037 import ready
from tests.test_v042_auto import run, sess

LTHR = 170
Y = TODAY - timedelta(days=1)


def streams(blocks, step=1.0, base_speed=3.0):
    """blocks = [(secondes, fc, vitesse m/s, pente)] -> t, fc, distance, altitude (1 Hz)."""
    t, hr, d, a = [], [], [], []
    ti = di = ai = 0.0
    for dur, h, v, slope in blocks:
        for _ in range(int(dur / step)):
            t.append(ti); hr.append(h); d.append(di); a.append(ai)
            ti += step; di += v * step; ai += v * step * slope
    return np.array(t), np.array(hr, float), np.array(d), np.array(a)


def prof(blocks):
    t, h, d, a = streams(blocks)
    return profile_from_streams(t, h, d, a, lthr=LTHR)


WARM = [(900, 130, 3.0, 0)]
EASY = prof([(3000, 135, 3.0, 0)])
TEMPO = prof(WARM + [(1500, 156, 3.6, 0)] + [(600, 130, 3.0, 0)])
SEUIL = prof(WARM + [(600, 166, 4.0, 0), (180, 140, 3.0, 0)] * 3 + [(600, 130, 3.0, 0)])
VMA = prof(WARM + [(30, 150, 5.2, 0), (30, 135, 2.8, 0)] * 12 + [(180, 172, 4.8, 0), (120, 140, 3.0, 0)] * 3
           + [(600, 130, 3.0, 0)])
COTES = prof(WARM + [(90, 166, 2.5, 0.12), (120, 140, 2.5, -0.10)] * 8 + [(600, 130, 3.0, 0)])
LONG = prof([(6000, 138, 3.0, 0)])


def test_detects_each_session_type():
    assert detect(EASY) == "facile"
    assert detect(TEMPO) == "tempo"
    assert detect(SEUIL) == "seuil"
    assert detect(VMA) == "vma"
    assert detect(COTES) == "cotes"
    assert detect(LONG) == "long"
    assert detect(None) == "inconnu"
    no_hr = dict(EASY, zones=None, fast_reps=0)
    assert detect(no_hr) == "inconnu"


def test_short_vma_without_hr_is_seen_on_pace():
    t, _, d, a = streams(WARM + [(30, 0, 5.2, 0), (30, 0, 2.8, 0)] * 12)
    p = profile_from_streams(t, None, d, a, lthr=LTHR)
    assert p["fast_reps"] >= 10 and detect(p) == "vma"


def test_compare_verdicts():
    assert compare("threshold", "seuil")["verdict"] == "conforme"
    assert compare("threshold", "facile")["verdict"] == "plus_facile"
    assert compare("z2", "vma")["verdict"] == "plus_dur"
    assert compare("tempo", "seuil")["ok"]                   # un tempo un peu appuyé reste conforme
    assert compare("hills", "cotes")["ok"] and compare("vo2", "seuil")["verdict"] == "plus_facile"
    assert compare("threshold", "inconnu")["verdict"] == "non_verifie"


def test_threshold_session_run_as_easy_jog_becomes_modified():
    a = run(Y, 55)
    s = [sess("seuil", Y, 55, "threshold")]
    m = match_sessions(s, [a], {}, {}, TODAY, {a.key: EASY})[0]
    assert m["status"] == "modified" and m["type"]["verdict"] == "plus_facile" and m["type"]["label"] == "footing"
    m = match_sessions(s, [a], {}, {}, TODAY, {a.key: SEUIL})[0]
    assert m["status"] == "done" and m["type"]["verdict"] == "conforme"
    m = match_sessions(s, [a], {}, {}, TODAY, {})[0]           # pas de flux : la durée seule décide
    assert m["status"] == "done" and m["type"]["verdict"] == "non_verifie"


def test_profile_is_stored_with_the_activity_and_used_by_auto_validation(tmp_path):
    c = ready(tmp_path)
    store = c.pilot.store
    store.upsert_session(sess("seuil-hier", Y, 55, "threshold"))
    a = run(Y, 55)
    a.profile = EASY
    store.add_activity(a)
    assert store.get(PROFILES_KEY)[a.key]["zones"]
    done = c.service.auto_validate()
    got = next(d for d in done if d["session_id"] == "seuil-hier")
    assert got["status"] == "modified" and got["type"]["verdict"] == "plus_facile"
    w = c.get(f"/api/week?start={Y - timedelta(days=Y.weekday())}").json()
    s = next(x for d in w["days"] for x in d["sessions"] if x["session_id"] == "seuil-hier")
    assert s["feedback"]["status"] == "modified" and s["feedback"]["auto"]["type"]["label"] == "footing"
