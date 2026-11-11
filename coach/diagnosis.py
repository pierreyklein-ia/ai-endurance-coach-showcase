"""Bilan de départ d'un nouvel athlète, calculé à partir de son historique (Strava, FIT).

Déterministe et explicable : chaque constat porte ses chiffres et l'activité qui les
justifie. Le LLM ne fait que rédiger à partir de ce bilan ; il ne « devine » rien.

Indicateurs (fenêtre récente = 12 semaines, fenêtre précédente = les 12 d'avant) :
- volume : heures et km de course par semaine, part du trail (D+ > 25 m/km) ;
- vitesse : séances de qualité (intitulé de séance structurée, ou sortie plate courue
  au moins à 88 % de la vitesse de référence) par semaine, date de la dernière ;
- référence de vitesse : meilleur effort plat ≥ 5 km sur 9 mois, ramené à un 10 km
  (Riegel, exposant 1,06) ;
- endurance de vitesse : marathon réel comparé à celui que prédit ce 10 km ;
- récupération : effort de plus de 6 h (ou 60 km) dans les 21 derniers jours ;
- historique long (v0.18, jusqu'à 5 ans) : volume par année, records par distance, ultras, plus grosses
  semaines d'entraînement (hors courses de 6 h et plus), coupures de 3 semaines et plus. Il sert à doser la
  charge, pas à juger la forme actuelle.

Seuils (repères de la littérature, à valider en pilote) :
- les coureurs d'endurance entraînés font ~2 séances intenses par semaine, ~20 % du
  temps (Seiler 2010 ; Stöggl & Sperlich 2015) -> moins d'une par semaine sur 12
  semaines = vitesse peu travaillée ;
- marathon > 6 % plus lent que la prédiction de Riegel (1,06) = décrochage au-delà
  d'une heure (la marge couvre l'écart usuel des amateurs, exposant ~1,08).
"""
from __future__ import annotations

import re
import statistics
from datetime import date, timedelta

from pydantic import BaseModel, Field

from .pilot.store import Activity

RUN_SPORTS = {"Course", "Trail"}
QUALITY_NAME = re.compile(
    r"\d+\s*[x×]\s*\d+|fractionn|seuil|vma|tempo|@\s*mp|\ballures?\b|30\s*/\s*30|pyramide|"
    r"\bc[ôo]tes?\b|interval|\btest\b|miles? rep|\b\d+\s*k\s*@|lignes droites", re.I)
# v0.36 : intitulés qui signalent une course (dossard), pour la 1re semaine du plan
RACE_NAME = re.compile(r"marathon|\bsemi\b|dossard|comp[ée]t|\brace\b|\bultra\b|\bcorrida\b|\bfoul[ée]es\b|"
                       r"\b\d+\s*km de\b|\bcourse de\b|\btrail de\b", re.I)
RIEGEL = 1.06
RECENT_DAYS = 44                       # v0.36 : sorties gardées en détail (2 semaines + 30 jours de Frandsen)
FLAT_DPLUS_PER_KM = 12.0
TRAIL_DPLUS_PER_KM = 25.0


class Finding(BaseModel):
    id: str
    severity: str                      # "high" | "medium" | "info"
    title: str
    detail: str
    activity_ids: list[str] = Field(default_factory=list)


class Reference(BaseModel):
    name: str
    day: date
    distance_km: float
    time_s: float
    equivalent_10k_s: float


class YearStat(BaseModel):
    year: int
    runs: int
    km: float
    hours: float
    dplus: int


class Effort(BaseModel):
    """Une performance marquante de l'historique (record sur une distance, ultra terminé)."""
    label: str                         # « 10 km », « Ultra »…
    name: str
    day: date
    distance_km: float
    time_s: float
    dplus_m: int = 0


class Gap(BaseModel):
    start: date
    end: date
    days: int


class History(BaseModel):
    """Profil long terme (v0.18) : jusqu'à 5 ans de résumés Strava. Sert à doser (jusqu'où il a déjà monté sans
    casser), pas à juger la forme actuelle (qui reste fondée sur les 12 dernières semaines)."""
    years: float = 0.0                 # profondeur réelle de l'historique
    runs: int = 0
    per_year: list[YearStat] = Field(default_factory=list)
    records: list[Effort] = Field(default_factory=list)       # meilleur effort plat par distance
    ultras: list[Effort] = Field(default_factory=list)        # > 6 h ou > 60 km, les plus longs d'abord
    peak_week_min: float = 0.0          # plus grosse semaine d'entraînement (min, hors courses de 6 h et plus)
    peak_week_day: date | None = None
    peak_week_12m_min: float = 0.0      # idem, 12 derniers mois
    peak_4w_hours: float = 0.0          # plus gros bloc de 4 semaines (h), 12 derniers mois
    gaps: list[Gap] = Field(default_factory=list)              # coupures de 3 semaines et plus sans course
    best_10k_eq_s: float | None = None  # meilleur équivalent 10 km sur l'historique
    best_10k_eq_day: date | None = None


