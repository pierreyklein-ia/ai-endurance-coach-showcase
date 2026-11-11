"""Générateur d'affûtage déterministe (0 token) à partir de TA charge réelle.

Règles (Wang et al., PLOS One 2023, registre `taper.*`) :
- durée ≤ 21 j (ici 14 j + la veille de l'affûtage) ;
- volume réduit PROGRESSIVEMENT : ≈ -35 % la 1re semaine, ≈ -55 % la 2e
  (moyenne ≈ -45 %, dans la fourchette -41 / -60 %) ;
- intensité conservée : 1 séance de qualité courte par semaine + rappel d'allure J-4 ;
- fréquence conservée (au moins 80 % des séances habituelles).
Le plan produit passe ensuite dans les MÊMES garde-fous que les propositions du LLM.
Le LLM ne génère pas le plan : il l'explique et l'adapte au fil des messages.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..schemas import PlannedSession
from ..session_content import Workout, fit, render

# Intensity factor moyen d'une séance (échauffement compris) : hrTSS/h ≈ 100·IF²
IF = {"rest": 0.0, "z1": 0.65, "z2": 0.72, "long": 0.72, "tempo": 0.83,
      "threshold": 0.85, "vo2": 0.86, "hills": 0.82}
BOUNDS = {"z1": (30, 60), "z2": (40, 100), "long": (90, 210), "tempo": (40, 70),
          "threshold": (50, 75), "vo2": (40, 70), "hills": (45, 90)}
# Priorités quand il faut réduire : le facile d'abord, la sortie longue ensuite,
# la qualité en dernier (intensité conservée). Durée = base x échelle^poids.
WEIGHT = {"z1": 1.0, "z2": 1.0, "long": 0.6, "tempo": 0.25, "threshold": 0.25, "vo2": 0.25, "hills": 0.4}
FIXED_MIN = {1: 20, 3: 30}                              # J-1 déblocage, J-3 footing + lignes droites


def estimate_load(intensity: str, minutes: float, dplus_m: float = 0) -> float:
    if intensity in ("rest", "race") or minutes <= 0:
        return 0.0
    f = IF.get(intensity, 0.72)
    if intensity in ("long", "z2", "z1") and minutes > 0:
        f += min(0.06, dplus_m / (minutes / 60) / 10_000)    # le D+ fait monter la FC
    return round(100 * f * f * minutes / 60, 1)


@dataclass
class TaperInputs:
    pre_taper_week_load: float
    sessions_per_week: float
    typical_long_min: float = 180
    long_dplus_per_h: float = 350


# (jours avant la course, intensité, part de la durée "type", D+ ?, description)
TEMPLATE = [
    (15, "z2", 1.0, True, "Endurance fondamentale, relâché"),
    (14, "threshold", 1.0, False, "Échauffement 20 min, 3 x 10 min au seuil (récup 3 min), retour au calme"),
    (13, "z1", 0.8, False, "Footing très facile"),
    (12, "z2", 1.0, True, "Endurance avec quelques côtes en marche active"),
    (11, "long", 1.0, True, "Dernière sortie longue, descentes techniques relâchées"),
    (10, "z1", 0.8, False, "Décrassage"),
    (9, "rest", 0, False, "Repos"),
    (8, "tempo", 1.0, False, "Échauffement 20 min, 2 x 10 min allure tempo, retour au calme"),
    (7, "z1", 0.8, False, "Footing très facile"),
    (6, "z2", 0.9, True, "Endurance courte avec un peu de D+"),
    (5, "rest", 0, False, "Repos"),
    (4, "tempo", 0.8, False, "Rappel d'allure : 4 x 3 min tempo, récup 2 min"),
    (3, "z1", 0.6, False, "Footing + 4 lignes droites"),
    (2, "rest", 0, False, "Repos (voyage, sac, ravitos)"),
    (1, "z1", 0.4, False, "Déblocage 20 min, très facile"),
]
# v0.50 : séances de qualité structurées (le texte suit la durée calculée : session_content)
TEMPLATE_W = {14: Workout(20, 3, 600, 180, cooldown=10, pace=" au seuil"),
              8: Workout(20, 2, 600, 180, cooldown=10, pace=" allure tempo"),
              4: Workout(15, 4, 180, 120, cooldown=10, pace=" allure tempo", prefix="Rappel d'allure : ")}
BASE_MIN = {"z1": 45, "z2": 60, "tempo": 55, "threshold": 65, "long": 180}
WEEK_TARGETS = ((15, 9, 0.65), (8, 1, 0.45))           # (du J-, au J-, part de la charge pré-affûtage)


def taper_plan(race_day: date, inputs: TaperInputs, start: date, race_name: str,
               race_minutes: int, prefix: str = "coach") -> list[PlannedSession]:
    base = dict(BASE_MIN, long=max(100, min(inputs.typical_long_min * 0.65, 200)))
    days = {j: (k, share, dp, desc) for j, k, share, dp, desc in TEMPLATE}
    if inputs.sessions_per_week < 5:                    # peu de séances d'habitude : plus de repos
        days[13] = ("rest", 0, False, "Repos")
        days[7] = ("rest", 0, False, "Repos")

    minutes: dict[int, float] = {}
    for hi, lo, share in WEEK_TARGETS:
        # Semaine entamée (plan généré en cours de route) : cible au prorata des jours restants.
        js_all = [j for j in range(hi, lo - 1, -1) if race_day - timedelta(days=j) >= start]
        target = inputs.pre_taper_week_load * share * len(js_all) / (hi - lo + 1)
        js = [j for j in js_all if days[j][0] != "rest"]
        raw = {j: base[days[j][0]] * days[j][1] for j in js}

        def dur(j: int, scale: float) -> float:
            if j in FIXED_MIN:
                return FIXED_MIN[j]
            k = days[j][0]
            return min(max(raw[j] * scale ** WEIGHT[k], BOUNDS[k][0]), BOUNDS[k][1])

        def week_load(scale: float) -> float:
            return sum(estimate_load(days[j][0], dur(j, scale),
                                     inputs.long_dplus_per_h * dur(j, scale) / 60 if days[j][2] else 0)
                       for j in js)

        lo_s, hi_s = 0.1, 4.0                            # dichotomie sur le facteur d'échelle
        for _ in range(40):
            mid = (lo_s + hi_s) / 2
            lo_s, hi_s = (mid, hi_s) if week_load(mid) < target else (lo_s, mid)
        for j in js:
            minutes[j] = dur(j, lo_s)

    plan = []
    for j in range(15, 0, -1):
        day = race_day - timedelta(days=j)
        if day < start:
            continue
        k, _, dp_flag, desc = days[j]
        m = int(round(minutes.get(j, 0) / 5) * 5)
        dp = int(inputs.long_dplus_per_h * m / 60 / 10) * 10 if dp_flag else 0
        if j in TEMPLATE_W and m:
            desc = render(fit(TEMPLATE_W[j], m))
        plan.append(PlannedSession(session_id=f"{prefix}-{day}", day=day, intensity=k, duration_min=m,
                                   elevation_gain_m=dp, planned_load=estimate_load(k, m, dp),
                                   description=desc))
    plan.append(PlannedSession(session_id=f"{prefix}-{race_day}", day=race_day, intensity="race",
                               duration_min=race_minutes, elevation_gain_m=10_150, planned_load=0,
                               is_race=True, description=f"{race_name} — objectif : finir en maîtrise"))
    for j in (1, 2, 3):                                  # récupération post-course
        day = race_day + timedelta(days=j + 1)           # la course dure ~1,5 j
        plan.append(PlannedSession(session_id=f"{prefix}-{day}", day=day, intensity="rest",
                                   duration_min=0, planned_load=0, description="Récupération post-course"))
    return plan


def pre_taper_inputs(daily_loads: list[float], session_days: int, long_minutes: list[float],
                     long_dplus_per_h: float | None = None) -> TaperInputs:
    """`daily_loads` : les 28 jours AVANT le début de l'affûtage."""
    weeks = max(len(daily_loads) / 7, 1)
    return TaperInputs(pre_taper_week_load=sum(daily_loads) / weeks,
                       sessions_per_week=session_days / weeks,
                       typical_long_min=(sorted(long_minutes)[len(long_minutes) // 2] if long_minutes else 180),
                       long_dplus_per_h=long_dplus_per_h or 350)
