"""v0.43 — Type de séance réellement couru (footing, tempo, seuil, VMA, côtes), lu dans les flux de la sortie.

Sert à la validation automatique : une séance prévue « seuil » validée par un footing tranquille passe en
« modifiée », avec le type détecté (« plus facile que prévu »). Fonctions pures, testées.

Données : fréquence cardiaque (zones Friel en % de la FC au seuil, comme le moteur de charge), et, quand
elles existent, la distance (blocs rapides : utile pour la VMA courte où la FC n'a pas le temps de monter) et
l'altitude (répétitions de côtes). Sans FC ni distance : type « inconnu », la validation reste sur la durée.

Repères (synthèse, à affiner sur données réelles) :
- VMA : ≥ 6 min au-dessus de la FC seuil (et la zone 5 domine les zones 4-5, v0.52.4), ou ≥ 6 accélérations franches (+25 % sur l'allure médiane, 20 s et plus) avec ≥ 4 min en zone 4-5 (≥ 8 accélérations sans FC) ;
- seuil : ≥ 12 min en zones 4-5 (95 % de la FC seuil et plus), ou ≥ 8 min en zones 4-5 sur ≥ 20 min en zones 3-5
  (v0.50.1 : répétitions de 8 min, la FC n'atteint la zone 4 qu'après 2-3 min) ;
- côtes : ≥ 4 montées de 25 m et plus, avec ≥ 5 min en zones 4-5 ;
- tempo : ≥ 15 min en zones 3 à 5 (90 % de la FC seuil et plus) ;
- sinon footing (sortie longue au-delà de 90 min).
"""
from __future__ import annotations

import numpy as np

# zones Friel (course) : Z1 < 85 %, Z2 85-90, Z3 90-95, Z4 95-100, Z5 ≥ 100 % de la FC au seuil
ZONES = [(0.0, 0.85), (0.85, 0.90), (0.90, 0.95), (0.95, 1.00), (1.00, 9.99)]
LABELS = {"facile": "footing", "long": "sortie longue", "tempo": "tempo", "seuil": "seuil", "vma": "VMA",
          "cotes": "côtes", "inconnu": "non détecté"}
LEVEL = {"facile": 1, "long": 1, "tempo": 2, "seuil": 3, "cotes": 3, "vma": 4}
# intensité prévue (plan) -> types acceptés comme conformes
PLANNED = {"z1": {"facile", "long"}, "z2": {"facile", "long"}, "long": {"long", "facile"},
           "tempo": {"tempo", "seuil"}, "threshold": {"seuil", "tempo", "cotes"}, "vo2": {"vma"},
           "hills": {"cotes", "seuil", "vma"},
           "club": {"vma", "seuil", "tempo", "cotes"}}      # v0.52.2 : séance du club, contenu choisi par le club
PLANNED_LEVEL = {"z1": 1, "z2": 1, "long": 1, "tempo": 2, "threshold": 3, "hills": 3, "vo2": 4, "club": 3}


def _resample(t: np.ndarray, v: np.ndarray, step: float) -> tuple[np.ndarray, np.ndarray]:
    ok = np.isfinite(v)
    t1 = np.arange(0, t[-1], step)
    return t1, np.interp(t1, t[ok], v[ok])


