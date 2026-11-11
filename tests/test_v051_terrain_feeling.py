"""v0.51 : pente et vent dans les consignes et la base du coach ; ressenti de fin de semaine."""
from datetime import timedelta

from coach.hr_zones import HrZones, target
from coach.knowledge import cards, tags_for
from tests.test_pilot import TODAY
from tests.test_v037 import ready

W0 = TODAY - timedelta(days=TODAY.weekday())


def test_threshold_target_mentions_slope_and_wind():
    z = HrZones(190, 45, 180, {}, {})
    t = target("threshold", "20 min d'échauffement, 3 × 8 min au seuil, récup 2 min, 15 min de retour au calme.", z)
    assert "vent de face" in t["note"] and "FC" in t["note"]
    assert t["hr"] == [171, 180]
    assert "vent" not in target("z2", "Footing", z)["note"]


def test_terrain_cards_are_found_from_the_athlete_words():
    tags = tags_for("La deuxième répétition était en montée avec le vent de face")
    assert "terrain" in tags
    ids = {c["id"] for c in cards(tags | {"threshold"}, k=4)}
    assert {"kb_pente_minetti2002", "kb_vent_pugh1971"} <= ids


def test_week_feeling_is_saved_told_to_the_coach_and_shown(tmp_path):
    c = ready(tmp_path)
    r = c.post("/api/recap/feeling", json={"week_start": W0.isoformat(), "fatigue": 4, "difficulty": 3, "fun": 4,
                                            "note": "jambes lourdes jeudi"})
    assert r.status_code == 200, r.text
    assert r.json()["fatigue"] == 4
    assert c.service.wait_coach(20)
    msgs = c.get("/api/chat").json()["messages"]
    line = next(m for m in msgs if m["role"] == "user" and "Ressenti de ma semaine" in m["text"])
    assert "fatigue 4/5" in line["text"] and "jambes lourdes" in line["text"]
    assert msgs[-1]["role"] == "coach"
    v = c.get("/api/recap").json()
    assert (v["preview"] or {}).get("feeling", {}).get("fun") == 4
    assert "Ressenti de l'athlète" in c.pilot.repo.get("a").profile_summary
    # correction : un seul ressenti par semaine, marqué « corrigé » dans le chat
    c.post("/api/recap/feeling", json={"week_start": W0.isoformat(), "fatigue": 2, "difficulty": 2, "fun": 5})
    assert c.service.wait_coach(20)
    assert c.pilot.store.get("weekly_feelings")[W0.isoformat()]["fatigue"] == 2
    assert any("Ressenti corrigé" in m["text"] for m in c.get("/api/chat").json()["messages"])


def test_week_feeling_validation(tmp_path):
    c = ready(tmp_path)
    assert c.post("/api/recap/feeling", json={"week_start": W0.isoformat(), "fatigue": 6, "difficulty": 3,
                                              "fun": 4}).status_code == 422
    assert c.post("/api/recap/feeling", json={"week_start": "hier", "fatigue": 3, "difficulty": 3,
                                              "fun": 4}).status_code == 422
    try:
        c.service.set_week_feeling(W0.isoformat(), 0, 3, 3)
        raise AssertionError("valeur hors bornes acceptée")
    except ValueError as e:
        assert "fatigue" in str(e)
