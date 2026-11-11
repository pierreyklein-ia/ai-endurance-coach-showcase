"""v0.37 — Objectifs de l'athlète : principal (A), secondaires (B), projets (C), classés par date.

Une seule source pour toutes les vues (État, Progrès, Aujourd'hui) : le profil d'accueil
(`goal_*` = objectif principal, `events` = autres échéances). Sans profil (pilote), l'objectif
principal vient des réglages (`race_name`, `race_date`).
"""
from __future__ import annotations

from datetime import date

from .onboarding import Profile

ROLE_LABELS = {"A": "Objectif principal", "B": "Objectif secondaire", "C": "Projet"}
_ORDER = {"A": 0, "B": 1, "C": 2}


def goals_list(p: Profile | None, today: date, race_name: str | None = None,
               race_date: date | None = None) -> list[dict]:
    """Liste ordonnée : principal d'abord, puis secondaires et projets, chacun du plus proche au plus lointain.

    Chaque entrée : role, label, name, date (iso ou None), year, days_left, km, dplus, kind, note, done
    (course passée : affichée grisée « terminé »)."""
    raw: list[tuple[str, str, date | None, int | None, float | None, int | None, str, str]] = []
    if p is not None:
        raw.append(("A", p.goal_name, p.goal_date, p.goal_date.year if p.goal_date else None,
                    p.goal_distance_km, p.goal_dplus_m, p.goal_kind or "trail", ""))
        for e in p.events or []:
            raw.append((e.role if e.role in ("B", "C") else "B", e.name, e.date, e.year or (e.date.year if e.date else None),
                        e.distance_km, e.dplus_m, e.kind or "trail", e.note or ""))
    elif race_name and race_date:
        raw.append(("A", race_name, race_date, race_date.year, None, None, "trail", ""))
    out = []
    for role, name, d, year, km, dplus, kind, note in raw:
        out.append({"role": role, "label": ROLE_LABELS[role], "name": name,
                    "date": d.isoformat() if d else None, "year": year,
                    "days_left": (d - today).days if d else None,
                    "km": km, "dplus": dplus, "kind": kind, "note": note,
                    "done": bool(d and d < today)})
    # à venir d'abord (le plus proche en tête), puis date inconnue, puis courses passées ;
    # à date égale, ordre alphabétique (stable d'un appel à l'autre)
    out.sort(key=lambda g: (_ORDER[g["role"]], g["done"], g["date"] is None, g["date"] or "", g["name"].lower()))
    return out
