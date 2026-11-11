"""v0.50 — Zones cardiaques, allures repères et cibles de séance.

Valeurs (FC max, FC de repos, FC au seuil) : saisie de l'athlète > zones Strava (`GET /athlete/zones`, autorisation
`profile:read_all`) > estimation d'après ses données (FC max observée dans des flux propres, FC au seuil d'après ses
meilleurs efforts de 30 à 60 min, sinon 93 % de la FC max) > réglages du serveur (PilotSettings). Chaque valeur garde
sa source (« saisie », « Strava », « estimée », « réglage »).

Modèle de zones unique : Friel, en % de la FC au seuil (le même que coach/load_engine.py et coach/session_type.py).
Les valeurs retenues alimentent le moteur de charge (imports suivants) et la détection du type de séance.

Cibles par séance : footing / récupération (Z1-Z2), sortie longue (Z2), tempo (Z3), seuil (Z4), VMA (Z5), côtes
(Z4-Z5). Sur les répétitions courtes (30/30, moins de 2 min), la FC monte en retard : l'allure et l'effort passent
d'abord. Fonctions pures, testées.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .load_engine import FRIEL_RUN_ZONES

OVERRIDES_KEY = "hr_overrides"         # {"hr_max": 190, "hr_rest": 48, "lthr": 172} (None = automatique)
STRAVA_KEY = "strava_zones"            # {"zones": [{"min": 0, "max": 120}, …], "custom": bool, "at": "2026-11-08"}
RESOLVED_KEY = "hr_zones_resolved"     # dernière résolution (contexte du coach)
NAMES = {"hr_max": "FC max", "hr_rest": "FC de repos", "lthr": "FC au seuil"}
LIMITS = {"hr_max": (120, 230), "hr_rest": (25, 110), "lthr": (100, 220)}
LABELS = {"saisie": "saisie", "strava": "Strava", "estimee": "estimée", "reglage": "réglage"}
ZONE_NAMES = ["Récupération", "Endurance", "Tempo", "Seuil", "VMA"]
LTHR_SHARE = 0.93                      # FC au seuil par défaut : 93 % de la FC max
EST_MIN_ACTIVITIES = 3                 # estimation seulement avec assez de sorties à FC propre
EST_MAX_DAYS, EST_LTHR_DAYS = 365, 180


@dataclass
class HrZones:
    hr_max: float
    hr_rest: float
    lthr: float
    src: dict = field(default_factory=dict)          # hr_max / hr_rest / lthr -> saisie | strava | estimee | reglage
    paces: dict = field(default_factory=dict)        # allures repères du bilan (seuil, vma, 10 km, endurance…)
    strava: list | None = None                        # zones Strava brutes (affichage)

    def bpm(self, pct: float) -> int:
        return int(round(self.lthr * pct))

    def zones(self) -> list[dict]:
        out = []
        for i, (lo, hi) in enumerate(FRIEL_RUN_ZONES):
            out.append({"zone": f"Z{i + 1}", "label": ZONE_NAMES[i], "lo": self.bpm(lo) if lo else None,
                        "hi": self.bpm(hi) - 1 if hi < 9 else None,
                        "pct": f"{round(lo * 100)}-{round(hi * 100)} %" if lo and hi < 9
                        else f"< {round(hi * 100)} %" if not lo else f"≥ {round(lo * 100)} %"})
        return out

    def as_dict(self) -> dict:
        return {"hr_max": self.hr_max, "hr_rest": self.hr_rest, "lthr": self.lthr, "src": self.src,
                "paces": self.paces}


# --------------------------------------------------------------------------- estimation d'après les données
def stream_stats(t, hr) -> dict:
    """Repères FC d'une sortie (flux propre) : pic sur 30 s, meilleures moyennes sur 30 et 60 min."""
    import numpy as np
    if t is None or hr is None or len(t) < 30 or len(hr) != len(t):
        return {}
    t = np.asarray(t, float)
    h = np.array([np.nan if x is None else x for x in hr], float)
    h[(h < 40) | (h > 225)] = np.nan
    ok = np.isfinite(h)
    if ok.sum() < 30 or ok.mean() < 0.8:                 # flux troué : pas une référence
        return {}
    t5 = np.arange(0, t[-1], 5.0)
    h5 = np.interp(t5, t[ok], h[ok])

    def best(seconds: int) -> float | None:
        k = seconds // 5
        if len(h5) < k:
            return None
        c = np.cumsum(np.insert(h5, 0, 0.0))
        return float(((c[k:] - c[:-k]) / k).max())
    out = {"hr_peak": round(best(30) or 0, 1)}
    for name, sec in (("hr_best30", 1800), ("hr_best60", 3600)):
        v = best(sec)
        if v:
            out[name] = round(v, 1)
    return out


def _key_day(key: str) -> date | None:
    try:
        return date(int(key[:4]), int(key[4:6]), int(key[6:8]))
    except (ValueError, TypeError):
        return None


