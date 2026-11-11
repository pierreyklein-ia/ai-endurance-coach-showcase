"""v0.47 : décor de l'avatar en deux choix qui se croisent — lieu × ambiance (montagne + neige, mer + nuit…)."""
from datetime import date

from coach import avatar_image as ai
from coach.career import (
    AMBIANCES,
    LEVEL_SCENES,
    PLACES,
    TROPHY_SCENES,
    Facts,
    career,
    clean_avatar,
    resolve_scene,
    split_scene,
)
from tests.test_career import act
from tests.test_v025 import long_runner, make

NOV = date(2026, 11, 2)
ALL = set(PLACES) | set(AMBIANCES)


def test_every_old_scene_lands_on_exactly_one_axis_with_its_unlock_rule_unchanged():
    old = [i for _, i in LEVEL_SCENES] + [i for _, i in TROPHY_SCENES]
    assert set(old) == ALL and not set(PLACES) & set(AMBIANCES)
    assert {i: lv for lv, i in LEVEL_SCENES}["hiver"] == 9 and {i: t for t, i in TROPHY_SCENES}["neige"] == "dplus_2500"


def test_migration_of_old_single_scene():
    assert split_scene({"scene": "hiver"}) == (None, "hiver")            # ambiance : lieu auto + cette ambiance
    assert split_scene({"scene": "montagne"}) == ("montagne", None)      # lieu : ambiance auto
    assert split_scene({"scene": None}) == (None, None)
    assert split_scene({"scene": "bizarre"}) == (None, None)
    a = clean_avatar({"scene": "hiver"}, 9)
    assert a["place"] is None and a["ambiance"] == "hiver" and a["scene"] == "hiver"
    a = clean_avatar({"scene": "montagne"}, 9)
    assert a["place"] == "montagne" and a["ambiance"] is None and a["scene"] == "montagne"
    # lieu auto = meilleur lieu débloqué par niveau
    r = resolve_scene({"scene": "hiver"}, 9, NOV, ALL)
    assert r == {"place": "montagne", "ambiance": "hiver", "scene": "hiver"}
    assert resolve_scene({"scene": "hiver"}, 13, NOV, ALL)["place"] == "sommet"


def test_old_client_changing_scene_wins_over_stale_place_and_ambiance():
    stored = clean_avatar({"place": "montagne", "ambiance": "neige"}, 9, None, ALL)
    assert stored["scene"] == "montagne"
    assert split_scene(stored) == ("montagne", "neige")
    assert split_scene({**stored, "scene": "foret"}) == ("foret", None)
    assert split_scene({**stored, "scene": None}) == (None, None)


def test_auto_keeps_season_on_best_place():
    assert resolve_scene({}, 1, NOV, {"route"}) == {"place": "route", "ambiance": "aucune", "scene": "route"}
    assert resolve_scene({}, 7, NOV, None) == {"place": "montagne", "ambiance": "automne", "scene": "automne"}
    assert resolve_scene({}, 7, date(2026, 1, 5), None)["ambiance"] == "aucune"      # hiver pas encore débloqué
    assert resolve_scene({"ambiance": "aucune"}, 7, NOV, None)["scene"] == "montagne"
    c = career(Facts([act(date(2026, 3, 3), 5, 28)], today=NOV))
    assert c["scene"] == "route" and c["place"] == "route" and c["ambiance"] == "aucune"


def test_unlock_rules_per_axis_and_validation():
    # niveau 6 sans trophée : montagne oui, sommet / ville / volcan non ; printemps oui, hiver / neige / nuit non
    a = clean_avatar({"place": "montagne", "ambiance": "printemps"}, 6)
    assert (a["place"], a["ambiance"]) == ("montagne", "printemps")
    for p in ("sommet", "ville", "volcan"):
        assert clean_avatar({"place": p, "ambiance": "printemps"}, 6)["place"] is None
    for x in ("hiver", "neige", "nuit"):
        assert clean_avatar({"place": "montagne", "ambiance": x}, 6)["ambiance"] is None
    # trophées : neige et ville débloqués par trophée, pas par niveau
    a = clean_avatar({"place": "ville", "ambiance": "neige"}, 2, None, {"route", "collines", "ville", "neige"})
    assert (a["place"], a["ambiance"]) == ("ville", "neige")
    # chaque axe n'accepte que ses propres valeurs ; « aucune » toujours permise
    a = clean_avatar({"place": "neige", "ambiance": "montagne"}, 20, None, ALL)
    assert a["place"] is None and a["ambiance"] is None
    assert clean_avatar({"place": "route", "ambiance": "aucune"}, 1)["ambiance"] == "aucune"
    assert clean_avatar({"place": "bord_de_mer", "ambiance": "neige"}, 20, None, ALL)["scene"] == "bord_de_mer"


