"""Moteur de charge 100 % déterministe (0 token LLM).

Ce module remplace ce que la spec attribue au "classifieur ML" côté données
sportives : identifier les zones et calculer la charge n'est PAS un problème
d'apprentissage, c'est du calcul physiologique à partir des seuils de
l'athlète. Le ML n'a de sens ici que pour la détection d'anomalies.

Pipeline : flux FC brut -> nettoyage (GIGO) -> TRIMP/hrTSS -> PMC (CTL/ATL/TSB)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

HR_MIN_PHYSIO = 30
HR_MAX_PHYSIO = 220
MAX_SLOPE_BPM_PER_S = 15   # au-delà : artefact quasi certain (capteur optique)


# --------------------------------------------------------------------------- #
# 1. Qualité de la donnée (GIGO)
# --------------------------------------------------------------------------- #
@dataclass
class CleanResult:
    hr: np.ndarray            # série nettoyée (NaN = échantillon rejeté puis interpolé)
    rejected_mask: np.ndarray
    quality: float           # part d'échantillons valides [0, 1]
    cadence_lock_s: int      # secondes suspectes de "cadence lock"


def hampel(x: np.ndarray, half_window: int = 7, n_sigmas: float = 3.0) -> np.ndarray:
    """Masque des outliers par filtre de Hampel (médiane glissante + MAD)."""
    n = len(x)
    mask = np.zeros(n, dtype=bool)
    k = 1.4826  # MAD -> écart-type pour une loi normale
    for i in range(n):
        lo, hi = max(0, i - half_window), min(n, i + half_window + 1)
        win = x[lo:hi]
        win = win[~np.isnan(win)]
        if len(win) < 3:
            continue
        med = np.median(win)
        mad = k * np.median(np.abs(win - med))
        if mad > 0 and abs(x[i] - med) > n_sigmas * mad:
            mask[i] = True
    return mask


def clean_hr(hr_bpm: np.ndarray, dt_s: float = 1.0,
             cadence_spm: np.ndarray | None = None) -> CleanResult:
    hr = hr_bpm.astype(float).copy()
    bad = (hr < HR_MIN_PHYSIO) | (hr > HR_MAX_PHYSIO) | np.isnan(hr)

    slope = np.abs(np.diff(hr, prepend=hr[0])) / dt_s
    bad |= slope > MAX_SLOPE_BPM_PER_S

    tmp = hr.copy()
    tmp[bad] = np.nan
    bad |= hampel(tmp)

    # Cadence lock : le capteur optique "accroche" la cadence de pas (~160-180)
    lock_s = 0
    if cadence_spm is not None and len(cadence_spm) == len(hr):
        locked = np.abs(hr - cadence_spm) <= 3
        lock_s = int(locked.sum() * dt_s)
        if lock_s >= 60:           # seuil à calibrer sur données réelles
            bad |= locked

    hr[bad] = np.nan
    valid = ~np.isnan(hr)
    if valid.sum() >= 2:
        idx = np.arange(len(hr))
        hr = np.interp(idx, idx[valid], hr[valid])
    return CleanResult(hr=hr, rejected_mask=bad,
                       quality=float(valid.mean()) if len(hr) else 0.0,
                       cadence_lock_s=lock_s)


# --------------------------------------------------------------------------- #
# 2. Zones & charge d'une séance
# --------------------------------------------------------------------------- #
FRIEL_RUN_ZONES = [(0.0, 0.85), (0.85, 0.90), (0.90, 0.95), (0.95, 1.00), (1.00, 9.99)]


def time_in_zones(hr: np.ndarray, lthr: float, dt_s: float = 1.0) -> list[float]:
    """Minutes par zone (zones Friel en % de la FC au seuil lactique)."""
    ratio = hr / lthr
    return [float(((ratio >= lo) & (ratio < hi)).sum() * dt_s / 60)
            for lo, hi in FRIEL_RUN_ZONES]


def _trimp_weight(hrr: np.ndarray | float, sex: str):
    a, b = (0.64, 1.92) if sex == "M" else (0.86, 1.67)   # Banister
    return a * np.exp(b * hrr)


def banister_trimp(hr: np.ndarray, hr_rest: float, hr_max: float,
                   sex: str = "M", dt_s: float = 1.0) -> float:
    hrr = np.clip((hr - hr_rest) / (hr_max - hr_rest), 0, 1)
    return float(np.sum((dt_s / 60) * hrr * _trimp_weight(hrr, sex)))


def hr_tss(hr: np.ndarray, hr_rest: float, hr_max: float, lthr: float,
           sex: str = "M", dt_s: float = 1.0) -> float:
    """hrTSS : TRIMP normalisé pour que 1 h au seuil = 100."""
    hrr_lt = (lthr - hr_rest) / (hr_max - hr_rest)
    ref = 60 * hrr_lt * float(_trimp_weight(hrr_lt, sex))
    return 100 * banister_trimp(hr, hr_rest, hr_max, sex, dt_s) / ref


def session_load(hr_raw: np.ndarray, *, hr_rest: float, hr_max: float, lthr: float,
                 duration_min: float, rpe: int | None, srpe_to_tss: float,
                 sex: str = "M", dt_s: float = 1.0, min_quality: float = 0.8,
                 cadence_spm: np.ndarray | None = None) -> tuple[float, str]:
    """Charge d'une séance avec repli sRPE si la FC est inexploitable.

    `srpe_to_tss` : coefficient propre à l'athlète, appris par régression sur
    ses séances "propres" (hrTSS ~ RPE x durée). Retourne (charge, source).
    """
    cleaned = clean_hr(hr_raw, dt_s, cadence_spm)
    if cleaned.quality >= min_quality:
        return hr_tss(cleaned.hr, hr_rest, hr_max, lthr, sex, dt_s), "hrTSS"
    if rpe is not None:
        return rpe * duration_min * srpe_to_tss, "sRPE_fallback"
    return float("nan"), "unusable"   # -> l'orchestrateur demande le RPE à l'athlète


# --------------------------------------------------------------------------- #
# 3. Performance Management Chart (Banister / EWMA)
# --------------------------------------------------------------------------- #
@dataclass
class LoadState:
    ctl: float                  # "fitness"  (EWMA 42 j) -- affichage PMC
    atl: float                  # "fatigue"  (EWMA 7 j)
    tsb: float                  # "forme"    = CTL - ATL (veille)
    acwr: float                 # aigu/chronique EWMA 7:28 (Williams 2017) -- garde-fou
    a7: float | None = None     # état EWMA aigu (λ = 2/(7+1))
    c28: float | None = None    # état EWMA chronique (λ = 2/(28+1))


def pmc(daily_loads: list[float], ctl0: float = 0.0, atl0: float = 0.0,
        a7_0: float | None = None, c28_0: float | None = None,
        ctl_days: int = 42, atl_days: int = 7) -> list[LoadState]:
    """PMC (Banister, constantes 42/7) + ACWR EWMA 7:28 (Williams et al. 2017).

    Deux jeux d'EWMA distincts : les zones ACWR validées (0,8 / 1,3 / 1,5)
    viennent de la littérature 7:28 ; les appliquer à un ratio ATL/CTL 7:42
    serait une erreur d'unité silencieuse.
    """
    kc, ka = 1 - math.exp(-1 / ctl_days), 1 - math.exp(-1 / atl_days)
    la, lc = 2 / (7 + 1), 2 / (28 + 1)
    ctl, atl = ctl0, atl0
    a7 = atl0 if a7_0 is None else a7_0
    c28 = ctl0 if c28_0 is None else c28_0
    out = []
    for load in daily_loads:
        tsb = ctl - atl
        ctl += (load - ctl) * kc
        atl += (load - atl) * ka
        a7 += (load - a7) * la
        c28 += (load - c28) * lc
        out.append(LoadState(ctl=ctl, atl=atl, tsb=tsb,
                             acwr=a7 / c28 if c28 > 1e-6 else float("nan"),
                             a7=a7, c28=c28))
    return out