RECORD_BANDS = [("5 km", 5_000, 5_400), ("10 km", 10_000, 10_700), ("semi", 21_000, 21_700),
                ("marathon", 42_000, 43_500)]


def history(acts: list[Activity], today: date, years: int = 5) -> History:
    start = today - timedelta(days=365 * years + years // 4)
    runs = [a for a in acts if is_run(a) and start <= a.local_day <= today]
    if not runs:
        return History()
    h = History(years=round((today - min(a.local_day for a in runs)).days / 365.25, 1), runs=len(runs))
    by_year: dict[int, list[Activity]] = {}
    for a in runs:
        by_year.setdefault(a.local_day.year, []).append(a)
    h.per_year = [YearStat(year=y, runs=len(v), km=round(sum(a.distance_m for a in v) / 1000),
                           hours=round(sum(a.duration_s for a in v) / 3600), dplus=round(sum(a.dplus_m or 0 for a in v)))
                  for y, v in sorted(by_year.items())]
    for label, lo, hi in RECORD_BANDS:
        cands = [a for a in runs if lo <= a.distance_m <= hi and dplus_per_km(a) < FLAT_DPLUS_PER_KM]
        best = min(cands, key=run_time, default=None)
        if best:
            h.records.append(Effort(label=label, name=best.name or best.sport, day=best.local_day,
                                    distance_km=round(best.distance_m / 1000, 2), time_s=run_time(best)))
    flat = [a for a in runs if a.distance_m >= 5000 and dplus_per_km(a) < FLAT_DPLUS_PER_KM]
    b10 = min(flat, key=eq_10k, default=None)
    if b10:
        h.best_10k_eq_s, h.best_10k_eq_day = round(eq_10k(b10)), b10.local_day
    ultras = [a for a in runs if a.duration_s >= 6 * 3600 or a.distance_m >= 60_000]
    h.ultras = [Effort(label="Ultra", name=a.name or a.sport, day=a.local_day, distance_km=round(a.distance_m / 1000),
                       time_s=a.duration_s, dplus_m=round(a.dplus_m or 0))
                for a in sorted(ultras, key=lambda a: -a.distance_m)[:8]]
    weeks: dict[date, float] = {}
    for a in runs:
        if a.duration_s >= 6 * 3600:                  # une course de 30 h n'est pas une semaine d'entraînement
            continue
        m = a.local_day - timedelta(days=a.local_day.weekday())
        weeks[m] = weeks.get(m, 0.0) + a.duration_s / 60
    if weeks:
        pk = max(weeks, key=weeks.get)
        h.peak_week_min, h.peak_week_day = round(weeks[pk]), pk
        y1 = today - timedelta(days=365)
        h.peak_week_12m_min = round(max((v for k, v in weeks.items() if k >= y1), default=0))
        mondays = sorted(k for k in weeks if k >= y1)
        h.peak_4w_hours = round(max((sum(weeks.get(m + timedelta(weeks=i), 0) for i in range(4)) / 60
                                     for m in mondays), default=0), 1)
    days = sorted({a.local_day for a in runs})
    for a, b in zip(days, days[1:]):
        if (b - a).days >= 21:
            h.gaps.append(Gap(start=a + timedelta(days=1), end=b - timedelta(days=1), days=(b - a).days - 1))
    return h


class RecentRun(BaseModel):
    """Une sortie de course des dernières semaines (v0.36) : la 1re semaine du plan part de ce réel."""
    day: date
    name: str = ""
    minutes: float                     # temps écoulé (comme le contrôle Frandsen des garde-fous)
    km: float
    dplus: int = 0
    quality: bool = False
    race: bool = False                 # intitulé de course, ou effort plat au niveau de la référence


class Diagnosis(BaseModel):
    today: date
    first_day: date | None
    last_day: date | None
    n_activities: int
    n_runs: int
    weeks: int = 12
    run_hours_per_week: float
    run_km_per_week: float
    dplus_per_week: float
    trail_share: float                 # part du temps de course en trail
    quality_recent: int                # séances de qualité, 12 dernières semaines
    quality_previous: int              # les 12 semaines d'avant
    quality_per_week: float
    last_quality: date | None
    last_quality_name: str = ""
    reference: Reference | None = None
    vma_kmh: float | None = None       # estimation : 10 km ≈ 87 % de la VMA (hypothèse)
    paces: dict[str, str] = Field(default_factory=dict)
    marathon: Reference | None = None
    marathon_gap: float | None = None  # réel / prédit - 1
    recent_big_effort: Reference | None = None
    days_since_big_effort: int | None = None
    typical_week_min: float = 0.0
    typical_days_per_week: int = 4
    # v0.15 : ce que Strava dit du reste de la semaine (pour les questions de l'accueil)
    other_sports: dict[str, float] = Field(default_factory=dict)   # sport -> heures par semaine (hors course)
    strength_per_week: float = 0.0                                 # séances « Renfo » enregistrées
    hr_share: float = 0.0                                          # part des sorties récentes avec FC
    rare_run_days: list[int] = Field(default_factory=list)         # jours (0 = lundi) presque jamais courus
    history: History = Field(default_factory=History)               # v0.18 : profil long terme (5 ans)
    # v0.36 : sorties des 44 derniers jours (None = bilan enregistré avant la v0.36)
    recent_runs: list[RecentRun] | None = None
    findings: list[Finding] = Field(default_factory=list)
    focus: str = "équilibre"           # "vitesse" | "endurance de vitesse" | "volume" | "équilibre"


def is_run(a: Activity) -> bool:
    """Sortie de course retenue dans les statistiques (v0.48 : hors sorties douteuses, voir store.sanitize)."""
    return (a.sport in RUN_SPORTS and a.distance_m > 500 and a.duration_s > 300
            and not getattr(a, "suspect", False))


def dplus_per_km(a: Activity) -> float:
    return (a.dplus_m or 0) / max(a.distance_m / 1000, 0.1)


def run_time(a: Activity) -> float:
    """Temps en mouvement si connu (pauses exclues), sinon temps écoulé."""
    return a.moving_s or a.duration_s


def speed(a: Activity) -> float:
    t = run_time(a)
    return a.distance_m / t if t else 0.0


def eq_10k(a: Activity) -> float:
    return run_time(a) * (10_000 / a.distance_m) ** RIEGEL


def fmt_time(s: float) -> str:
    s = int(round(s))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h} h {m:02d}" if h else f"{m}:{sec:02d}"


