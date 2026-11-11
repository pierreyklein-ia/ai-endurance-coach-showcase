"""Profil du coureur en radar (v0.20) : une note sur 10 par qualité, calculée sur ses données.

Les 6 piliers classiques (puissance aérobie, endurance fractionnelle, économie, durabilité, robustesse,
métabolisme) ne se mesurent pas tous sans laboratoire. On garde ce qui se mesure honnêtement avec une montre,
on remplace le reste par des qualités mesurables et utiles au plan, et on dit pour chaque note d'où elle vient
et quelle confiance lui accorder.

Axes (note 0-10 ; 10 = repère d'un amateur expert, pas d'un élite mondial) :
1. Puissance aérobie : VMA estimée (meilleur effort plat, 10 km ≈ 87 % VMA). 10/10 = 22 km/h (H), 19 (F).
2. Endurance de vitesse (ex-« endurance fractionnelle », LT2 % VO2max : labo) : exposant personnel de Riegel
   entre le 10 km et le meilleur effort plat de plus d'une heure (semi, marathon). 1,03 → 10 ; 1,15 → 0.
3. Durabilité : découplage FC / allure (corrigée de la pente) entre la 1re et la 2e moitié des sorties longues
   (≥ 90 min, échauffement de 10 min exclu), médiane des 3 dernières. 2 % → 10 ; 12 % → 0. Repère élite < 3,5 %.
4. Caisse : heures de course par semaine sur 12 mois. 12 h → 10.
5. Robustesse (v0.25) : 1 point par mois sans arrêt de course de 14 jours ou plus (blessure probable ; la
   récupération après une très longue course ne compte pas), remise à 0 à chaque arrêt long ; pénalité pour les
   semaines de pic de charge (ACWR > 1,5).
6. Spécificité : D+ par km des 12 dernières semaines rapporté à celui de l'objectif, et plus longue sortie des
   16 dernières semaines rapportée à 45 % du temps de course estimé. Normal qu'elle soit basse loin de la course.
7. Force et prévention : séances de renfo enregistrées sur 12 semaines. 2 par semaine → 10.

Non mesurés (laboratoire) : économie de course (coût en O2) et métabolisme (FatMax, tolérance glucidique).
"""
from __future__ import annotations

import math
import re
import statistics
from datetime import date, timedelta

from .diagnosis import FLAT_DPLUS_PER_KM, dplus_per_km, eq_10k, is_run, run_time

RACE_WORDS = re.compile(r"(?i)\b(race|semi|half|marathon|10 ?km|comp[ée]t\w*|chrono|dossard)\b")

NOT_MEASURED = [
    {"key": "economie", "label": "Économie de course",
     "why": "Se mesure au laboratoire (oxygène consommé à 14-16 km/h). Une montre ne peut pas la déduire.",
     "how": "Test d'effort avec analyse des gaz (médecine du sport, ~150-250 €)."},
    {"key": "metabolisme", "label": "Métabolisme (FatMax, glucides)",
     "why": "Se mesure au laboratoire (calorimétrie) ; la tolérance aux glucides se travaille à l'entraînement.",
     "how": "Note en course ce que tu absorbes (g/h) : le coach pourra en tenir compte."},
]


def _clamp(x: float) -> float:
    return round(max(0.0, min(10.0, x)), 1)


