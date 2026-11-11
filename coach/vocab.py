"""v0.48 : vocabulaire simple ou expert.

Une coureuse de club ne connaît pas forcément « VMA », « seuil », « 30/30 ». En vocabulaire simple (par défaut pour un
nouvel athlète), chaque séance porte un titre en clair et une courte explication des termes techniques (« C'est
quoi ? »), et le coach explique tout terme technique la première fois qu'il l'emploie. Le compte du propriétaire,
coureur expérimenté, reste en vocabulaire expert (déduit de son historique, ou parce qu'il emploie ces termes).
Le vocabulaire ne change que les mots : le plan et la sécurité sont identiques.
"""
from __future__ import annotations

import re
from typing import Literal

Vocabulary = Literal["simple", "expert"]
KEY = "vocabulary"
SET_KEY = "vocabulary_set"                 # choisi par l'athlète (Réglages) : on ne le déduit plus

# terme -> (motif, explication courte en langage courant)
GLOSSARY: list[tuple[str, str, str]] = [
    ("VMA", r"\bVMA\b", "vitesse maximale aérobie : ta plus grande vitesse tenue environ 6 minutes. Une séance de "
                        "VMA, c'est courir vite sur de courtes répétitions, avec de la récupération entre deux."),
    ("Seuil", r"\bseuil\b", "allure soutenue que tu pourrais tenir environ 1 h en course : « confortablement "
                            "difficile », tu peux dire quelques mots mais pas discuter."),
    ("Double seuil", r"double seuil", "deux séances au seuil le même jour (matin et soir), à intensité contrôlée."),
    ("30/30", r"30\s*/\s*30|30 s rapide / 30 s", "30 secondes vite, 30 secondes en trottinant, et on recommence."),
    ("Fractionné", r"fractionn", "alterner des passages rapides et des récupérations en trottinant."),
    ("Footing", r"\bfooting\b", "course tranquille : tu dois pouvoir parler en courant."),
    ("Endurance", r"\bendurance\b", "allure facile, en aisance : tu peux tenir une conversation."),
    ("Lignes droites", r"lignes droites", "accélérations progressives de 15 à 20 secondes, relâché, sans sprinter."),
    ("Sprints en côte", r"sprints? de 10 s en côte|sprints en côte", "8 à 10 secondes très vite en montée, "
                                                                      "récupération en redescendant en marchant."),
    ("Allure marathon", r"allure marathon", "l'allure que tu tiendrais sur un marathon : soutenue mais régulière."),
    ("Tempo", r"\btempo\b", "allure soutenue et régulière, un peu plus lente que le seuil."),
    ("Effort x/10", r"effort \d+(?:-\d+)?/10", "note de difficulté ressentie, de 1 (très facile) à 10 (maximal)."),
    ("Récup", r"\br[ée]cup\b", "récupération : on trottine ou on marche entre deux efforts."),
    ("Échauffement", r"[ée]chauffement", "course très facile au début, pour préparer le corps à l'effort."),
    ("Retour au calme", r"retour au calme", "quelques minutes très faciles à la fin, pour redescendre doucement."),
    ("Sortie longue", r"sortie longue", "la plus longue sortie de la semaine, à allure facile : elle construit "
                                         "l'endurance."),
    ("Gainage", r"gainage|planche", "exercices où l'on tient une position (planche...) pour renforcer le tronc."),
    ("D+", r"\bD\+", "dénivelé positif : le total des montées, en mètres."),
    ("Allure course", r"allure (de )?course", "l'allure que tu prévois de tenir le jour de ta course."),
]

SIMPLE_TITLES = {
    "VMA courte (30/30)": "Fractionné court : 30 s vite / 30 s lent",
    "Seuil": "Allure soutenue (seuil)",
    "Tempo": "Allure soutenue",
    "Footing de récupération": "Footing très facile",
    "Footing": "Footing tranquille",
    "Footing + lignes droites": "Footing tranquille + accélérations",
    "Footing + sprints en côte": "Footing tranquille + sprints en montée",
    "Sortie longue + allure marathon": "Sortie longue, avec un passage plus soutenu",
    "Double seuil (matin)": "Allure soutenue (matin)",
    "Double seuil (soir)": "Allure soutenue (soir)",
    "Côtes": "Montées",
}

# termes qu'emploie un coureur expérimenté (« fractionné » est courant au club : il n'en fait pas partie)
EXPERT_TERMS = re.compile(r"\bVMA\b|\bseuil\b|\btempo\b|\bVO2|\bfartlek\b|\bAS\s?(10|21|42)\b|\bFCmax\b|\bACWR\b|"
                          r"\bz[1-5]\b|allure sp[ée]cifique|\bLT[12]\b|\bhrTSS\b|\bCTL\b|\bTSB\b", re.I)


def terms_in(*texts: str) -> list[dict]:
    """Termes techniques présents dans le titre et le détail d'une séance, avec leur explication."""
    t = " ".join(x for x in texts if x)
    out, seen = [], set()
    for term, pat, text in GLOSSARY:
        if term not in seen and re.search(pat, t, re.I):
            if term == "Seuil" and re.search(r"double seuil", t, re.I) and not re.search(
                    r"(?<!double )seuil", t, re.I):
                continue
            seen.add(term)
            out.append({"term": term, "text": text})
    return out


def simple_title(title: str) -> str:
    return SIMPLE_TITLES.get(title, title)


def infer(store) -> Vocabulary:
    """Pas de choix enregistré : expert pour un coureur expérimenté (bilan : ultras ou ≥ 5 h de course par semaine ;
    le fractionné du club ne suffit pas) ou un compte déjà en place sans bilan (le pilote) ; simple sinon (nouvel athlète)."""
    diag = store.get("diag_full")
    if diag:
        hist = diag.get("history") or {}
        if hist.get("ultras") or (diag.get("run_hours_per_week") or 0) >= 5:
            return "expert"
        return "simple"
    if store.get("onboarded") or store.get("profile"):
        return "expert"
    return "simple"


def get(store) -> Vocabulary:
    v = store.get(KEY)
    return v if v in ("simple", "expert") else infer(store)


def set_(store, v: Vocabulary, chosen: bool = True) -> None:
    store.set(KEY, v)
    if chosen:
        store.set(SET_KEY, True)


def learn_from_text(store, text: str) -> None:
    """L'athlète emploie lui-même « VMA », « seuil »... : vocabulaire expert (sauf s'il a choisi dans Réglages)."""
    t = text or ""
    if re.search(r"c'est quoi|qu'est-ce|[çc]a veut dire|signifi|explique|comprends pas|\?", t, re.I):
        return                                     # « c'est quoi le seuil ? » : justement, elle ne connaît pas
    if not store.get(SET_KEY) and store.get(KEY) != "expert" and EXPERT_TERMS.search(t):
        store.set(KEY, "expert")


def prompt_block(v: Vocabulary) -> str:
    if v != "simple":
        return ""
    return ("\n- Vocabulaire SIMPLE : l'athlète n'est pas spécialiste. La première fois que tu emploies un terme "
            "technique (VMA, seuil, 30/30, fractionné, tempo, allure marathon, ACWR, D+...), explique-le en quelques "
            "mots simples entre parenthèses, par ex. « VMA (vitesse maximale aérobie : courir vite, sur de courtes "
            "répétitions) », « seuil (allure soutenue que tu peux tenir environ 1 h) ». Préfère les mots de tous les "
            "jours. Cela ne change ni le plan, ni les consignes de sécurité.")
