"""Lecture FIT / GPX et découpage en segments de distance fixe.

Suunto Vertical : altitude barométrique (`enhanced_altitude`) -> D+ fiable ;
FC optique au poignet sauf ceinture -> passer la FC par load_engine.clean_hr.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

SEMICIRCLE_TO_DEG = 180.0 / 2**31
EARTH_R = 6_371_000.0


@dataclass
class Track:
    name: str
    dist: np.ndarray               # m, distance horizontale cumulée
    alt: np.ndarray                # m
    t: np.ndarray | None = None    # s depuis le départ (None pour un tracé de course)
    hr: np.ndarray | None = None
    lat: np.ndarray | None = None
    lon: np.ndarray | None = None
    start_utc: datetime | None = None


def _haversine_cum(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    la, lo = np.radians(lat), np.radians(lon)
    dla, dlo = np.diff(la), np.diff(lo)
    a = np.sin(dla / 2) ** 2 + np.cos(la[:-1]) * np.cos(la[1:]) * np.sin(dlo / 2) ** 2
    return np.concatenate([[0.0], np.cumsum(2 * EARTH_R * np.arcsin(np.sqrt(a)))])


def load_fit(path: str | Path) -> Track:
    import fitdecode
    rows = []
    with fitdecode.FitReader(str(path)) as fit:
        for frame in fit:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "record":
                def g(name):
                    return frame.get_value(name, fallback=None)
                alt = g("enhanced_altitude")
                rows.append((g("timestamp"), g("distance"), alt if alt is not None else g("altitude"),
                             g("heart_rate"), g("position_lat"), g("position_long")))
    rows = [r for r in rows if r[0] is not None and r[2] is not None]
    if len(rows) < 10:
        raise ValueError(f"{path}: pas assez d'enregistrements exploitables")
    ts = [r[0] for r in rows]
    t = np.array([(x - ts[0]).total_seconds() for x in ts])
    alt = np.array([r[2] for r in rows], float)
    hr = np.array([np.nan if r[3] is None else r[3] for r in rows], float)
    lat = np.array([np.nan if r[4] is None else r[4] * SEMICIRCLE_TO_DEG for r in rows])
    lon = np.array([np.nan if r[5] is None else r[5] * SEMICIRCLE_TO_DEG for r in rows])
    if all(r[1] is not None for r in rows):
        dist = np.array([r[1] for r in rows], float)
    else:
        ok = ~np.isnan(lat)
        dist = np.interp(np.arange(len(lat)), np.where(ok)[0], _haversine_cum(lat[ok], lon[ok]))
    start = ts[0] if ts[0].tzinfo else ts[0].replace(tzinfo=UTC)
    return Track(Path(path).stem, dist, alt, t, hr, lat, lon, start)


def load_gpx(path: str | Path) -> Track:
    import gpxpy
    with open(path) as f:
        gpx = gpxpy.parse(f)
    pts = [p for trk in gpx.tracks for seg in trk.segments for p in seg.points]
    lat = np.array([p.latitude for p in pts])
    lon = np.array([p.longitude for p in pts])
    alt = np.array([p.elevation if p.elevation is not None else np.nan for p in pts], float)
    times = [p.time for p in pts]
    t = start = None
    if all(times):
        t = np.array([(x - times[0]).total_seconds() for x in times])
        start = times[0]
    return Track(Path(path).stem, _haversine_cum(lat, lon), alt, t, None, lat, lon, start)


# --------------------------------------------------------------------------- #
# Segmentation
# --------------------------------------------------------------------------- #
@dataclass
class Segments:
    x0: np.ndarray        # m, début du segment
    dx: np.ndarray        # m
    dz: np.ndarray        # m
    grade: np.ndarray     # dz/dx
    lat: np.ndarray       # milieu de segment (NaN si inconnu)
    lon: np.ndarray
    t0: np.ndarray | None = None      # s écoulées au début du segment
    dt: np.ndarray | None = None      # s passées sur le segment
    hr: np.ndarray | None = None      # FC moyenne
    moving: np.ndarray | None = None  # False = arrêt (ravito, pause)

    @property
    def speed(self) -> np.ndarray:
        return self.dx / self.dt


def _smooth(x: np.ndarray, w: int = 5) -> np.ndarray:
    if len(x) < w:
        return x
    pad = np.pad(x, w // 2, mode="edge")
    return np.array([np.median(pad[i:i + w]) for i in range(len(x))])


def _still_mask(t: np.ndarray, d: np.ndarray, min_speed: float, window_s: float) -> np.ndarray:
    """Arrêts détectés sur une fenêtre glissante, PAS seconde par seconde.

    Constat sur données réelles (Suunto via Strava) : la distance n'est mise à
    jour que toutes les 2-3 s en montée lente -> 37 % des pas de 1 s ont
    Δdistance = 0. Une détection seconde par seconde (et le flux `moving` de
    Strava, qui fait la même chose) retire ces secondes du temps de mouvement
    et gonfle les vitesses de 50 à 100 %.
    """
    half = window_s / 2
    lo = np.searchsorted(t, t - half, side="left")
    hi = np.clip(np.searchsorted(t, t + half, side="right") - 1, 0, len(t) - 1)
    span = np.maximum(t[hi] - t[lo], 1e-9)
    return (d[hi] - d[lo]) / span < min_speed


def segment(track: Track, seg_len: float = 25.0, min_moving_speed: float = 0.15,
            stop_window_s: float = 30.0) -> Segments:
    """Découpage à distance fixe.

    25 m par défaut : sur tes sorties réelles, 100 m sous-estimait le D+ de
    8 % en médiane et jusqu'à 33 % sur les petites côtes de Loire (lissage des
    bosses de 30-40 m) ; 25 m colle au D+ Strava (écart médian 0 %).
    """
    d = np.maximum.accumulate(track.dist)          # distance monotone
    marks = np.arange(0.0, d[-1], seg_len)
    marks = np.append(marks, d[-1]) if d[-1] - marks[-1] > seg_len / 2 else marks
    alt = np.interp(marks, d, _smooth(np.nan_to_num(track.alt, nan=np.nanmean(track.alt))))
    dx, dz = np.diff(marks), np.diff(alt)
    mid = (marks[:-1] + marks[1:]) / 2
    lat = np.interp(mid, d, track.lat) if track.lat is not None else np.full(len(dx), np.nan)
    lon = np.interp(mid, d, track.lon) if track.lon is not None else np.full(len(dx), np.nan)
    seg = Segments(marks[:-1], dx, dz, dz / dx, lat, lon)
    if track.t is not None:
        # Temps "en mouvement" : on retire les intervalles bruts où l'on n'avance
        # pas (ravito, photo, lacet) -> la vitesse de segment n'est pas polluée
        # par les arrêts, qui sont modélisés séparément (stops par ravito).
        raw_dt = np.diff(track.t, prepend=track.t[0])
        still = _still_mask(track.t, d, min_moving_speed, stop_window_s)
        t_moving = np.cumsum(np.where(still, 0.0, raw_dt))
        tm_elapsed = np.interp(marks, d, track.t)
        tm_moving = np.interp(marks, d, t_moving)
        seg.t0 = tm_elapsed[:-1]
        seg.dt = np.maximum(np.diff(tm_moving), 1e-3)
        seg.moving = (np.diff(tm_moving) > 0) & ((dx / seg.dt) >= min_moving_speed)
        if track.hr is not None:
            hr = np.interp(mid, d, np.nan_to_num(track.hr, nan=np.nanmedian(track.hr)))
            seg.hr = hr
    return seg


def elevation_gain(seg: Segments) -> float:
    return float(seg.dz[seg.dz > 0].sum())
