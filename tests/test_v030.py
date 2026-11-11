"""v0.30 : accessoires et décors gagnés avec les trophées, décor au choix, prompt d'image explicite."""
from coach import avatar_image as ai
from coach.career import clean_avatar
from tests.test_v025 import long_runner, make


def test_trophy_gear_and_scenes_are_listed_with_how_to_unlock(tmp_path):
    c = make(tmp_path, long_runner())
    c.post("/api/strava/connect")
    r = c.get("/api/career").json()
    ids = {g["id"]: g for g in r["gear"]}
    assert "lunettes" in ids and ids["lunettes"]["how"].startswith("trophée") and ids["bandeau"]["how"] == "niv. 2"
    sc = {s["id"]: s for s in r["scenes"]}
    assert {"route", "foret", "volcan", "bord_de_mer"} <= set(sc) and sc["route"]["unlocked"]
    got = {t["id"] for t in r["trophies"] if t["day"]}
    assert ids["lunettes"]["unlocked"] == ("dist_21.1" in got)


def test_scene_choice_only_if_unlocked(tmp_path):
    c = make(tmp_path, long_runner())
    c.post("/api/strava/connect")
    r = c.get("/api/career").json()
    ok = next(s["id"] for s in r["scenes"] if s["unlocked"])
    locked = [s["id"] for s in r["scenes"] if not s["unlocked"]]
    a = c.post("/api/career/avatar", json={"scene": ok}).json()["avatar"]
    assert a["scene"] == ok and c.get("/api/career").json()["scene"] == ok
    if locked:
        assert c.post("/api/career/avatar", json={"scene": locked[0]}).json()["avatar"]["scene"] is None
    assert clean_avatar({"scene": "volcan", "gear": ["lunettes"]}, 1, {"lunettes"}, {"route"}) == {
        **clean_avatar({}, 1), "scene": None, "gear": ["lunettes"]}


def test_prompt_insists_on_every_accessory():
    p = ai.prompt({"skin": 1}, 3, ["montre", "lunettes"], "volcan", "trail")
    assert "MUST clearly wear ALL" in p and "sunglasses" in p and "GPS sports watch" in p and "volcanic" in p
    assert "No accessories" in ai.prompt({}, 1, [], "route", None)


def test_career_is_cached_until_data_changes(tmp_path, monkeypatch):
    import coach.career as career_mod
    c = make(tmp_path, long_runner())
    c.post("/api/strava/connect")
    calls = []
    real = career_mod.career
    monkeypatch.setattr(career_mod, "career", lambda f: calls.append(1) or real(f))
    c.get("/api/career")
    c.get("/api/career")
    assert len(calls) == 1                                    # v0.31 : pas de recalcul si rien n'a changé
    c.post("/api/career/strength", json={"exercise": "squat", "kg": 100, "reps": 5, "bodyweight": 70})
    c.get("/api/career")
    assert len(calls) == 2