def estimate(profiles: dict, today: date) -> dict:
    """FC max et FC au seuil estimées d'après les profils de sortie (clé d'activité -> stats). {} si trop peu."""
    rows = [(d, p) for k, p in (profiles or {}).items() if isinstance(p, dict) and p.get("hr_peak")
            and (d := _key_day(k)) and today - timedelta(days=EST_MAX_DAYS) <= d <= today]
    if len(rows) < EST_MIN_ACTIVITIES:
        return {}
    peak = max(p["hr_peak"] for _, p in rows)
    if not 140 <= peak <= 220:
        return {}
    out: dict = {"hr_max": round(peak), "n": len(rows)}
    efforts = [max(p.get("hr_best60") or 0, 0.98 * (p.get("hr_best30") or 0)) for d, p in rows
               if d >= today - timedelta(days=EST_LTHR_DAYS)]
    lt = max(efforts, default=0)
    if 0.80 * peak <= lt <= 0.97 * peak:                 # effort assez soutenu pour être une référence
        out["lthr"] = round(lt)
    return out


def strava_lthr(zones: list | None) -> float | None:
    """FC au seuil d'après les 5 zones FC de Strava : début de la zone 5 (« anaérobie », au-dessus du seuil)."""
    if not zones or len(zones) != 5:
        return None
    try:
        v = float(zones[4]["min"])
    except (KeyError, TypeError, ValueError):
        return None
    return v if 120 <= v <= 215 else None


def resolve(base: tuple[float, float, float | None], overrides: dict | None, strava: dict | None, est: dict | None,
            paces: dict | None = None) -> HrZones:
    """`base` = (FC max, FC repos, FC seuil ou None) des réglages du serveur."""
    ov = {k: v for k, v in (overrides or {}).items() if v}
    est = est or {}
    src = {}
    if ov.get("hr_max"):
        hr_max, src["hr_max"] = float(ov["hr_max"]), "saisie"
    elif est.get("hr_max"):
        hr_max, src["hr_max"] = float(est["hr_max"]), "estimee"
    else:
        hr_max, src["hr_max"] = float(base[0]), "reglage"
    if ov.get("hr_rest"):
        hr_rest, src["hr_rest"] = float(ov["hr_rest"]), "saisie"
    else:
        hr_rest, src["hr_rest"] = float(base[1]), "reglage"
    s_lt = strava_lthr((strava or {}).get("zones"))
    if ov.get("lthr"):
        lthr, src["lthr"] = float(ov["lthr"]), "saisie"
    elif s_lt:
        lthr, src["lthr"] = s_lt, "strava"
    elif est.get("lthr"):
        lthr, src["lthr"] = float(est["lthr"]), "estimee"
    elif src["hr_max"] != "reglage" or not base[2]:
        lthr = float(round(LTHR_SHARE * hr_max))
        src["lthr"] = "estimee" if src["hr_max"] != "reglage" else "reglage"
    else:
        lthr, src["lthr"] = float(base[2]), "reglage"
    lthr = min(lthr, hr_max - 1)
    return HrZones(hr_max, hr_rest, lthr, src, dict(paces or {}), (strava or {}).get("zones"))


def apply(settings, z: HrZones) -> None:
    """Valeurs retenues -> réglages du moteur de charge (imports suivants, type de séance)."""
    settings.hr_max, settings.hr_rest, settings.lthr = z.hr_max, z.hr_rest, z.lthr


def validate(values: dict) -> dict:
    """Saisie de l'athlète : bornes physiologiques et ordre repos < seuil < max. Lève ValueError."""
    out = {}
    for k, v in values.items():
        if k not in LIMITS:
            continue
        if v in (None, "", 0):
            out[k] = None
            continue
        v = float(v)
        lo, hi = LIMITS[k]
        if not lo <= v <= hi:
            raise ValueError(f"{NAMES[k]} : entre {lo} et {hi} bpm")
        out[k] = round(v)
    return out


# --------------------------------------------------------------------------- cibles de séance
TARGETS = {
    "z1": {"lo": 0.72, "hi": 0.85, "zone": "Z1", "rpe": "2-3/10", "pace": "endurance", "what": "récupération"},
    "z2": {"lo": 0.80, "hi": 0.90, "zone": "Z1-Z2", "rpe": "3-4/10", "pace": "endurance", "what": "endurance"},
    "long": {"lo": 0.80, "hi": 0.90, "zone": "Z2", "rpe": "4/10", "pace": "endurance", "what": "sortie longue"},
    "tempo": {"lo": 0.90, "hi": 0.95, "zone": "Z3", "rpe": "6/10", "pace": "marathon", "what": "tempo"},
    "threshold": {"lo": 0.95, "hi": 1.00, "zone": "Z4", "rpe": "7/10", "pace": "seuil", "what": "seuil"},
    "vo2": {"lo": 1.00, "hi": 1.06, "zone": "Z5", "rpe": "9/10", "pace": "vma", "what": "VMA"},
    "hills": {"lo": 0.95, "hi": 1.02, "zone": "Z4-Z5", "rpe": "8/10", "pace": None, "what": "côtes"},
}
_PACE = re.compile(r"(\d{1,2}:\d{2})\s*/\s*km")


