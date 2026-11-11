"""Registre versionné des paramètres de sécurité — source unique de vérité.

Pourquoi un registre plutôt qu'un "agent scientifique qui cherche" à l'exécution :
- une décision qui touche la santé doit être REPRODUCTIBLE et AUDITABLE :
  même entrée + même version de registre => même verdict ;
- la veille scientifique est un processus HORS LIGNE (humain + LLM en
  assistance) qui produit une nouvelle version du registre, relue et datée ;
- à l'exécution, le RAG ne fait que retrouver les extraits qui justifient
  ces valeurs, pour les citer à l'athlète.

Principe de gouvernance : seule une règle `validated` ou `literature` peut
BLOQUER. Une règle `placeholder` ne produit qu'un avertissement.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

Status = Literal["validated", "literature", "placeholder"]

REGISTRY_VERSION = "2026-11-04.1"


@dataclass(frozen=True)
class RuleParam:
    key: str
    value: Any
    status: Status
    source: str
    population: str = ""
    validated_by: str = ""
    note: str = ""


_REGISTRY: list[RuleParam] = [
    # --- ACWR : zones fournies et validées par le porteur de projet ----------
    RuleParam("acwr.method", "ewma_7_28", "literature",
              "Williams et al., BJSM 2017 (EWMA pour l'ACWR)",
              note="Les seuils doivent être lus avec la MÊME méthode de calcul."),
    RuleParam("acwr.under", 0.8, "validated",
              "Gabbett, BJSM 2016 (training-injury prevention paradox)",
              "sports collectifs (cricket, rugby, AFL)", "porteur de projet",
              "Sous-entraînement : avertissement, jamais blocage."),
    RuleParam("acwr.sweet_hi", 1.3, "validated", "Gabbett, BJSM 2016",
              "sports collectifs", "porteur de projet"),
    RuleParam("acwr.danger_warn", 1.5, "validated", "Gabbett, BJSM 2016",
              "sports collectifs", "porteur de projet",
              "Au-delà : alerte systématique, même en bloc de choc déclaré."),
    RuleParam("acwr.danger_block", 1.7, "validated",
              "porteur de projet, après rejeu de sa saison 2026 (EWMA max 1,65 en bloc de choc)",
              "athlète pilote, ultra-trail", "porteur de projet",
              "Seuil 1,5 jugé trop strict pour des blocs de choc maîtrisés ; critiques : Impellizzeri et al., IJSPP 2020."),
    RuleParam("acwr.vigilance_requires_shock_block", True, "validated",
              "porteur de projet",
              note="1,3-1,5 autorisé uniquement dans un bloc de choc déclaré au plan."),
    # --- Récupération : À VALIDER par le porteur (placeholders => alerte seulement)
    RuleParam("recovery.no_intensity_hours", 36, "placeholder",
              "à valider — pistes : Barnes, Sports Med 2014 (alcool et récupération) ; "
              "Fullagar et al., Sports Med 2015 (manque de sommeil)",
              note="Après alcool important ou nuit trop courte : pas d'intensité dans ce délai."),
    RuleParam("recovery.min_sleep_hours", 5.0, "placeholder", "à valider"),
    RuleParam("recovery.min_sleep_quality", 3, "placeholder", "à valider (échelle 1-5)"),
    RuleParam("recovery.max_life_stress_for_intensity", 3, "placeholder", "à valider (échelle 1-5)",
              note="Stress de vie au-delà : pas d'intensité dans les 24 h."),
    # --- Douleur -----------------------------------------------------------
    RuleParam("pain.max_for_intensity", 3, "validated", "porteur de projet"),
    # --- Affûtage : première passe de veille ---------------------------------
    RuleParam("taper.days", 14, "literature",
              "Wang et al., PLOS One 2023 (méta-analyse, 14 études, 174 athlètes)",
              "demi-fond, fond, cyclisme ; AUCUNE étude ultra-trail",
              note="8-14 j = effet le plus fort ; <=21 j efficace. À trancher pour 100 miles."),
    RuleParam("taper.volume_reduction_min", 0.41, "literature", "Wang et al., PLOS One 2023"),
    RuleParam("taper.volume_reduction_max", 0.60, "literature", "Wang et al., PLOS One 2023",
              note="Au-delà de 60 % : pas d'effet significatif (sur-affûtage)."),
    RuleParam("taper.keep_intensity", True, "literature", "Wang et al., PLOS One 2023"),
    RuleParam("taper.keep_frequency", True, "literature", "Wang et al., PLOS One 2023"),
    RuleParam("taper.progressive", True, "literature", "Wang et al., PLOS One 2023",
              note="Progressif > palier (les deux efficaces)."),
    RuleParam("taper.frequency_tolerance", 0.8, "placeholder", "à valider"),
    # --- Charge -------------------------------------------------------------
    RuleParam("load.tsb_floor", -30.0, "placeholder", "à valider"),
    RuleParam("load.catch_up_tolerance", 0.10, "placeholder",
              "exigence de la spec (pas de rattrapage) ; tolérance à valider"),
    # --- Pic sur une seule sortie (v0.27, veille 2026-11) -----------------------
    RuleParam("session.peak_warn_ratio", 1.10, "literature",
              "Frandsen et al., Br J Sports Med 2025;59(17):1203-1210 (5 205 coureurs, 18 mois)",
              "coureurs amateurs sur route (âge moyen 46 ans) ; transposition au trail à confirmer",
              note="Sortie > 110 % de la plus longue sortie des 30 j précédents : sur-risque (HRR 1,5 à 2,3). "
                   "Le plan ne contient que des durées : le contrôle se fait sur la durée."),
    RuleParam("session.peak_cap_ratio", 1.30, "placeholder",
              "choix de produit (prudence), pas une valeur de l'étude",
              note="Au-delà : la séance est ramenée automatiquement à 110 %. Modifiable."),
    RuleParam("session.peak_window_days", 30, "literature", "Frandsen et al., BJSM 2025"),
    RuleParam("session.peak_floor_min", 50, "placeholder",
              "choix de produit : sous 50 min (ou 8 km), pas de contrôle (reprise, footings courts)"),
    # --- Volume maximum déclaré par l'athlète (v0.49) ------------------------------
    RuleParam("week.max_tolerance", 0.05, "validated",
              "déclaration de l'athlète (le temps dont il dispose) ; demande du porteur de projet",
              "tous", "porteur de projet",
              "R10_week_max : 7 jours au-delà de son maximum (temps, km ou D+ estimés) + 5 % de marge d'arrondi. "
              "Bloquant pour une proposition du coach ; simple alerte quand l'athlète déplace lui-même une séance."),
]

REGISTRY: dict[str, RuleParam] = {r.key: r for r in _REGISTRY}


def R(key: str) -> Any:
    return REGISTRY[key].value


def can_block(key: str) -> bool:
    return REGISTRY[key].status in ("validated", "literature")


def registry_hash() -> str:
    """Empreinte loguée avec chaque décision (traçabilité jury / RGPD)."""
    blob = json.dumps([asdict(r) for r in _REGISTRY], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]
