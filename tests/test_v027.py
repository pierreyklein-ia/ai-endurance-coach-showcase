"""v0.27 : fiches de la veille de novembre, nouveaux thèmes."""
from coach.knowledge import KNOWLEDGE, cards, tags_for


def top(q):
    return [c["id"] for c in cards(tags_for(q))]


def test_cards_have_unique_ids():
    ids = [e.id for e in KNOWLEDGE]
    assert len(ids) == 53 and len(set(ids)) == 53          # v0.38 : + 9 fiches nutrition ; v0.51 : + 3 terrain


def test_carbs_question_brings_morton():
    assert "kb_nutrition_morton2026" in top("combien de glucides par heure sur un ultra ?")


def test_hrv_question_brings_schaffarczyk():
    assert top("ma VFC est basse ce matin")[0] == "kb_vfc_schaffarczyk2026"


def test_new_themes_detected():
    assert "durability" in tags_for("je m'effondre en fin de course")
    assert "heat" in tags_for("avec la canicule je dois prendre du sel")
    assert "hrv" in tags_for("mon HRV chute")


def test_morton_card_caps_at_120():
    t = next(e.text for e in KNOWLEDGE if e.id == "kb_nutrition_morton2026")
    assert "30-60" in t and "60-90" in t and "90-120" in t and "jamais plus de 120" in t
