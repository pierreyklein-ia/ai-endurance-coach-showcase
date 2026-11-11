"""v0.36 : coach personnalisable (genre, personnalité, prénom, tutoiement / vouvoiement).

La personnalité ne change QUE le ton et la formulation des messages. Les garde-fous, les règles de
sécurité et le plan calculé par le code sont identiques quel que soit le choix : le bloc ajouté aux
prompts le rappelle explicitement au LLM, et le code (guardrails.check) reste le seul juge.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field, field_validator

KEY = "coach_persona"
Gender = Literal["homme", "femme"]
Personality = Literal["bienveillant", "exigeant", "motivateur", "sante"]
Address = Literal["tu", "vous"]
DEFAULT_NAMES = {"femme": "Léa", "homme": "Marc"}

_TONES = {
    "bienveillant": "bienveillant : chaleureux, rassurant, encourageant ; valorise chaque progrès, dédramatise les "
                    "séances manquées.",
    "exigeant": "exigeant : direct, précis, factuel ; attend de la rigueur et la régularité, va droit au but. "
                "Exigeant sur la qualité d'exécution, JAMAIS sur la douleur : on ne pousse jamais à travers une "
                "douleur, une maladie ou une fatigue anormale.",
    "motivateur": "motivateur : énergique, enthousiaste, qui donne envie d'y aller ; formules courtes et "
                  "dynamiques. L'énergie sert à lancer les séances prévues, pas à en ajouter.",
    "sante": "santé d'abord : prudent et attentif aux signaux du corps (sommeil, douleur, récupération) ; explique "
             "pourquoi la récupération compte. Cette prudence porte sur la formulation : elle ne modifie pas le plan "
             "calculé et n'annule pas de séance que le code a validée.",
}


def _clean_name(v: str) -> str:
    v = unicodedata.normalize("NFC", str(v))
    v = re.sub(r"[^\w \-'’]", "", v, flags=re.UNICODE)      # lettres, chiffres, espace, tiret, apostrophe
    v = re.sub(r"\s+", " ", v).strip(" -'’")
    return v[:20].strip()


class CoachPersona(BaseModel):
    gender: Gender = "femme"
    personality: Personality = "bienveillant"
    name: str = Field(default=DEFAULT_NAMES["femme"], min_length=1, max_length=20)
    address: Address = "tu"
    # v0.48 : vocabulaire (simple = termes techniques expliqués). Enregistré à part (clé « vocabulary ») : le choisir
    # ne vaut pas choix du coach ; None = inchangé.
    vocabulary: Literal["simple", "expert"] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = _clean_name(v)
        if not v:
            raise ValueError("prénom du coach vide")
        return v


def load(store) -> CoachPersona:
    raw = store.get(KEY)
    try:
        return CoachPersona.model_validate(raw) if raw else CoachPersona()
    except Exception:                                         # noqa: BLE001 — donnée abîmée : défauts
        return CoachPersona()


def view(store) -> dict:
    from . import vocab
    return {**load(store).model_dump(), "chosen": store.get(KEY) is not None, "vocabulary": vocab.get(store)}


def save(store, p: CoachPersona) -> dict:
    from . import vocab
    if p.vocabulary:
        vocab.set_(store, p.vocabulary)
    store.set(KEY, p.model_dump(exclude={"vocabulary"}))
    return view(store)


def persona_block(p: CoachPersona, vocabulary: str = "expert", athlete_sex: str | None = None) -> str:
    """Bloc ajouté à la fin de chaque prompt système qui écrit à l'athlète (ton et mots uniquement)."""
    from .vocab import prompt_block
    fem = p.gender == "femme"
    agree = ("une femme : accorde au féminin ce que tu dis de toi (« je suis prête », « ravie », « contente »)"
             if fem else "un homme : accorde au masculin ce que tu dis de toi (« je suis prêt », « ravi », « content »)")
    addr = ("TUTOIE l'athlète (« tu », « ton plan »)." if p.address == "tu" else
            "VOUVOIE l'athlète (« vous », « votre plan », impératif en « -ez ») dans tous tes messages ; cette "
            "consigne remplace toute mention de tutoiement plus haut.")
    return (
        "\n\n# Personnalité du coach (ton uniquement)\n"
        f"- Tu t'appelles {p.name} ; présente-toi ou signe avec ce prénom quand c'est naturel (premier message).\n"
        f"- Tu es {agree}.\n"
        + ({"M": "- L'athlète est un homme : accorde au masculin ce que tu dis de lui (« prêt », « partant »).\n",
            "F": "- L'athlète est une femme : accorde au féminin ce que tu dis d'elle (« prête », « partante »).\n"}
           .get((athlete_sex or "").upper()[:1], ""))
        + f"- Adresse : {addr}\n"
        f"- Style {_TONES[p.personality]}"
        + prompt_block(vocabulary) + "\n"
        "- Cette personnalité ne change QUE le ton et les mots. Les garde-fous, les règles de sécurité, les "
        "consignes ci-dessus et le plan calculé par le code restent strictement identiques quelle que soit la "
        "personnalité : jamais de séance en plus, jamais d'encouragement à courir avec une douleur ou des "
        "symptômes, jamais d'atténuation d'une alerte de santé.")
