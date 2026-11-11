"""v0.20 : profil du coureur (radar)."""
from datetime import timedelta

from coach.pilot.store import PilotStore
from coach.pilot.strava_api import durability_backfill, record_decoupling
from coach.radar import decoupling, runner_profile
from tests.test_onboarding import TODAY, act


def streams(minutes=120, drift=0.0, speed=3.0, hr=140.0, hills=False):
    t = list(range(0, minutes * 60 + 1, 5))
    d, alt, h = [], [], []
    for s in t:
        frac = s / t[-1]
        d.append(speed * s)
        alt.append(100 + (50 if hills and (s // 600) % 2 else 0) * ((s % 600) / 600))
        h.append(hr * (1 + drift * frac))
    return {"time": t, "heart_rate": h, "distance": d, "altitude": alt}


def test_decoupling_flat_vs_drift():
    assert abs(decoupling(streams())) < 0.5
    dec = decoupling(streams(drift=0.08))
    assert 3 < dec < 6                       # la FC monte de 8 % sur la sortie : ~4 % entre les deux moitiés


def test_decoupling_needs_90_min_and_distance():
    assert decoupling(streams(minutes=60)) is None
    s = streams(); del s["distance"]
    assert decoupling(s) is None


def history(today=TODAY, race_long=True):
    acts = []
    for w in range(52):
        d = today - timedelta(days=7 * w + 1)
        acts += [act(d, 10, 55), act(d - timedelta(days=2), 12, 66), act(d - timedelta(days=4), 20, 120, name="Sortie longue")]
        if w % 2 == 0:
            acts.append(act(d - timedelta(days=3), 0, 40, sport="Renfo", name="Renfo"))
    acts.append(act(today - timedelta(days=40), 10, 42, name="10 km de Rennes"))
    if race_long:
        acts.append(act(today - timedelta(days=60), 21.1, 95, name="Semi de Paris"))
    return acts


def test_profile_axes_and_not_measured():
    pr = runner_profile(history(), TODAY, sex="M", dec_items=[{"day": TODAY - timedelta(days=10), "dec": 4.0}])
    axes = {a["key"]: a for a in pr["axes"]}
    assert set(axes) == {"puissance", "endurance_vitesse", "durabilite", "caisse", "robustesse", "specificite", "force"}
    assert {m["key"] for m in pr["not_measured"]} == {"economie", "metabolisme"}
    assert 4 < axes["puissance"]["score"] < 7                 # 10 km en 42 min : VMA ~16,4 km/h
    assert axes["endurance_vitesse"]["score"] is not None and "Semi" in axes["endurance_vitesse"]["basis"]
    assert axes["durabilite"]["score"] == 8.0
    assert axes["force"]["score"] is not None
    assert all(a["score"] is None or 0 <= a["score"] <= 10 for a in pr["axes"])


def test_speed_endurance_ignores_easy_long_runs():
    """Une sortie longue d'entraînement à allure facile ne mesure pas l'endurance de vitesse : « à mesurer »."""
    pr = runner_profile(history(race_long=False), TODAY)
    ax = next(a for a in pr["axes"] if a["key"] == "endurance_vitesse")
    assert ax["score"] is None and ax["confidence"] == "à mesurer" and "semi" in ax["tip"].lower()


def test_no_data_everything_to_measure():
    pr = runner_profile([], TODAY)
    assert all(a["score"] is None for a in pr["axes"]) and pr["weakest"] is None


class FakeAPI:
    def __init__(self, acts, s):
        self.acts, self.s, self.calls = acts, s, []

    def get(self, path, params=None):
        self.calls.append(path)
        return self.acts if path == "/athlete/activities" else self.s


def raw(i, minutes, day):
    return {"id": i, "name": f"Longue {i}", "moving_time": minutes * 60, "has_heartrate": True, "sport_type": "Run",
            "start_date": f"{day}T07:00:00Z", "start_date_local": f"{day}T08:00:00"}


def strava_streams(s):
    return {k2: {"data": s[k]} for k, k2 in (("time", "time"), ("heart_rate", "heartrate"),
                                              ("distance", "distance"), ("altitude", "altitude"))}


def test_durability_backfill_once(tmp_path):
    store = PilotStore(tmp_path / "p.db")
    days = [(TODAY - timedelta(days=k)).isoformat() for k in (5, 12, 20)]
    api = FakeAPI([raw(1, 120, days[0]), raw(2, 60, days[1]), raw(3, 130, days[2])], strava_streams(streams(drift=0.06)))
    n = durability_backfill(store, api, TODAY)
    assert n == 2 and store.get("decoupling_backfilled") is True       # la sortie de 60 min est ignorée
    items = store.get("decoupling")
    assert set(items) == {"1", "3"} and all(2 < x["dec"] < 5 for x in items.values())
    record_decoupling(store, raw(4, 30, days[0]), streams())           # trop courte : rien
    assert "4" not in store.get("decoupling")
