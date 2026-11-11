"""Contrats typés de l'équipe d'agents (v0.40).

Comme pour le reste du graphe, aucun agent ne produit de texte libre « parsé » ensuite : chaque sortie est un
schéma Pydantic rempli par tool use (réponse finale forcée), donc testable et traçable.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SpecialistKey = Literal["charge", "nutrition", "course", "prevention"]
Risk = Literal["low", "medium", "high"]


class RoutingDecision(BaseModel):
    """Sortie du superviseur : quels spécialistes consulter (0 à 3 pour un message ordinaire) et pourquoi."""
    specialists: list[SpecialistKey] = Field(default_factory=list, max_length=4)
    reason: str = Field(default="", max_length=300)


class SpecialistNote(BaseModel):
    """Note d'un spécialiste au coach : {findings, recommendations, risks, evidence_ids, confidence} (+ niveau de
    risque, veto). `evidence_ids` : identifiants de fiches de la base (outil knowledge_search)."""
    findings: list[str] = Field(default_factory=list, max_length=8)
    recommendations: list[str] = Field(default_factory=list, max_length=6)
    risks: list[str] = Field(default_factory=list, max_length=5)
    risk_level: Risk = "low"
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.7, ge=0, le=1)
    veto: bool = False                    # Prévention seulement : pas d'intensité proposée (les garde-fous décident)
    veto_reason: str | None = None


class CriticVerdict(BaseModel):
    """LLM-as-judge : le brouillon du coach est-il fondé, cohérent avec l'équipe, sûr ?"""
    verdict: Literal["ok", "revise"] = "ok"
    reasons: list[str] = Field(default_factory=list, max_length=8)


class Priority(BaseModel):
    action: str = Field(max_length=300)
    why: str = Field(default="", max_length=400)
    cards: list[str] = Field(default_factory=list, max_length=4)


class EyeReport(BaseModel):
    """Rapport approfondi « Œil du coach » (rédigé par le coach à partir des sections de toute l'équipe)."""
    summary: str = Field(max_length=900)
    strengths: list[str] = Field(default_factory=list, max_length=6)
    weaknesses: list[str] = Field(default_factory=list, max_length=6)
    risks: list[str] = Field(default_factory=list, max_length=6)
    nutrition: str = Field(default="", max_length=700)
    race: str = Field(default="", max_length=700)
    priorities: list[Priority] = Field(default_factory=list, max_length=3)
    to_measure: list[str] = Field(default_factory=list, max_length=4)   # ce qu'il faut mesurer d'ici le prochain point
    next_review: str = Field(default="", max_length=200)


__all__ = ["SpecialistKey", "Risk", "RoutingDecision", "SpecialistNote", "CriticVerdict", "Priority", "EyeReport"]
