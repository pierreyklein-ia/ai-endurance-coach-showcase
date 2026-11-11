"""Parcours de course : trouvé par le système, validé, jamais demandé à l'athlète.

Principe produit (décision du 30/09/2026) : l'athlète ne fournit ni GPX, ni
liste de points de passage, ni arrêts détaillés. Il donne au plus UNE valeur :
la durée moyenne de ses arrêts. Le reste est résolu automatiquement :

  course cible (conversation, objectif Strava/Suunto)
    -> sources ordonnées (catalogue sous accord > organisateur > agrégateurs)
    -> contrôle licence (usage commercial autorisé ?)
    -> téléchargement GPX + points de passage
    -> validation (distance, D+ officiels ; départ/arrivée)
    -> confirmation en une phrase à l'athlète

⚠ Trace de Trail : CGU « usage personnel et non commercial » ; usage commercial
soumis à accord écrit (offre pro). Tant qu'il n'y a pas d'accord, la source est
marquée `commercial_use_allowed=False` et n'est utilisée qu'en mode POC.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .model import Checkpoint
from .tracks import Track, elevation_gain, load_gpx, segment

AID_SERVICES = {"ravito", "ravito_chaud", "base_vie", "eau"}


@dataclass(frozen=True)
class CoursePoint:
    name: str
    km: float                 # kilométrage officiel (celui de la source)
    alt: float | None = None
    services: tuple[str, ...] = ()

    @property
    def is_aid(self) -> bool:
        return bool(AID_SERVICES & set(self.services))


@dataclass
class ValidationReport:
    ok: bool
    km: float
    dplus: float
    issues: list[str] = field(default_factory=list)


@dataclass
class RaceCourse:
    race: str
    track: Track
    points: list[CoursePoint]
    official_km: float
    official_dplus: float
    source: str
    commercial_use_allowed: bool
    report: ValidationReport | None = None

    def checkpoints(self, avg_stop_min: float, stop_sd_min: float | None = None) -> list[Checkpoint]:
        """Points de passage du modèle : arrêt moyen UNIQUE appliqué à chaque ravitaillement.

        Les km officiels (souvent en 3D ou recalés) sont ramenés à la distance
        horizontale du GPX pour tomber au bon endroit du profil.
        """
        scale = (self.track.dist[-1] / 1000) / self.official_km
        sd = avg_stop_min * 0.5 if stop_sd_min is None else stop_sd_min
        return [Checkpoint(p.name, p.km * scale, avg_stop_min if p.is_aid else 0.0,
                           sd if p.is_aid else 0.0) for p in self.points if p.km > 0]


def validate(track: Track, official_km: float, official_dplus: float,
             tol_km: float = 0.03, tol_dplus: float = 0.05, seg_len: float = 50.0) -> ValidationReport:
    """Le D+ d'un tracé issu d'un modèle numérique de terrain est bruité : mesuré
    sur tronçons de 50 m (sur la Diag 2026 : 10 196 m vs 10 191 m annoncés)."""
    km = float(track.dist[-1] / 1000)
    dplus = elevation_gain(segment(track, seg_len=seg_len))
    issues = []
    if abs(km / official_km - 1) > tol_km:
        issues.append(f"distance {km:.1f} km vs {official_km:.1f} km officiels")
    if abs(dplus / official_dplus - 1) > tol_dplus:
        issues.append(f"D+ {dplus:.0f} m vs {official_dplus:.0f} m officiels")
    return ValidationReport(not issues, km, dplus, issues)


def load_course_gpx(path: str | Path) -> Track:
    """Les GPX d'agrégateurs portent des horodatages FACTICES (Trace de Trail :
    un point toutes les 3 s) : on les supprime, sinon le tracé passerait pour
    une activité réalisée."""
    tr = load_gpx(path)
    tr.t, tr.start_utc = None, None
    return tr


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
class CourseSource(Protocol):
    name: str
    commercial_use_allowed: bool

    def find(self, race: str, year: int) -> list[str]: ...
    def fetch(self, ref: str) -> tuple[Track, list[CoursePoint], dict]: ...


class CatalogSource:
    """Catalogue local de courses : JSON (points officiels + chiffres) + GPX.

    C'est la cible en production : alimenté par des accords (organisateurs,
    Trace de Trail pro), versionné, relu. Un fichier = une édition.
    """
    name = "catalogue"

    def __init__(self, root: str | Path, commercial_use_allowed: bool = False):
        self.root = Path(root)
        self.commercial_use_allowed = commercial_use_allowed

    def find(self, race: str, year: int) -> list[str]:
        key = race.lower().replace(" ", "-")
        return [str(p) for p in sorted(self.root.glob(f"*{key}*{year}*.json"))]

    def fetch(self, ref: str):
        meta = json.loads(Path(ref).read_text())
        gpx = Path(ref).with_name(meta["gpx_file"])
        points = [CoursePoint(p["name"], p["km"], p.get("alt"), tuple(p.get("services", [])))
                  for p in meta["points"]]
        return load_course_gpx(gpx), points, meta


class WebSearchPort(Protocol):
    def search(self, query: str, allowed_domains: list[str]) -> list[str]: ...


class TraceDeTrailSource:
    """Agrégateur Trace de Trail (pages /fr/trace/<id>).

    Recherche : moteur de recherche restreint au domaine. Téléchargement :
    endpoint d'export à confirmer DANS le cadre d'un accord pro (CGU) — non
    implémenté volontairement tant que l'accord n'existe pas.
    """
    name = "tracedetrail"
    commercial_use_allowed = False

    def __init__(self, search: WebSearchPort):
        self.search_port = search

    def find(self, race: str, year: int) -> list[str]:
        urls = self.search_port.search(f"{race} {year} parcours", ["tracedetrail.fr"])
        return [u for u in urls if "/trace/" in u]

    def fetch(self, ref: str):
        raise NotImplementedError("Export GPX/points Trace de Trail : accord pro requis (CGU).")


def resolve(race: str, year: int, sources: list[CourseSource], official_km: float,
            official_dplus: float, commercial: bool = True) -> tuple[RaceCourse | None, list[str]]:
    """Première source autorisée dont le tracé passe la validation. Retourne aussi le journal."""
    log = []
    for src in sources:
        if commercial and not src.commercial_use_allowed:
            log.append(f"{src.name} : ignorée (usage commercial non autorisé)")
            continue
        for ref in src.find(race, year):
            try:
                track, points, meta = src.fetch(ref)
            except NotImplementedError as e:
                log.append(f"{src.name} : {e}")
                continue
            rep = validate(track, official_km, official_dplus)
            log.append(f"{src.name} : {ref} -> {'OK' if rep.ok else 'rejeté : ' + '; '.join(rep.issues)}")
            if rep.ok:
                return RaceCourse(race, track, points, official_km, official_dplus,
                                  meta.get("source", src.name), src.commercial_use_allowed, rep), log
    return None, log
