"""Lecture des flux Strava (connecteur MCP officiel) -> Track.

⚠ Usage PERSONNEL uniquement (Strava API Policy §3.5) : pour tester le Race
Pacer sur tes propres données. Le produit multi-athlètes passe par l'API
Suunto (cf. revue d'architecture).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .tracks import Track


def track_from_strava(streams: dict, name: str = "strava") -> Track:
    """`streams` = réponse de get_activity_streams (time, distance, altitude, heart_rate...)."""
    for k in ("time", "distance", "altitude"):
        if not streams.get(k):
            raise ValueError(f"flux '{k}' manquant")
    hr = streams.get("heart_rate")
    hr_arr = None if not hr else np.array([np.nan if x is None else x for x in hr], float)
    return Track(name=name, dist=np.array(streams["distance"], float),
                 alt=np.array(streams["altitude"], float),
                 t=np.array(streams["time"], float), hr=hr_arr)


def load_strava_json(path: str | Path, name: str | None = None) -> Track:
    p = Path(path)
    return track_from_strava(json.loads(p.read_text()), name or p.stem)