# --------------------------------------------------------------------------- durabilité (flux)
def decoupling(streams: dict) -> float | None:
    """Découplage FC / allure corrigée de la pente (Friel, Pa:HR), en %. None si les flux ne suffisent pas."""
    t, hr, d = streams.get("time"), streams.get("heart_rate"), streams.get("distance")
    alt = streams.get("altitude")
    if not t or not hr or not d or len(t) != len(hr) or len(t) != len(d) or t[-1] < 90 * 60:
        return None
    start = next((i for i, x in enumerate(t) if x >= 600), None)          # échauffement exclu
    if start is None:
        return None
    # fenêtres de ~30 s : vitesse corrigée de la pente (montée +3,3 %/% de pente, descente +1,8 %/%)
    pts = []
    i = start
    while i < len(t) - 1:
        j = i
        while j < len(t) - 1 and t[j] - t[i] < 30:
            j += 1
        dt, dd = t[j] - t[i], d[j] - d[i]
        if dt > 0 and dd > 1 and dt < 120:                                 # pauses ignorées
            g = ((alt[j] - alt[i]) / dd) if alt and len(alt) == len(t) else 0.0
            g = max(-0.3, min(0.3, g))
            f = 1 + 3.3 * g if g > 0 else 1 + 1.8 * g
            h_mean = statistics.fmean(hr[i:j + 1])
            if h_mean > 60:
                pts.append((t[i], dt, dd * f, h_mean * dt))
        i = j
    if len(pts) < 40:
        return None
    mid = (pts[0][0] + pts[-1][0]) / 2
    halves = [[p for p in pts if p[0] < mid], [p for p in pts if p[0] >= mid]]
    ef = []
    for hv in halves:
        tt = sum(p[1] for p in hv)
        if tt <= 0:
            return None
        ef.append((sum(p[2] for p in hv) / tt) / (sum(p[3] for p in hv) / tt))
    return round((ef[0] - ef[1]) / ef[0] * 100, 1)


# --------------------------------------------------------------------------- axes
def _vma(runs, day, sex):
    flat = [a for a in runs if day - timedelta(days=270) <= a.local_day <= day and a.distance_m >= 5000
            and dplus_per_km(a) < FLAT_DPLUS_PER_KM]
    best = min(flat, key=eq_10k, default=None)
    if not best:
        return None
    vma = 10_000 / eq_10k(best) * 3.6 / 0.87
    lo, hi = (9, 19) if sex == "F" else (10, 22)
    return {"score": _clamp((vma - lo) / (hi - lo) * 10), "value": f"VMA ~{vma:.1f} km/h".replace(".", ","),
            "basis": f"{best.name or best.sport} ({best.local_day:%d/%m/%Y}), 10 km ≈ 87 % de la VMA",
            "confidence": "moyenne" if best.distance_m <= 12_000 else "faible", "raw": round(vma, 1)}


def _speed_endurance(runs, day):
    win = [a for a in runs if day - timedelta(days=365) <= a.local_day <= day and dplus_per_km(a) < FLAT_DPLUS_PER_KM]
    short = min((a for a in win if 5000 <= a.distance_m <= 12_000), key=eq_10k, default=None)
    if not short:
        return None
    t10 = eq_10k(short)
    v10 = 10_000 / t10
    # seulement des efforts longs « de course » : nom de course, ou allure ≥ 85 % de la vitesse 10 km
    # (une sortie longue d'entraînement à allure facile ne dit rien de la tenue de l'allure).
    longs = [a for a in win if a.distance_m >= 18_000 and run_time(a) >= 3600
             and (RACE_WORDS.search(a.name or "") or a.distance_m / run_time(a) >= 0.85 * v10)]
    if not longs:
        return None
    best_k, best_a = None, None
    for a in longs:
        k = math.log(run_time(a) / t10) / math.log(a.distance_m / 10_000)
        if best_k is None or k < best_k:
            best_k, best_a = k, a
    return {"score": _clamp((1.15 - best_k) / (1.15 - 1.03) * 10),
            "value": f"exposant {best_k:.3f}".replace(".", ","),
            "basis": f"{best_a.name or best_a.sport} ({best_a.distance_m / 1000:.1f} km, {best_a.local_day:%d/%m/%Y}) "
                     f"comparé à ton 10 km ; 1,06 = tenue moyenne".replace(".", ",", 1),
            "confidence": "moyenne" if best_a.distance_m >= 21_000 else "faible", "raw": round(best_k, 3)}


def _durability(dec_items, day):
    vals = [x for x in dec_items if day - timedelta(days=120) <= x["day"] <= day]
    vals = sorted(vals, key=lambda x: x["day"])[-3:]
    if not vals:
        return None
    dec = statistics.median(x["dec"] for x in vals)
    return {"score": _clamp(10 - (dec - 2)), "value": f"découplage {dec:.1f} %".replace(".", ","),
            "basis": f"{len(vals)} sortie(s) longue(s) récente(s) avec cardio ; repère élite < 3,5 %",
            "confidence": "moyenne" if len(vals) >= 2 else "faible", "raw": dec}


