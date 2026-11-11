"""Contrats de données partagés entre les nœuds du graphe.

Principe : tout ce qui circule entre nœuds est typé (Pydantic). Le LLM ne
produit jamais de texte libre qui serait ensuite "parsé" : il remplit ces
schémas via structured output / tool use, ce qui rend chaque étape testable.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Entrée conversationnelle
# --------------------------------------------------------------------------- #
class Channel(str, Enum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    WEB = "web"                      # v0.9 : application web installable (chat intégré)


class InboundMessage(BaseModel):
    """Message normalisé par l'adaptateur de canal (voix déjà transcrite).

    `athlete_id` est un pseudonyme interne : le numéro de téléphone ne sort
    jamais de la couche adaptateur (minimisation RGPD avant tout appel LLM).
    """
    athlete_id: str
    channel: Channel
    text: str
    is_voice: bool = False
    voice_seconds: float = 0.0


class Route(str, Enum):
    NOISE = "noise"            # "ok", "👍", "merci" -> réponse template, 0 token LLM
    RED_FLAG = "red_flag"      # douleur, maladie, symptôme -> chemin sécurité
    COACHING = "coaching"      # ressenti, RPE, contrainte agenda, question plan


# --------------------------------------------------------------------------- #
# Signaux extraits (petit modèle, sortie structurée)
# --------------------------------------------------------------------------- #
class Pain(BaseModel):
    location: str
    severity_0_10: int = Field(ge=0, le=10)


class Signals(BaseModel):
    rpe_last_session: int | None = Field(default=None, ge=1, le=10)
    sleep_quality_1_5: int | None = Field(default=None, ge=1, le=5)
    fatigue_1_5: int | None = Field(default=None, ge=1, le=5)
    life_stress_1_5: int | None = Field(default=None, ge=1, le=5)
    sleep_hours: float | None = Field(default=None, ge=0, le=16)
    alcohol: Literal["none", "moderate", "heavy"] | None = None
    emotional_distress_1_5: int | None = Field(default=None, ge=1, le=5)
    off_topic: bool = False                  # question hors du champ sportif (couple, travail...)
    pain: list[Pain] = Field(default_factory=list)
    illness_symptoms: list[str] = Field(default_factory=list)
    unavailable_dates: list[date] = Field(default_factory=list)
    athlete_question: str | None = None


# --------------------------------------------------------------------------- #
# Plan & décisions
# --------------------------------------------------------------------------- #
Intensity = Literal["rest", "z1", "z2", "tempo", "threshold", "vo2", "long", "hills", "race"]


class PlannedSession(BaseModel):
    session_id: str                      # clé d'idempotence (Calendar extendedProperties)
    day: date
    intensity: Intensity
    duration_min: int = Field(ge=0, le=4000)   # course 180 km incluse
    elevation_gain_m: int = Field(default=0, ge=0)
    planned_load: float = Field(ge=0)    # charge estimée (hrTSS-like)
    description: str = ""
    is_race: bool = False                # exclu du plafond ACWR (la course EST le pic)


class PlanChange(BaseModel):
    action: Literal["keep", "modify", "cancel", "add", "move"]
    session: PlannedSession
    reason: str


class CoachDecision(BaseModel):
    """Sortie UNIQUE du gros modèle par échange : décision + message.

    Fusionner "décider" et "rédiger" en un seul appel = -1 appel LLM par
    échange (levier FinOps n°1 après la taille du contexte).
    """
    changes: list[PlanChange]
    message_to_athlete: str
    evidence_ids: list[str] = Field(default_factory=list)  # passages RAG cités
    is_major_change: bool = False


class Violation(BaseModel):
    rule_id: str
    detail: str
    severity: Literal["block", "warn"] = "block"


class GuardrailVerdict(BaseModel):
    allowed: bool                                     # aucun "block"
    violations: list[Violation] = Field(default_factory=list)
    registry_version: str = ""

    @property
    def blocking(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "block"]
