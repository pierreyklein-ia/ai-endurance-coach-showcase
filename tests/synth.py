"""Athlète et parcours SYNTHÉTIQUES à paramètres connus.

Sert à vérifier que l'estimateur retrouve les paramètres qui ont généré les
données (test du code). Ne dit RIEN du réalisme du modèle : ça, seul le
backtest sur la vraie course le dira.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import numpy as np

from coach.pacer.model import prior_speed
from coach.pacer.tracks import Track

TRUE = dict(v_flat=2.9, down_cap=1.25, beta=0.08, noise=0.12)


def profile(rng, km: float, steep: bool, step: float = 20.0):
    """Relief par sections : pentes douces (Loire) ou raides (Réunion)."""
    grades, x = [], 0.0
    while x < km * 1000:
        L = rng.uniform(400, 3000 if not steep else 6000)
        g = rng.normal(0, 0.05) if not steep else rng.choice([-1, 1]) * rng.uniform(0.05, 0.35)
        n = int(L // step)
        grades += [g] * n
        x += n * step
    grades = np.array(grades[: int(km * 1000 // step)])
    return np.full(len(grades), step), grades


def training_track(rng, hours: float, name: str, steep_share: float = 0.2) -> Track:
    step = 20.0
    dxs, gs = profile(rng, km=hours * 12, steep=rng.random() < steep_share, step=step)
    t, x, z = [0.0], [0.0], [50.0]
    for dx, g in zip(dxs, gs):
        v = prior_speed(np.array([g]), TRUE["v_flat"], TRUE["down_cap"])[0]
        v *= (1 + t[-1] / 3600) ** (-TRUE["beta"]) * np.exp(rng.normal(0, TRUE["noise"]))
        t.append(t[-1] + dx / v); x.append(x[-1] + dx); z.append(z[-1] + g * dx)
        if rng.random() < 0.002:                       # arrêt de 2 à 5 min
            t.append(t[-1] + rng.uniform(120, 300)); x.append(x[-1]); z.append(z[-1])
        if t[-1] > hours * 3600:
            break
    n = len(t)
    return Track(name, np.array(x), np.array(z), np.array(t), np.full(n, 140.0),
                 None, None, datetime(2026, 6, 1, 8, tzinfo=UTC))


def training_set(seed: int = 1) -> list[Track]:
    rng = np.random.default_rng(seed)
    hours = list(rng.uniform(1, 2.5, 20)) + list(rng.uniform(3, 5, 8)) + [11.0, 14.0]
    return [training_track(rng, h, f"act_{i}") for i, h in enumerate(hours)]


def diag_like_course(seed: int = 7) -> Track:
    """~180 km, profil raide ; lat/lon fixés sur Saint-Pierre pour le calcul de nuit."""
    rng = np.random.default_rng(seed)
    dx, g = profile(rng, km=180, steep=True, step=100.0)
    g = g * 10_200 / float((g * dx)[g > 0].sum())      # recalage sur 10 200 m D+
    x = np.concatenate([[0], np.cumsum(dx)])
    z = np.concatenate([[0], np.cumsum(g * dx)]) + 1000
    lat = np.full(len(x), -21.34)
    lon = np.full(len(x), 55.48)
    return Track("diag_like", x, z, None, None, lat, lon, None)


RACE_START = datetime(2026, 10, 15, 22, 0, tzinfo=timezone(timedelta(hours=4)))