def _volume(runs, day):
    win = [a for a in runs if day - timedelta(days=364) <= a.local_day <= day]
    if not win:
        return None
    first = min(a.local_day for a in win)
    weeks = max(4, min(52, ((day - first).days + 1) / 7))
    hpw = sum(a.duration_s for a in win) / 3600 / weeks
    return {"score": _clamp(hpw / 12 * 10), "value": f"{hpw:.1f} h de course par semaine".replace(".", ","),
            "basis": f"moyenne sur {round(weeks)} semaines", "confidence": "élevée", "raw": round(hpw, 1)}


def _robustness(runs, daily_loads_fn, day):
    """v0.25 (demande de Pierre-Yves) : 1 point par mois sans arrêt long, remise à 0 après un arrêt de course de
    14 jours ou plus (blessure probable). Un arrêt qui suit une très longue course (4 h ou plus dans les 3 jours
    avant) est une récupération prévue : il ne compte pas. Pénalité pour les pics de charge (ACWR > 1,5)."""
    past = sorted((a for a in runs if a.local_day <= day), key=lambda a: a.local_day)
    if len(past) < 6:
        return None
    last_stop, gap_days = None, 0
    for prev, nxt in zip(past, past[1:]):
        gap = (nxt.local_day - prev.local_day).days - 1
        if gap >= 14:
            planned = any(a.duration_s >= 4 * 3600 and 0 <= (prev.local_day - a.local_day).days <= 3 for a in past
                          if a.local_day <= prev.local_day)
            if not planned:
                last_stop, gap_days = nxt.local_day, gap
    tail_gap = (day - past[-1].local_day).days
    if tail_gap >= 14:                                          # arrêt en cours
        return {"score": 0.0, "value": f"arrêt en cours ({tail_gap} jours sans courir)",
                "basis": "remise à 0 après 14 jours sans course ; 1 point par mois de reprise sans arrêt long",
                "confidence": "élevée", "raw": 0}
    since = last_stop or past[0].local_day
    months = (day - since).days / 30.4
    spikes = 0
    if daily_loads_fn:
        start = max(since, day - timedelta(days=364))
        acwr = daily_loads_fn(start, day)
        spikes = sum(1 for k in range(0, len(acwr), 7) if any(x > 1.5 for x in acwr[k:k + 7]))
    score = min(10.0, months) - 0.5 * max(0, spikes - 2)
    basis = (f"dernier arrêt long : {gap_days} jours, repris le {last_stop:%d/%m/%Y}" if last_stop
             else "aucun arrêt de 14 jours ou plus dans ton historique")
    if spikes:
        basis += f" ; {spikes} semaine(s) de pic de charge (ACWR > 1,5)"
    return {"score": _clamp(score), "value": f"{months:.0f} mois sans arrêt long".replace(".", ","),
            "basis": basis + ". Remise à 0 après 14 jours sans course (hors récupération d'une très longue course) ; "
                             "1 point par mois.", "confidence": "élevée", "raw": round(months, 1)}


def _specificity(runs, day, goal, goal_s):
    if not goal or not goal.get("km"):
        return None
    win = [a for a in runs if day - timedelta(days=84) <= a.local_day <= day]
    if not win:
        return None
    km = sum(a.distance_m for a in win) / 1000
    dpkm = sum(a.dplus_m or 0 for a in win) / km if km else 0
    goal_dpkm = (goal.get("dplus") or 0) / goal["km"]
    a = 1.0 if goal_dpkm < 5 else min(1.0, dpkm / goal_dpkm)
    longest = max((a.duration_s for a in runs if day - timedelta(days=112) <= a.local_day <= day), default=0)
    need = 0.45 * goal_s if goal_s else 3 * 3600
    b = min(1.0, longest / need)
    return {"score": _clamp(10 * (0.5 * a + 0.5 * b)),
            "value": f"{dpkm:.0f} m D+/km (objectif {goal_dpkm:.0f}), plus longue sortie {longest / 3600:.1f} h".replace(".", ","),
            "basis": f"pour {goal['name']} ; repère : sortie longue ≥ {need / 3600:.1f} h".replace(".", ","),
            "confidence": "élevée", "raw": round(0.5 * a + 0.5 * b, 2)}