def test_career_lists_both_axes():
    c = career(Facts([act(date(2026, 3, 3), 5, 28)], today=NOV))
    assert [x["id"] for x in c["places"]] == list(PLACES)
    assert [x["id"] for x in c["ambiances"]] == ["aucune", *AMBIANCES]
    amb = {x["id"]: x for x in c["ambiances"]}
    assert amb["aucune"]["unlocked"] and not amb["neige"]["unlocked"] and amb["neige"]["how"].startswith("trophée")
    assert {x["id"]: x for x in c["places"]}["sommet"]["how"] == "niv. 13"


def test_gemini_prompt_describes_place_and_ambiance_and_cache_key_has_both():
    p = ai.prompt({}, 6, [], "bord_de_mer", "trail", "neige")
    assert "snowy beach" in p and "snow" in p and "sea" in p
    p = ai.prompt({}, 6, [], "montagne", "trail", "nuit")
    assert "mountain trail" in p and "at night" in p and "moon" in p
    p = ai.prompt({}, 6, [], "ville", None, "nuit")
    assert "city street" in p and "lit windows" in p
    p = ai.prompt({}, 6, [], "foret", None, "automne")
    assert "forest" in p and "autumn" in p
    assert "volcanic" in ai.prompt({}, 1, [], "volcan", None)                    # ancien appel, sans ambiance
    assert "winter" in ai.prompt({}, 1, [], "hiver", None)                       # ancien décor unique « hiver »
    k = [ai.key_for({}, 6, [], "montagne", None, None, a) for a in ("neige", "nuit", "aucune")]
    assert len(set(k)) == 3 and ai.key_for({}, 6, [], "montagne", None) != ai.key_for({}, 6, [], "volcan", None)


def test_api_round_trip_place_and_ambiance(tmp_path):
    c = make(tmp_path, long_runner())
    c.post("/api/strava/connect")
    r = c.get("/api/career").json()
    places = [x["id"] for x in r["places"] if x["unlocked"]]
    ambs = [x["id"] for x in r["ambiances"] if x["unlocked"]]
    assert places and ambs[0] == "aucune"
    p, a = places[-1], ambs[0]
    body = {k: v for k, v in r["avatar"].items() if k != "scene"}          # le nouveau client n'envoie pas `scene`
    saved = c.post("/api/career/avatar", json={**body, "place": p, "ambiance": a}).json()["avatar"]
    assert (saved["place"], saved["ambiance"]) == (p, a)
    back = c.get("/api/career").json()
    assert (back["avatar"]["place"], back["avatar"]["ambiance"]) == (p, a)
    assert (back["place"], back["ambiance"], back["scene"]) == (p, a, p)
    # verrouillé : retombe en auto
    locked = [x["id"] for x in r["places"] if not x["unlocked"]]
    if locked:
        assert c.post("/api/career/avatar", json={"place": locked[0]}).json()["avatar"]["place"] is None
    # ancien client : seulement `scene` (un lieu) ; une ambiance seule, via clean_avatar (niveau 9)
    old = c.post("/api/career/avatar", json={"scene": places[0]}).json()["avatar"]
    assert old["place"] == places[0] and old["ambiance"] is None
    assert c.get("/api/career").json()["place"] == places[0]
    a9 = clean_avatar({"scene": "hiver"}, 9)
    assert (a9["place"], a9["ambiance"]) == (None, "hiver")
