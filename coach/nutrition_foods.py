"""v0.38 — Palette des aliments de course (valeurs TYPIQUES par portion, à vérifier sur l'étiquette).

Sert à trois choses :
- le bilan nutrition des sorties ≥ 1 h 30 (l'athlète coche ce qu'il a pris, on calcule les g/h) ;
- les conseils du coach quand ça s'est mal passé (alternatives plus digestes, plus salées…) ;
- le plan nutrition de course du simulateur (ce qu'il faut prendre entre deux ravitos).

Les valeurs sont des ordres de grandeur pour une portion courante ; les marques varient beaucoup
(un gel va de 20 à 45 g de glucides). L'athlète peut corriger la quantité de glucides d'un produit.

Repères (synthèse, voir les fiches nutrition de `knowledge.py`) : 30-60 g/h sous 2-2 h 30,
60-90 g/h au-delà, 90-120 g/h seulement si l'intestin a été entraîné, jamais plus de 120 g/h ;
au-delà de ~60 g/h, mélanger glucose (maltodextrine) et fructose ; sodium 300-600 mg par litre bu,
plus par forte chaleur ou grosse sudation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

# familles : gel, boisson, sucre (solide sucré), reel (aliment « normal »), sale, sel
# digest : "rapide" (sucres simples, peu de fibres/gras), "moyen", "lent" (gras, protéines, fibres)


@dataclass(frozen=True)
class Food:
    id: str
    name: str
    family: str
    portion: str
    carbs_g: float
    sodium_mg: float = 0.0
    fluid_ml: float = 0.0
    caffeine_mg: float = 0.0
    fructose: bool = False         # contient du fructose (utile au-delà de 60 g/h)
    digest: str = "rapide"
    note: str = ""

    def view(self) -> dict:
        return asdict(self)


FOODS: tuple[Food, ...] = (
    # gels
    Food("gel", "Gel énergétique", "gel", "1 gel (~30-40 g)", 25, 50, fructose=True),
    Food("gel_hd", "Gel haute dose (maltodextrine + fructose)", "gel", "1 gel", 40, 60, fructose=True),
    Food("gel_cafe", "Gel à la caféine", "gel", "1 gel", 25, 50, caffeine_mg=75, fructose=True,
         note="plutôt en fin de course ou la nuit ; pas plus de ~3 mg/kg sur la course"),
    Food("gommes", "Gommes / cubes énergétiques", "gel", "3 gommes", 24, 50, fructose=True),
    Food("miel", "Miel (dosette)", "gel", "1 dosette de 20 g", 16, 0, fructose=True),
    # boissons
    Food("iso", "Boisson isotonique (6-8 %)", "boisson", "1 flasque de 500 ml", 30, 250, 500, fructose=True),
    Food("iso_hd", "Boisson énergétique haute dose (~80 g/L)", "boisson", "1 flasque de 500 ml", 40, 300, 500,
         fructose=True, note="à tester à l'entraînement : concentrée, elle peut écœurer"),
    Food("eau", "Eau", "boisson", "500 ml", 0, 0, 500),
    Food("cola", "Cola", "boisson", "1 verre de 250 ml", 26, 10, 250, caffeine_mg=25, fructose=True,
         note="dégazé si possible ; très apprécié en fin d'ultra"),
    Food("soupe", "Soupe / bouillon salé", "boisson", "1 bol de 250 ml", 5, 800, 250, digest="moyen",
         note="chaud et salé : idéal la nuit et quand le sucré ne passe plus"),
    Food("the_sucre", "Thé ou café sucré", "boisson", "1 tasse", 10, 0, 200),
    # solides sucrés
    Food("compote", "Compote à boire", "sucre", "1 gourde de 90 g", 13, 0, fructose=True),
    Food("compote_sport", "Compote énergétique (sport)", "sucre", "1 gourde", 25, 50, fructose=True),
    Food("pate_fruits", "Pâte de fruits", "sucre", "1 pièce (~25 g)", 18, 0, fructose=True),
    Food("barre", "Barre énergétique", "sucre", "1 barre (~40 g)", 26, 80, fructose=True, digest="moyen"),
    Food("barre_cereales", "Barre de céréales", "sucre", "1 barre (~25 g)", 16, 50, digest="moyen"),
    Food("pain_epices", "Pain d'épices", "sucre", "1 tranche (~30 g)", 21, 60, fructose=True),
    Food("quatre_quarts", "Quatre-quarts / gâteau", "sucre", "1 part (~30 g)", 15, 80, digest="lent",
         note="gras : à garder pour les allures lentes"),
    Food("banane", "Banane", "sucre", "1 banane moyenne", 25, 0, fructose=True, digest="moyen"),
    Food("fruits_secs", "Fruits secs (abricots, dattes, raisins)", "sucre", "1 poignée (~40 g)", 25, 10,
         fructose=True, digest="moyen", note="fibres : à tester, peut accélérer le transit"),
    Food("bonbons", "Bonbons", "sucre", "5 bonbons (~25 g)", 20, 0, fructose=True),
    # aliments « normaux »
    Food("sandwich", "Demi-sandwich (jambon, fromage)", "reel", "1/2 sandwich", 30, 700, digest="lent",
         note="aux bases de vie ou à allure lente"),
    Food("patate", "Pommes de terre salées", "reel", "100 g", 17, 300, digest="moyen"),
    Food("puree", "Purée / patate douce en gourde", "reel", "1 gourde (~80 g)", 15, 150, digest="moyen"),
    Food("riz", "Riz / semoule / pâtes", "reel", "1 petit bol", 30, 200, digest="moyen"),
    # salé
    Food("fromage", "Fromage", "sale", "1 portion (~30 g)", 0, 200, digest="lent"),
    Food("charcuterie", "Jambon / saucisson", "sale", "2-3 tranches", 0, 400, digest="lent"),
    Food("biscuits_sales", "Biscuits salés / crackers", "sale", "1 poignée (~30 g)", 18, 300, digest="moyen"),
    Food("chips", "Chips", "sale", "1 petit paquet (~30 g)", 15, 180, digest="lent"),
    # sel
    Food("sel", "Capsule / pastille de sel", "sel", "1 capsule", 0, 220),
)

BY_ID = {f.id: f for f in FOODS}
FAMILY_LABELS = {"gel": "Gels et gommes", "boisson": "Boissons", "sucre": "Solides sucrés",
                 "reel": "Aliments", "sale": "Salé", "sel": "Sel"}


def catalog() -> list[dict]:
    """La palette, pour l'interface (fiche de retour, plan de course)."""
    return [f.view() for f in FOODS]