def _strength(acts, day):
    recent = [a for a in acts if day - timedelta(days=84) <= a.local_day <= day]
    if not recent:
        return None                                   # pas de données sur la période : on ne note pas
    n = sum(1 for a in recent if a.sport == "Renfo")
    pw = n / 12
    return {"score": _clamp(pw / 2 * 10), "value": f"{pw:.1f} séance de renfo par semaine".replace(".", ","),
            "basis": "séances « Renfo » enregistrées sur 12 semaines ; 2 par semaine = 10/10",
            "confidence": "faible" if n == 0 else "moyenne", "raw": round(pw, 2)}


AXES = [("puissance", "Puissance aérobie"), ("endurance_vitesse", "Endurance de vitesse"), ("durabilite", "Durabilité"),
        ("caisse", "Caisse"), ("robustesse", "Robustesse"), ("specificite", "Spécificité"), ("force", "Force et prévention")]

TIPS = {
    "puissance": ("Un test de 6 min à fond (ou un 5-10 km de course) recalera la note.",
                  "Fractionné court (30/30, 400 m) une fois par semaine en période de développement."),
    "endurance_vitesse": ("Un semi ou une sortie de 1 h 15 à allure soutenue sur le plat suffit à la mesurer.",
                          "Seuil long (3 × 12 min, 2 × 20 min) et allure marathon en fin de sortie longue."),
    "durabilite": ("Fais une sortie longue de 2 h ou plus avec ta ceinture cardio : je calcule le découplage.",
                   "Sorties longues avec une fin à allure soutenue ; nutrition pendant l'effort."),
    "caisse": ("Il faut quelques semaines de sorties pour la calculer.", "Monter le volume de 10 % par semaine au plus, en endurance facile."),
    "robustesse": ("Il faut au moins 6 semaines d'historique.", "Régularité avant intensité ; semaine allégée toutes les 3-4 semaines."),
    "specificite": ("Donne au coach la distance et le D+ de ton objectif.",
                    "Se construit dans les 8-12 dernières semaines : D+ et sorties longues proches de la course."),
    "force": ("Enregistre tes séances de renfo sur Strava (type « Musculation »).",
              "2 séances par semaine : force lourde + prévention (mollets, hanches, gainage)."),
}


def runner_profile(acts, today: date, goal: dict | None = None, goal_s: float | None = None, sex: str = "M",
                   dec_items: list[dict] | None = None, acwr_fn=None) -> dict:
    """Notes actuelles et d'il y a 3 mois. dec_items : [{day: date, dec: %}] (sorties longues avec flux)."""
    runs = [a for a in acts if is_run(a)]
    dec_items = dec_items or []

    def compute(day):
        return {"puissance": _vma(runs, day, sex), "endurance_vitesse": _speed_endurance(runs, day),
                "durabilite": _durability(dec_items, day), "caisse": _volume(runs, day),
                "robustesse": _robustness(runs, acwr_fn, day), "specificite": _specificity(runs, day, goal, goal_s),
                "force": _strength(acts, day)}

    now, before = compute(today), compute(today - timedelta(days=91))
    axes = []
    for key, label in AXES:
        cur, old = now[key], before[key]
        axes.append({"key": key, "label": label, "score": cur["score"] if cur else None,
                     "score_3m": old["score"] if old else None,
                     "value": cur["value"] if cur else None, "basis": cur["basis"] if cur else None,
                     "confidence": cur["confidence"] if cur else "à mesurer",
                     "tip": TIPS[key][1] if cur else TIPS[key][0]})
    measured = [a for a in axes if a["score"] is not None]
    strongest = max(measured, key=lambda a: a["score"], default=None)
    weakest = min((a for a in measured if a["key"] != "specificite"), key=lambda a: a["score"], default=None)
    return {"axes": axes, "not_measured": NOT_MEASURED,
            "strongest": strongest["label"] if strongest else None, "weakest": weakest["label"] if weakest else None}
