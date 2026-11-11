"""Onglet « Progrès » (v0.19) : séries pour suivre l'évolution, calculées sur tout l'historique.

Tout est déterministe et explicable (aucun LLM) :
- forme / fatigue / fraîcheur : PMC Banister 42/7 sur la charge quotidienne (load_engine.pmc), prolongé jusqu'au
  jour de l'objectif avec la charge des séances prévues (ce que donnera le plan s'il est suivi) ;
- cumuls sur 7 jours glissants : km et D+ de course, heures et charge toutes activités ;
- semaines : volume, monotonie de Foster (moyenne / écart type de la charge quotidienne), contrainte (charge ×
  monotonie), régularité (jours prévus où une sortie a bien eu lieu) ;
- chronos estimés à chaque date : meilleur effort plat ≥ 5 km des 270 jours précédents (comme le bilan) ramené à un 10 km
  (Riegel, exposant 1,06), puis 5 km, semi ; marathon corrigé par l'écart personnel observé sur un vrai marathon
  (sinon +4 %, écart usuel des amateurs) ;
- objectifs trail : distance-effort (km + D+/100, convention ITRA), temps = marathon estimé × (km-effort / 42,195)
  ^ 1,10. Repère : sur 85 km roulants, ~3 × le temps marathon. Fourchette ±6 % (trail), ±3 % (route).
  Terrain technique, chaleur, nuit : non modélisés (le Race Pacer le fait pour une course précise) ;
- efficacité aérobie : allure des footings plats (30-120 min, FC enregistrée) ramenée à une même FC
  (EF = vitesse / FC, Coggan) ; elle baisse quand l'endurance progresse.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from .diagnosis import FLAT_DPLUS_PER_KM, RIEGEL, dplus_per_km, eq_10k, is_run, run_time
from .load_engine import pmc

TRAIL_EXP = 1.10
BEST_WINDOW = 270                                  # même fenêtre que la référence du bilan
DEFAULT_MARATHON_GAP = 0.04
DISTANCES = [("5 km", 5_000), ("10 km", 10_000), ("semi", 21_097.5), ("marathon", 42_195)]


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _r(x, nd=1):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, nd)


def tsb_label(tsb: float) -> str:
    if tsb > 5:
        return "frais"
    if tsb >= -10:
        return "neutre"
    if tsb >= -30:
        return "en charge"
    return "très fatigué"


# --------------------------------------------------------------------------- chronos
def _marathon_gap(runs, day: date) -> float:
    """Écart personnel entre un vrai marathon (2 dernières années) et la prédiction de Riegel."""
    maras = [a for a in runs if 41_000 <= a.distance_m <= 43_500 and dplus_per_km(a) < FLAT_DPLUS_PER_KM
             and day - timedelta(days=730) <= a.local_day <= day]
    best_gap = None
    for m in maras:
        ref = [a for a in runs if a.distance_m >= 5000 and a.distance_m < 30_000
               and dplus_per_km(a) < FLAT_DPLUS_PER_KM and abs((a.local_day - m.local_day).days) <= 180]
        b = min(ref, key=eq_10k, default=None)
        if b:
            pred = eq_10k(b) * (42_195 / 10_000) ** RIEGEL
            g = run_time(m) * (42_195 / m.distance_m) / pred - 1
            best_gap = g if best_gap is None else min(best_gap, g)
    return DEFAULT_MARATHON_GAP if best_gap is None else max(0.0, min(0.15, best_gap))


def estimates_at(runs, day: date) -> dict | None:
    flat = [a for a in runs if day - timedelta(days=BEST_WINDOW) <= a.local_day <= day and a.distance_m >= 5000
            and dplus_per_km(a) < FLAT_DPLUS_PER_KM]
    best = min(flat, key=eq_10k, default=None)
    if not best:
        return None
    e10 = eq_10k(best)
    gap = _marathon_gap(runs, day)
    out = {"ref": {"name": best.name or best.sport, "day": best.local_day.isoformat(),
                   "km": round(best.distance_m / 1000, 2)}, "marathon_gap": round(gap, 3)}
    for label, m in DISTANCES:
        t = e10 * (m / 10_000) ** RIEGEL
        if label == "marathon":
            t *= 1 + gap
        out[label] = round(t)
    return out


def goal_time(est: dict, km: float, dplus: float | None, kind: str) -> tuple[int, int, int] | None:
    """(bas, médian, haut) en secondes pour un objectif, à partir des chronos estimés."""
    if not est or not km:
        return None
    dplus = dplus or 0
    trail = kind == "trail" or dplus / km > 15
    ekm = km + dplus / 100
    if ekm <= 42.2:
        t = est["10 km"] * (ekm / 10) ** RIEGEL
        if ekm > 30:                                   # l'écart personnel du marathon s'installe après 30 km
            t *= 1 + est["marathon_gap"] * (ekm - 30) / 12.2
    else:
        t = est["marathon"] * (ekm / 42.195) ** TRAIL_EXP
    band = 0.06 if trail else 0.03
    return round(t * (1 - band)), round(t), round(t * (1 + band))


# --------------------------------------------------------------------------- calcul principal
def progress(acts, plan, goals: list[dict], today: date, days: int = 180) -> dict:
    """acts : activités (store) ; plan : séances prévues ; goals : [{name, date, km, dplus, kind}]."""
    start = today - timedelta(days=days - 1)
    if not acts:
        return {"empty": True}
    first = min(a.local_day for a in acts)
    runs = [a for a in acts if is_run(a)]

    # charge quotidienne sur tout l'historique (PMC juste), puis découpe sur la fenêtre affichée
    n = (today - first).days + 1
    per_day = [0.0] * n
    km = [0.0] * n
    dp = [0.0] * n
    hrs = [0.0] * n
    for a in acts:
        i = (a.local_day - first).days
        if 0 <= i < n:
            per_day[i] += a.load or 0.0
            hrs[i] += a.duration_s / 3600
            if is_run(a):
                km[i] += a.distance_m / 1000
                dp[i] += a.dplus_m or 0
    seed = sum(per_day[:14]) / 14
    states = pmc(per_day, ctl0=seed, atl0=seed, a7_0=seed, c28_0=seed)

    def day_at(i):
        return (first + timedelta(days=i)).isoformat()

    lo = max(0, (start - first).days)
    pmc_series = [[day_at(i), _r(s.ctl), _r(s.atl), _r(s.ctl - s.atl)] for i, s in enumerate(states) if i >= lo]
    roll = []
    for i in range(lo, n):
        j = max(0, i - 6)
        roll.append([day_at(i), _r(sum(km[j:i + 1])), _r(sum(hrs[j:i + 1])), round(sum(dp[j:i + 1])),
                     round(sum(per_day[j:i + 1]))])

    # projection avec le plan (séances prévues après aujourd'hui), jusqu'au plus proche objectif couvert
    future = [s for s in plan if s.day > today]
    projection, race_day = [], None
    if future:
        end = max(s.day for s in future)
        covered = [g for g in goals if g.get("date") and today < g["date"] <= end + timedelta(days=3)]
        if covered:
            race_day = min(g["date"] for g in covered)
            end = race_day
        loads = [0.0] * (end - today).days
        for s in future:
            k = (s.day - today).days - 1
            if 0 <= k < len(loads) and not s.is_race:
                loads[k] += s.planned_load or 0.0
        last = states[-1]
        proj = pmc(loads, ctl0=last.ctl, atl0=last.atl, a7_0=last.a7, c28_0=last.c28)
        projection = [[(today + timedelta(days=k + 1)).isoformat(), _r(s.ctl), _r(s.atl), _r(s.ctl - s.atl)]
                      for k, s in enumerate(proj)]

    # semaines
    weeks = []
    planned_days = {}
    for s in plan:
        if s.intensity not in ("rest", "race") and s.day <= today:
            planned_days.setdefault(_monday(s.day), set()).add(s.day)
    done_days = {a.local_day for a in acts}
    m = _monday(max(start, first))
    while m <= today:
        idx = [(m - first).days + k for k in range(7) if 0 <= (m - first).days + k < n]
        dl = [per_day[i] for i in idx]
        mono = (statistics.mean(dl) / statistics.pstdev(dl)) if len(dl) >= 4 and statistics.pstdev(dl) > 0 else None
        load = sum(dl)
        pd = planned_days.get(m, set())
        weeks.append({"start": m.isoformat(), "km": round(sum(km[i] for i in idx)), "hours": _r(sum(hrs[i] for i in idx)),
                      "dplus": round(sum(dp[i] for i in idx)), "load": round(load),
                      "monotony": _r(mono, 2), "strain": round(load * mono) if mono else None,
                      "planned": len(pd), "done": len([d for d in pd if d in done_days]),
                      "partial": m + timedelta(days=6) > today})
        m += timedelta(weeks=1)

    # chronos estimés et objectifs : un point par semaine (lundi) + aujourd'hui
    points = []
    m = _monday(max(start, first))
    pts_days = []
    while m < today:
        pts_days.append(m)
        m += timedelta(weeks=1)
    pts_days.append(today)
    for d in pts_days:
        est = estimates_at(runs, d)
        row = {"day": d.isoformat()}
        if est:
            row.update({k: est[k] for k, _ in DISTANCES})
            for g in goals:
                gt = goal_time(est, g.get("km"), g.get("dplus"), g.get("kind", "trail"))
                if gt:
                    row[f"goal:{g['name']}"] = gt
        points.append(row)
    now_est = estimates_at(runs, today)
    ago = estimates_at(runs, today - timedelta(days=28))
    goal_cards = []
    for g in goals:
        cur = goal_time(now_est, g.get("km"), g.get("dplus"), g.get("kind", "trail")) if now_est else None
        prev = goal_time(ago, g.get("km"), g.get("dplus"), g.get("kind", "trail")) if ago else None
        goal_cards.append({"name": g["name"], "date": g["date"].isoformat() if g.get("date") else None,
                           "role": g.get("role", "A"), "label": g.get("label", "Objectif"),
                           "km": g.get("km"), "dplus": g.get("dplus"), "kind": g.get("kind"),
                           "estimate": cur, "delta_28d": (cur[1] - prev[1]) if cur and prev else None,
                           "days_to": (g["date"] - today).days if g.get("date") else None})

    # efficacité aérobie : allure des footings plats ramenée à une FC commune
    eff_runs = [a for a in runs if a.avg_hr and a.avg_hr > 90 and 30 * 60 <= run_time(a) <= 120 * 60
                and a.distance_m >= 4000 and dplus_per_km(a) < FLAT_DPLUS_PER_KM and a.local_day >= start]
    efficiency, hr_ref = [], None
    if len(eff_runs) >= 6:
        hr_ref = int(round(statistics.median(a.avg_hr for a in eff_runs) / 5) * 5)
        by_w: dict[date, list[float]] = {}
        for a in eff_runs:
            ef = (a.distance_m / run_time(a)) / a.avg_hr
            by_w.setdefault(_monday(a.local_day), []).append(ef)
        for w, v in sorted(by_w.items()):
            pace = 1000 / (statistics.median(v) * hr_ref)
            efficiency.append([w.isoformat(), round(pace), len(v)])

    last = states[-1]
    k7 = roll[-1] if roll else [None, 0, 0, 0, 0]
    race_form = None
    if race_day and projection:
        r = next((p for p in projection if p[0] == race_day.isoformat()), projection[-1])
        race_form = {"day": race_day.isoformat(), "ctl": r[1], "tsb": r[3], "label": tsb_label(r[3] or 0)}
    return {
        "range_days": days, "first_day": first.isoformat(), "today": today.isoformat(),
        "now": {"ctl": _r(last.ctl), "atl": _r(last.atl), "tsb": _r(last.ctl - last.atl),
                "tsb_label": tsb_label(last.ctl - last.atl), "acwr": _r(last.acwr, 2),
                "km7": k7[1], "h7": k7[2], "dplus7": k7[3], "load7": k7[4]},
        "pmc": pmc_series, "projection": projection, "race_form": race_form,
        "rolling7": roll, "weeks": weeks[-26:], "estimates": points,
        "estimates_now": now_est, "goals": goal_cards,
        "efficiency": efficiency, "efficiency_hr": hr_ref,
    }