def profile_from_streams(t, hr=None, dist=None, alt=None, *, lthr: float) -> dict | None:
    """Profil compact d'une sortie (gardé dans la base) : minutes par zone, accélérations, montées."""
    if t is None or len(t) < 30:
        return None
    t = np.asarray(t, float)
    out: dict = {"min": round(float(t[-1]) / 60, 1), "zones": None, "fast_reps": 0, "climb_reps": 0}
    if hr is not None:
        h = np.array([np.nan if x is None else x for x in hr], float)
        if np.isfinite(h).sum() > 30 and len(h) == len(t):
            _, h1 = _resample(t, h, 5.0)
            r = h1 / lthr
            out["zones"] = [round(float(((r >= lo) & (r < hi)).sum() * 5 / 60), 1) for lo, hi in ZONES]
        from .hr_zones import stream_stats  # v0.50 : FC max / seuil estimées (Mes zones)
        out.update(stream_stats(t, hr))
    if dist is not None and len(dist) == len(t):
        d = np.asarray(dist, float)
        if np.isfinite(d).sum() > 30:
            t5, d5 = _resample(t, d, 5.0)
            v = np.gradient(d5, 5.0)
            k = 6                                              # vitesse lissée sur 30 s
            vs = np.convolve(v, np.ones(k) / k, mode="same")
            moving = vs[vs > 1.0]
            if moving.size > 20:
                fast = vs > 1.25 * float(np.median(moving))   # +25 % : les variations du trail ne comptent pas
                runs, n = 0, 0
                for f in fast:                                 # blocs rapides d'au moins 20 s
                    n = n + 1 if f else 0
                    if n == 4:
                        runs += 1
                out["fast_reps"] = runs
    if alt is not None and len(alt) == len(t):
        a = np.asarray(alt, float)
        if np.isfinite(a).sum() > 30:
            _, a5 = _resample(t, a, 5.0)
            reps, low, high, climbing = 0, a5[0], a5[0], False
            for x in a5:                                       # montée de ≥ 25 m puis redescente de ≥ 15 m
                if not climbing:
                    low = min(low, x)
                    if x - low >= 25:
                        climbing, high = True, x
                else:
                    high = max(high, x)
                    if high - x >= 15:
                        reps, climbing, low = reps + 1, False, x
            out["climb_reps"] = reps + (1 if climbing else 0)
    return out


def detect(profile: dict | None) -> str:
    """Type de séance couru, d'après le profil."""
    if not profile:
        return "inconnu"
    z, fast, climbs, mins = profile.get("zones"), profile.get("fast_reps", 0), profile.get("climb_reps", 0), \
        profile.get("min", 0)
    if not z:
        if fast >= 8:
            return "vma"
        return "inconnu"
    z45, z345 = z[3] + z[4], z[2] + z[3] + z[4]
    # v0.52.4 : longues répétitions au seuil avec une dernière plus rapide (ou dérive de FC) : du seuil, pas de la
    # VMA ; la VMA domine en zone 5 (fractions longues) ou se fait en fractions courtes (zones 4-5 peu cumulées)
    if (z[4] >= 6 and z[4] >= 0.6 * z45) or (fast >= 6 and z45 >= 4 and (z45 < 12 or z[4] >= 0.5 * z45)):
        return "vma"
    if climbs >= 4 and z45 >= 5:
        return "cotes"
    if z45 >= 12 or (z45 >= 8 and z345 >= 20):      # v0.50.1 : 3 × 8 min, la FC met 2-3 min à monter
        return "seuil"
    if z345 >= 15:
        return "tempo"
    return "long" if mins >= 90 else "facile"


def compare(planned_intensity: str, detected: str) -> dict:
    """Verdict : conforme, plus facile, plus dur, autre (ou non vérifié). `ok` : le type correspond."""
    if detected == "inconnu" or planned_intensity not in PLANNED:
        return {"verdict": "non_verifie", "ok": True, "detected": detected, "label": LABELS.get(detected, detected)}
    if detected in PLANNED[planned_intensity]:
        return {"verdict": "conforme", "ok": True, "detected": detected, "label": LABELS[detected]}
    d = LEVEL[detected] - PLANNED_LEVEL[planned_intensity]
    verdict = "plus_facile" if d < 0 else "plus_dur" if d > 0 else "autre"
    return {"verdict": verdict, "ok": False, "detected": detected, "label": LABELS[detected]}


VERDICT_TEXT = {"conforme": "type conforme", "plus_facile": "plus facile que prévu", "plus_dur": "plus dur que prévu",
                "autre": "autre type de séance",
                "origine": "séance prévue au départ faite", "non_verifie": "type non vérifié (pas de fréquence cardiaque)"}

__all__ = ["LABELS", "VERDICT_TEXT", "compare", "detect", "profile_from_streams"]