def totals(items: list[dict], hours: float) -> dict:
    """`items` = [{"id": "gel", "qty": 3, "carbs_g": 22?}, …] -> glucides, sodium, liquide (totaux et /h).
    `carbs_g` optionnel : l'athlète corrige la valeur de SON produit."""
    c = na = ml = caf = 0.0
    fructose = False
    for it in items or []:
        f = BY_ID.get(str(it.get("id")))
        q = max(0.0, float(it.get("qty") or 0))
        if not f or q <= 0:
            continue
        c += q * float(it.get("carbs_g") or f.carbs_g)
        na += q * f.sodium_mg
        ml += q * f.fluid_ml
        caf += q * f.caffeine_mg
        fructose = fructose or f.fructose
    h = max(hours, 0.25)
    return {"carbs_g": round(c), "carbs_g_h": round(c / h), "sodium_mg": round(na), "sodium_mg_h": round(na / h),
            "fluid_ml": round(ml), "fluid_ml_h": round(ml / h), "caffeine_mg": round(caf), "fructose": fructose}


def target_carbs_g_h(duration_h: float, trained_max: float | None = None) -> tuple[int, int]:
    """Fourchette de glucides par heure selon la durée de l'effort, plafonnée à ce que l'athlète
    a déjà toléré à l'entraînement (+10 g/h au plus) et à 120 g/h."""
    lo, hi = (30, 60) if duration_h < 2.5 else (60, 90)
    if trained_max:
        tm = int(trained_max)
        hi = min(max(hi, tm), tm + 10)
        lo = min(lo, hi)
    return lo, min(hi, 120)
