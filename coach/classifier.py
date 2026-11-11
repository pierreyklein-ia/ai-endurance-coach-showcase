"""Décisions "Système 1" : classification calibrée à seuils asymétriques.

JEV (TypeSafe) renvoie des valeurs typées + des probabilités annoncées comme
calibrées, pour ~0,04 $/MTok en entrée et 70-500 ms. C'est exactement le
profil des "smart if-statements" du graphe :
  1. triage d'un message (noise / coaching / red_flag)
  2. interprétation d'un consentement ("ouais vas-y", "bof...") après interrupt
  3. (à benchmarker) extraction des Signals à la place de Haiku

Ce qui reste HORS de JEV, par conception :
- la détection d'urgence (regex déterministe, aucune dépendance réseau) ;
- tout texte libre (JEV ne génère pas de chaînes) -> Sonnet reste le "Système 2".

Tant que la calibration n'est pas MESURÉE sur nos messages (eval/triage_eval.py),
les seuils ci-dessous sont des hypothèses.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, Protocol

from .schemas import Route
from .triage import triage as rule_triage

Consent = Literal["yes", "no", "unclear"]


class CalibratedClassifier(Protocol):
    labels: tuple[str, ...]

    def predict_proba(self, text: str) -> dict[str, float]: ...


# --------------------------------------------------------------------------- #
# Routeur à seuils asymétriques
# --------------------------------------------------------------------------- #
@dataclass
class ThresholdRouter:
    """Coût d'erreur asymétrique -> seuils asymétriques.

    - Classer en NOISE un message porteur de signal = erreur grave -> il faut
      une quasi-certitude (P >= noise_min).
    - Rater un red_flag coûte cher, en déclencher un de trop coûte ~2 centimes
      -> seuil très bas (P >= red_flag_min).
    """
    clf: CalibratedClassifier
    fallback: CalibratedClassifier | None = None
    noise_min: float = 0.97
    red_flag_min: float = 0.10

    def __call__(self, text: str) -> Route:
        try:
            p = self.clf.predict_proba(text)
        except Exception:            # fournisseur indisponible -> règles locales
            if self.fallback is None:
                return Route.COACHING
            p = self.fallback.predict_proba(text)
        if p.get("red_flag", 0) >= self.red_flag_min:
            return Route.RED_FLAG
        if p.get("noise", 0) >= self.noise_min:
            return Route.NOISE
        return Route.COACHING


class RuleClassifier:
    """Baseline : les règles de triage.py, exposées en pseudo-probabilités."""
    labels = ("noise", "coaching", "red_flag")

    def predict_proba(self, text: str) -> dict[str, float]:
        r = rule_triage(text).value
        return {lab: 1.0 if lab == r else 0.0 for lab in self.labels}


class JevClassifier:
    """Adaptateur JEV — À COMPLÉTER avec le SDK reçu en early access.

    Le contrat attendu : entrée = état typé (texte + contexte minimal
    pseudonymisé), sortie = distribution de probabilités sur `labels`.
    Pré-requis RGPD avant tout envoi de message réel : DPA signé et
    localisation du traitement connue (données de santé, art. 9).
    """
    labels = ("noise", "coaching", "red_flag")

    def __init__(self, client=None):
        self.client = client

    def predict_proba(self, text: str) -> dict[str, float]:
        raise NotImplementedError("Brancher le SDK JEV (early access TypeSafe).")


# --------------------------------------------------------------------------- #
# Consentement après interrupt ("Je mets à jour ton agenda ?")
# --------------------------------------------------------------------------- #
_YES = re.compile(r"^(oui|ouais|ok|okay|go|yes|vas[- ]y|d'?accord|dac|valide|carrement|"
                  r"parfait|top|nickel|ca marche|allez)\b")
_NO = re.compile(r"^(non|nan|no|pas maintenant|attends|stop|surtout pas|laisse)\b")


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def rule_consent(answer: str) -> Consent:
    a = _norm(answer)
    if a in {"👍", "👌", "✅"}:
        return "yes"
    if a in {"👎", "❌"}:
        return "no"
    yes, no = bool(_YES.search(a)), bool(_NO.search(a))
    if yes and not no and " mais " not in f" {a} ":
        return "yes"
    if no and not yes:
        return "no"
    return "unclear"   # "oui mais pas samedi", "bof", "je sais pas" -> on redemande