def target(intensity: str, description: str, z: HrZones | None) -> dict | None:
    """Cible d'une séance de course : FC (bpm), allure si connue, effort ; ce qui fait foi en premier (`lead`)."""
    t = TARGETS.get(intensity)
    if t is None or z is None:
        return None
    from .session_content import short_reps
    lo, hi = z.bpm(t["lo"]), z.bpm(t["hi"])
    if intensity == "vo2":
        hi = min(hi, int(z.hr_max))
    pace = None
    m = _PACE.search(description or "") if intensity in ("tempo", "threshold", "vo2") else None
    if m:
        pace = f"{m[1]}/km"
    elif t["pace"] and z.paces.get(t["pace"]):
        pace = f"{z.paces[t['pace']]}/km"
    short = intensity in ("vo2", "threshold", "tempo", "hills") and short_reps(description or "")
    club = (description or "").startswith("Séance du club")          # contenu donné par le club : effort d'abord
    if club:
        pace = None
    lead = "pace" if short and pace else "effort" if short or club or intensity == "hills" else "hr"
    hr_txt = f"{lo}-{hi} bpm"
    note = ""
    if short:
        note = ("Fractions courtes : la FC monte en retard (elle n'atteint la zone qu'en fin de bloc) ; cale-toi sur "
                + ("l'allure et " if pace else "") + "l'effort.")
    elif intensity == "hills":
        note = "En côte, l'allure ne veut rien dire : effort et FC."
    elif club:
        note = "Séance du club : suis son contenu ; sur les fractions courtes, la FC monte en retard."
    elif intensity in ("threshold", "tempo"):                       # v0.51 : terrain et vent
        note = ("Montée ou vent de face : garde la FC et l'effort, l'allure ralentit (souvent 10 à 20 s/km) ; "
                "en descente ou vent dans le dos, ne cours pas après l'allure. La FC met 1 à 3 min à monter "
                "en début de répétition.")
    bits = {"pace": [pace, f"effort {t['rpe']}", f"FC {hr_txt} en fin de bloc ({t['zone']})"],
            "effort": [f"effort {t['rpe']}", f"FC {hr_txt} ({t['zone']})", pace],
            "hr": [f"FC {hr_txt} ({t['zone']})", pace, f"effort {t['rpe']}"]}[lead]
    text = "Cible : " + " · ".join(b for b in bits if b)
    if club:                       # v0.52.4 : contenu choisi par le club (seuil, VMA, côtes) : les deux repères
        s4 = TARGETS["threshold"]
        lo, hi = z.bpm(s4["lo"]), z.bpm(s4["hi"])
        hr_txt = f"{lo}-{hi} bpm"
        text = (f"Cible : selon la séance du club · seuil : FC {hr_txt}, effort 7/10 · "
                f"VMA : effort 9/10, FC au-dessus de {hi} bpm en fin de fraction")
        return {"zone": "Club", "hr": [lo, hi], "hr_text": hr_txt, "pace": None, "rpe": "7-9/10", "lead": "effort",
                "short_reps": short, "note": note, "text": text}
    return {"zone": t["zone"], "hr": [lo, hi], "hr_text": hr_txt, "pace": pace, "rpe": t["rpe"], "lead": lead,
            "short_reps": short, "note": note, "text": text}


def coach_brief(resolved: dict | None) -> str:
    """Phrase du contexte du coach : FC repères (avec leur source), zones et cibles par type de séance."""
    if not resolved:
        return ""
    z = HrZones(resolved["hr_max"], resolved["hr_rest"], resolved["lthr"], resolved.get("src") or {},
                resolved.get("paces") or {})
    src = lambda k: LABELS.get(z.src.get(k, ""), "")   # noqa: E731
    zs = ", ".join(f"{x['zone']} {x['lo'] or '<'}-{x['hi'] or '+'}" for x in z.zones())
    cibles = []
    for k in ("z2", "long", "tempo", "threshold", "vo2", "hills"):
        t = target(k, "", z)
        cibles.append(f"{t['zone']} {TARGETS[k]['what']} {t['hr_text']}" + (f" ({t['pace']})" if t["pace"] else ""))
    return (f"Fréquences cardiaques de l'athlète : FC max {z.hr_max:.0f} ({src('hr_max')}), repos {z.hr_rest:.0f} "
            f"({src('hr_rest')}), seuil {z.lthr:.0f} ({src('lthr')}). Zones Friel (% FC seuil) : {zs}. Cibles : "
            + " ; ".join(cibles) + ". Sur les fractions courtes (30/30, < 2 min), donne l'allure et l'effort d'abord : "
            "la FC monte en retard. Donne la cible FC quand tu proposes ou modifies une séance.")


__all__ = ["HrZones", "resolve", "estimate", "stream_stats", "strava_lthr", "apply", "validate", "target",
           "coach_brief", "OVERRIDES_KEY", "STRAVA_KEY", "RESOLVED_KEY", "LABELS"]