def pace(v_ms: float) -> str:
    s = 1000 / v_ms
    return f"{int(s // 60)}:{int(round(s % 60)):02d}"


def fr(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def _ref(a: Activity, eq: float | None = None) -> Reference:
    return Reference(name=a.name or a.sport, day=a.local_day, distance_km=round(a.distance_m / 1000, 2),
                     time_s=run_time(a), equivalent_10k_s=round(eq if eq is not None else eq_10k(a)))


def is_quality(a: Activity, v_ref: float | None) -> bool:
    if not is_run(a):
        return False
    if QUALITY_NAME.search(a.name or ""):
        return True
    flat = dplus_per_km(a) < FLAT_DPLUS_PER_KM
    return bool(v_ref) and flat and a.distance_m >= 3000 and speed(a) >= 0.88 * v_ref


def is_race_like(a: Activity, reference: Reference | None, usual_speed: float = 0.0) -> bool:
    """Course probable : intitulé de course, ou effort plat d'au moins 5 km couru au niveau de la référence
    (équivalent 10 km à moins de 3 % du meilleur des 9 derniers mois) et nettement plus vite que d'habitude
    (+10 % sur la vitesse médiane des sorties récentes)."""
    if RACE_NAME.search(a.name or ""):
        return True
    return bool(reference) and a.distance_m >= 5000 and dplus_per_km(a) < FLAT_DPLUS_PER_KM \
        and eq_10k(a) <= 1.03 * reference.equivalent_10k_s and speed(a) >= 1.10 * usual_speed


def diagnose(acts: list[Activity], today: date, weeks: int = 12) -> Diagnosis:
    runs = [a for a in acts if is_run(a)]
    recent_start = today - timedelta(weeks=weeks)
    prev_start = recent_start - timedelta(weeks=weeks)
    recent = [a for a in runs if recent_start <= a.local_day <= today]
    prev = [a for a in runs if prev_start <= a.local_day < recent_start]

    # référence de vitesse : meilleur effort plat >= 5 km sur 9 mois, ramené à 10 km
    flat = [a for a in runs if a.local_day >= today - timedelta(days=270) and a.distance_m >= 5000
            and dplus_per_km(a) < FLAT_DPLUS_PER_KM]
    best = min(flat, key=eq_10k, default=None)
    reference = _ref(best) if best else None
    v10 = 10_000 / reference.equivalent_10k_s if reference else None

    q_recent = [a for a in recent if is_quality(a, v10)]
    q_prev = [a for a in prev if is_quality(a, v10)]
    last_q = max((a for a in runs if is_quality(a, v10)), key=lambda a: a.start_utc, default=None)

    run_s = sum(a.duration_s for a in recent)
    trail_s = sum(a.duration_s for a in recent if a.sport == "Trail" or dplus_per_km(a) > TRAIL_DPLUS_PER_KM)

    # semaine type : médiane des semaines (lun-dim) de la fenêtre, hors semaines vides
    per_week: dict[date, list[Activity]] = {}
    for a in recent:
        per_week.setdefault(a.local_day - timedelta(days=a.local_day.weekday()), []).append(a)
    week_min = sorted(sum(x.duration_s for x in v) / 60 for v in per_week.values())
    week_days = sorted(len({x.local_day for x in v}) for v in per_week.values())

    # marathon : effort plat de 41 à 43,5 km
    mara = min((a for a in runs if 41_000 <= a.distance_m <= 43_500 and dplus_per_km(a) < FLAT_DPLUS_PER_KM
                and a.local_day >= today - timedelta(days=365)), key=run_time, default=None)
    gap = None
    if mara and reference:
        predicted = reference.equivalent_10k_s * (42_195 / 10_000) ** RIEGEL
        gap = run_time(mara) * (42_195 / mara.distance_m) / predicted - 1

    big = max((a for a in acts if a.local_day >= today - timedelta(days=21) and not getattr(a, "suspect", False)
               and (a.duration_s >= 6 * 3600 or a.distance_m >= 60_000)), key=lambda a: a.duration_s, default=None)

    d = Diagnosis(
        today=today, first_day=min((a.local_day for a in acts), default=None),
        last_day=max((a.local_day for a in acts), default=None), n_activities=len(acts), n_runs=len(runs),
        weeks=weeks, run_hours_per_week=round(run_s / 3600 / weeks, 1),
        run_km_per_week=round(sum(a.distance_m for a in recent) / 1000 / weeks, 1),
        dplus_per_week=round(sum(a.dplus_m or 0 for a in recent) / weeks),
        trail_share=round(trail_s / run_s, 2) if run_s else 0.0,
        quality_recent=len(q_recent), quality_previous=len(q_prev),
        quality_per_week=round(len(q_recent) / weeks, 2),
        last_quality=last_q.local_day if last_q else None, last_quality_name=last_q.name if last_q else "",
        reference=reference, marathon=_ref(mara) if mara else None,
        marathon_gap=round(gap, 3) if gap is not None else None,
        recent_big_effort=_ref(big) if big else None,
        days_since_big_effort=(today - big.local_day).days if big else None,
        typical_week_min=round(statistics.median(week_min)) if week_min else 0.0,
        typical_days_per_week=int(statistics.median(week_days)) if week_days else 4,
    )
    others = [a for a in acts if not is_run(a) and recent_start <= a.local_day <= today
              and not getattr(a, "suspect", False)]
    by_sport: dict[str, float] = {}
    for a in others:
        if a.sport != "Renfo":
            by_sport[a.sport] = by_sport.get(a.sport, 0.0) + a.duration_s / 3600 / weeks
    d.other_sports = {k: round(v, 1) for k, v in sorted(by_sport.items(), key=lambda x: -x[1]) if v >= 0.2}
    d.strength_per_week = round(sum(1 for a in others if a.sport == "Renfo") / weeks, 1)
    d.hr_share = round(sum(1 for a in recent if a.avg_hr) / len(recent), 2) if recent else 0.0
    if per_week:
        d.rare_run_days = [dw for dw in range(7)
                           if sum(1 for v in per_week.values() if any(x.local_day.weekday() == dw for x in v))
                           / len(per_week) < 0.15]
    if v10:
        d.vma_kmh = round(v10 * 3.6 / 0.87, 1)
        d.paces = {"vma": pace(v10 / 0.87), "10 km": pace(v10), "seuil": pace(v10 * 0.94),
                   "marathon": pace(v10 * (10_000 / 42_195) ** (RIEGEL - 1)),
                   "endurance": f"{pace(v10 * 0.76)}–{pace(v10 * 0.70)}"}
    d.history = history(acts, today)
    usual_speed = statistics.median(speed(a) for a in recent) if recent else 0.0
    d.recent_runs = [RecentRun(day=a.local_day, name=(a.name or a.sport).strip(), minutes=round(a.duration_s / 60, 1),
                               km=round(a.distance_m / 1000, 2), dplus=round(a.dplus_m or 0),
                               quality=is_quality(a, v10), race=is_race_like(a, reference, usual_speed))
                     for a in sorted(runs, key=lambda a: a.start_utc)
                     if today - timedelta(days=RECENT_DAYS) <= a.local_day <= today]
    d.findings = _findings(d, q_recent, q_prev)
    d.focus = _focus(d)
    return d


def _findings(d: Diagnosis, q_recent: list[Activity], q_prev: list[Activity]) -> list[Finding]:
    f: list[Finding] = []
    if d.recent_big_effort:
        r = d.recent_big_effort
        f.append(Finding(id="recuperation", severity="high", title="Récupération d'abord",
                         detail=f"{r.name} il y a {d.days_since_big_effort} jours ({fr(r.distance_km, 0)} km, "
                                f"{fmt_time(r.time_s)}). Les muscles et les tendons ont besoin de plusieurs "
                                "semaines, même quand les sensations reviennent vite."))
    if d.quality_per_week < 1:
        trend = ""
        if d.quality_previous >= 2 * max(d.quality_recent, 1):
            trend = f" Les 12 semaines d'avant, il y en avait {d.quality_previous} : la vitesse a été mise de côté."
        last = f" Dernière : « {d.last_quality_name} » le {d.last_quality:%d/%m}." if d.last_quality else ""
        f.append(Finding(id="vitesse", severity="high", title="Vitesse peu travaillée",
                         detail=f"{d.quality_recent} séance(s) de qualité en {d.weeks} semaines "
                                f"({fr(d.quality_per_week, 2)} par semaine), contre environ 2 par semaine chez "
                                f"les coureurs d'endurance entraînés.{last}{trend}"))
    if d.trail_share >= 0.6:
        f.append(Finding(id="terrain", severity="medium", title="Surtout du trail",
                         detail=f"{round(d.trail_share * 100)} % du temps de course en trail sur {d.weeks} semaines : "
                                "peu de course rapide sur le plat."))
    if d.marathon_gap is not None and d.marathon_gap > 0.06:
        m, r = d.marathon, d.reference
        f.append(Finding(id="endurance_vitesse", severity="medium", title="Décrochage au-delà d'une heure",
                         detail=f"Marathon en {fmt_time(m.time_s)} ({m.name}, {m.day:%d/%m/%Y}) : "
                                f"{round(d.marathon_gap * 100)} % plus lent que ce que prédit ton meilleur 10 km "
                                f"équivalent ({fmt_time(r.equivalent_10k_s)})."))
    if d.reference:
        r = d.reference
        f.append(Finding(id="reference", severity="info", title="Ta référence de vitesse",
                         detail=f"{r.name} ({r.day:%d/%m/%Y}) : {fr(r.distance_km, 2)} km en {fmt_time(r.time_s)}, "
                                f"soit un 10 km en {fmt_time(r.equivalent_10k_s)}. VMA estimée ~{fr(d.vma_kmh)} km/h "
                                "(hypothèse : 10 km à 87 % de la VMA, à confirmer par un test)."))
    else:
        f.append(Finding(id="reference", severity="medium", title="Pas de référence de vitesse",
                         detail="Aucun effort rapide sur le plat de 5 km ou plus : un test (10 km ou 6 min) "
                                "calera les allures."))
    hb = d.history
    if (hb.best_10k_eq_s and d.reference and hb.best_10k_eq_day and hb.best_10k_eq_day < d.today - timedelta(days=270)
            and hb.best_10k_eq_s < d.reference.equivalent_10k_s * 0.97):
        gain = 1 - hb.best_10k_eq_s / d.reference.equivalent_10k_s
        f.append(Finding(id="vitesse_passee", severity="info", title="La vitesse a déjà été là",
                         detail=f"En {hb.best_10k_eq_day.year}, ton meilleur effort valait un 10 km en "
                                f"{fmt_time(hb.best_10k_eq_s)}, {round(gain * 100)} % plus rapide qu'aujourd'hui : "
                                "c'est une qualité à réveiller plus qu'à construire."))
    if d.run_hours_per_week >= 6 and d.trail_share < 0.6 and d.quality_per_week >= 1:
        f.append(Finding(id="equilibre", severity="info", title="Base solide",
                         detail=f"{fr(d.run_hours_per_week)} h de course par semaine avec de la qualité régulière."))
    return f


def _focus(d: Diagnosis) -> str:
    ids = {x.id for x in d.findings}
    if "vitesse" in ids:
        return "vitesse"
    if "endurance_vitesse" in ids:
        return "endurance de vitesse"
    if d.run_hours_per_week < 3:
        return "volume"
    return "équilibre"
