"""Garde-fous déterministes : la sécurité est dans le code, pas dans le prompt.

Le LLM PROPOSE un diff de plan, ce module DISPOSE. Les seuils viennent du
registre versionné (`rules_registry`). Deux sévérités :
- block : relance le coach avec la violation (max 2), puis repli conservateur ;
- warn  : la proposition passe, l'avertissement est journalisé et visible du coach.
Une règle `placeholder` ne peut produire qu'un `warn`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .load_engine import LoadState, pmc
from .rules_registry import REGISTRY_VERSION, R, can_block
from .schemas import GuardrailVerdict, PlanChange, PlannedSession, Signals, Violation

HARD_INTENSITIES = {"tempo", "threshold", "vo2", "hills", "long"}
QUALITY_INTENSITIES = {"tempo", "threshold", "vo2", "hills"}


@dataclass(frozen=True)
class PlanContext:
    """Ce que le garde-fou doit savoir du macro-cycle (hors diff LLM)."""
    state: LoadState
    baseline_week_load: float                 # plan initial des 7 prochains jours
    today: date
    race_date: date | None = None
    shock_block_active: bool = False          # bloc de choc DÉCLARÉ au plan (pas par le LLM)
    pre_taper_week_load: float | None = None
    pre_taper_sessions_per_week: float | None = None
    longest_run_30d_min: float | None = None  # plus longue sortie réalisée sur 30 j (durée, v0.27)
    week_max: Any = None                      # v0.49 : week_max.WeekMax, volume maximum déclaré par l'athlète


def _sev(key: str) -> str:
    return "block" if can_block(key) else "warn"


def apply_changes(plan: list[PlannedSession], changes: list[PlanChange]) -> list[PlannedSession]:
    """Plan RÉSULTANT = plan courant + diff proposé par le LLM.

    On évalue toujours le plan complet : une séance intense que le LLM a
    simplement "oublié" de modifier doit aussi déclencher les règles.
    """
    by_id = {p.session_id: p for p in plan}
    for c in changes:
        if c.action == "cancel":
            by_id.pop(c.session.session_id, None)
        elif c.action != "keep":
            by_id[c.session.session_id] = c.session
    return sorted(by_id.values(), key=lambda p: p.day)


def _project(state: LoadState, sessions: list[PlannedSession], today: date,
             horizon: int = 7) -> list[LoadState]:
    loads = [0.0] * horizon
    for s in sessions:
        d = (s.day - today).days
        if 0 <= d < horizon and not s.is_race:
            loads[d] += s.planned_load
    return pmc(loads, ctl0=state.ctl, atl0=state.atl, a7_0=state.a7, c28_0=state.c28)


def _acwr_rules(peak: float, low: float, ctx: PlanContext, in_taper: bool) -> list[Violation]:
    """Zones ACWR (EWMA 7:28) :
    > 1,7            : bloqué, toujours ;
    1,5 - 1,7        : alerte en bloc de choc déclaré, bloqué sinon ;
    1,3 - 1,5        : autorisé en bloc de choc déclaré, bloqué sinon ;
    < 0,8            : alerte (sauf affûtage).
    """
    v = []
    if peak > R("acwr.danger_block"):
        v.append(Violation(rule_id="R03_acwr_danger", severity=_sev("acwr.danger_block"),
                           detail=f"ACWR projeté {peak:.2f} > {R('acwr.danger_block')} (blocage)"))
    elif peak > R("acwr.danger_warn"):
        if ctx.shock_block_active:
            v.append(Violation(rule_id="R03_acwr_high", severity="warn",
                               detail=f"ACWR projeté {peak:.2f} > {R('acwr.danger_warn')} en bloc de choc"))
        else:
            v.append(Violation(rule_id="R03_acwr_vigilance",
                               severity=_sev("acwr.vigilance_requires_shock_block"),
                               detail=f"ACWR projeté {peak:.2f} hors bloc de choc déclaré"))
    elif peak > R("acwr.sweet_hi") and not ctx.shock_block_active:
        v.append(Violation(rule_id="R03_acwr_vigilance",
                           severity=_sev("acwr.vigilance_requires_shock_block"),
                           detail=f"ACWR projeté {peak:.2f} en zone de vigilance hors bloc de choc déclaré"))
    if low < R("acwr.under") and not in_taper:
        v.append(Violation(rule_id="R03_acwr_under", severity="warn",
                           detail=f"ACWR projeté {low:.2f} < {R('acwr.under')} (sous-charge)"))
    return v


def _recovery_rules(hard: list[PlannedSession], signals: Signals, ctx: PlanContext) -> list[Violation]:
    """Récupération compromise (alcool, sommeil) et stress de vie : pas d'intensité à court terme."""
    v: list[Violation] = []
    def within(hours):
        return [s for s in hard if (s.day - ctx.today).days * 24 < hours]
    poor_sleep = ((signals.sleep_hours is not None and signals.sleep_hours < R("recovery.min_sleep_hours"))
                  or (signals.sleep_quality_1_5 is not None
                      and signals.sleep_quality_1_5 < R("recovery.min_sleep_quality")))
    if (signals.alcohol == "heavy" or poor_sleep) and within(R("recovery.no_intensity_hours")):
        cause = "alcool important" if signals.alcohol == "heavy" else "nuit trop courte"
        v.append(Violation(rule_id="R07_recovery", severity=_sev("recovery.no_intensity_hours"),
                           detail=f"{cause} + séance intense dans les {R('recovery.no_intensity_hours')} h"))
    if ((signals.life_stress_1_5 or 0) > R("recovery.max_life_stress_for_intensity")) and within(24):
        v.append(Violation(rule_id="R08_life_stress", severity=_sev("recovery.max_life_stress_for_intensity"),
                           detail=f"stress de vie {signals.life_stress_1_5}/5 + séance intense dans les 24 h"))
    return v


def _taper_rules(active: list[PlannedSession], ctx: PlanContext) -> list[Violation]:
    """Affûtage progressif : -41 à -60 % de volume, intensité et fréquence maintenues."""
    v: list[Violation] = []
    if not ctx.race_date or ctx.pre_taper_week_load is None:
        return v
    days_to_race = (ctx.race_date - ctx.today).days
    taper_days = R("taper.days")
    if not 0 < days_to_race <= taper_days + 7:
        return v

    window = [s for s in active if 0 <= (s.day - ctx.today).days < 7 and not s.is_race]
    week_load = sum(s.planned_load for s in window)
    # Progression évaluée au milieu de la fenêtre de 7 jours : 0 au début de
    # l'affûtage, 1 le jour de la course -> réduction cible croissante.
    progress = min(1.0, max(0.0, 1 - (days_to_race - 3.5) / taper_days))
    ceiling = ctx.pre_taper_week_load * (1 - R("taper.volume_reduction_min") * progress)
    floor = ctx.pre_taper_week_load * (1 - R("taper.volume_reduction_max"))

    if week_load > ceiling * 1.05:
        v.append(Violation(rule_id="R06_taper_volume", severity=_sev("taper.volume_reduction_min"),
                           detail=f"charge 7 j {week_load:.0f} > plafond d'affûtage {ceiling:.0f}"))
    if progress >= 0.5 and week_load < floor:
        v.append(Violation(rule_id="R06_taper_over", severity="warn",
                           detail=f"réduction > {R('taper.volume_reduction_max'):.0%} : sur-affûtage"))
    if R("taper.keep_intensity") and days_to_race > 3 and not any(
            s.intensity in QUALITY_INTENSITIES for s in window):
        v.append(Violation(rule_id="R06_taper_intensity", severity="warn",
                           detail="aucune séance de qualité : l'intensité doit être maintenue"))
    if R("taper.keep_frequency") and ctx.pre_taper_sessions_per_week:
        n = sum(1 for s in window if s.intensity != "rest")
        if n < ctx.pre_taper_sessions_per_week * R("taper.frequency_tolerance"):
            v.append(Violation(rule_id="R06_taper_frequency", severity="warn",
                               detail=f"{n} séances vs {ctx.pre_taper_sessions_per_week:.0f} habituelles"))
    return v


def _is_peak_candidate(s: PlannedSession) -> bool:
    """Sorties contrôlées : ni repos, ni course (jour J), ni séance bonus (charge 0), ni sortie courte."""
    return (s.intensity not in ("rest", "race") and not s.is_race and s.planned_load > 0
            and s.duration_min >= R("session.peak_floor_min"))


def _peak_rules(active: list[PlannedSession], ctx: PlanContext) -> list[Violation]:
    """Pic sur une séance (Frandsen 2025) : une sortie qui dépasse de plus de 10 % la plus longue des
    30 derniers jours augmente le risque de blessure. S'ajoute à l'ACWR, qui ne voit pas ce pic."""
    ref = ctx.longest_run_30d_min
    if not ref or ref <= 0:
        return []
    v = []
    for s in active:
        if not _is_peak_candidate(s):
            continue
        ratio = s.duration_min / ref
        if ratio > R("session.peak_warn_ratio"):
            v.append(Violation(
                rule_id="R09_peak_session", severity="warn",
                detail=f"{s.day:%d/%m} : {s.duration_min} min = {ratio:.0%} de ta plus longue sortie des "
                       f"{R('session.peak_window_days')} derniers jours ({ref:.0f} min) ; "
                       f"plafond conseillé {ref * R('session.peak_warn_ratio'):.0f} min"))
    return v


def _week_max_rules(active: list[PlannedSession], ctx: PlanContext,
                    plan_before: list[PlannedSession] | None) -> list[Violation]:
    """v0.49 : semaine (7 jours à partir d'aujourd'hui) au-delà du volume maximum que l'athlète a déclaré (temps,
    km ou D+ estimés). Si le plan d'avant dépassait déjà, seule une proposition qui AJOUTE du volume est signalée."""
    wm = ctx.week_max
    if wm is None or not wm.any():
        return []
    from .week_max import over_text, totals

    def window(plan):
        return [s for s in plan if 0 <= (s.day - ctx.today).days < 7]
    tol = R("week.max_tolerance")
    tot = totals(window(active), wm)
    over = over_text(tot, wm, tol)
    if not over:
        return []
    if plan_before is not None:
        b = totals(window([s for s in plan_before if s.intensity != "rest"]), wm)
        if all(tot[i] <= b[i] + 1 for i in range(3)):
            return []
    return [Violation(rule_id="R10_week_max", severity=_sev("week.max_tolerance"),
                      detail="7 prochains jours au-delà du volume maximum de l'athlète : " + " ; ".join(over))]


def cap_peak_sessions(changes: list[PlanChange], ctx: PlanContext) -> tuple[list[PlanChange], list[str]]:
    """Ramène à 110 % les séances proposées au-delà de 130 % de la plus longue sortie du mois.
    Renvoie les changements corrigés et la liste des explications (à ajouter au message)."""
    ref = ctx.longest_run_30d_min
    if not ref or ref <= 0:
        return changes, []
    out, notes = [], []
    cap = int(ref * R("session.peak_warn_ratio"))
    for c in changes:
        s = c.session
        if (c.action in ("add", "modify", "move") and _is_peak_candidate(s)
                and s.duration_min > ref * R("session.peak_cap_ratio") and cap >= R("session.peak_floor_min")):
            k = cap / s.duration_min
            s2 = s.model_copy(update={
                "duration_min": cap, "elevation_gain_m": int(s.elevation_gain_m * k),
                "planned_load": round(s.planned_load * k, 1),
                "description": (s.description + " " if s.description else "") + "(plafonnée à +10 % de ta plus longue sortie du mois)"})
            out.append(c.model_copy(update={"session": s2}))
            notes.append(f"Séance du {s.day:%d/%m} ramenée de {s.duration_min} à {cap} min : une sortie qui dépasse "
                         "de plus de 10 % ta plus longue sortie du mois augmente le risque de blessure (Frandsen 2025).")
        else:
            out.append(c)
    return out, notes


def check(plan_after: list[PlannedSession], signals: Signals, ctx: PlanContext,
          plan_before: list[PlannedSession] | None = None) -> GuardrailVerdict:
    """`plan_before` (v0.49) : le plan avant la proposition, pour R10 (un plan existant au-delà du plafond ne fait
    pas refuser une proposition qui n'ajoute rien)."""
    v: list[Violation] = []
    active = [s for s in plan_after if s.intensity != "rest"]
    hard = [s for s in active if s.intensity in HARD_INTENSITIES and not s.is_race]
    # La fenêtre de 7 jours évaluée chevauche-t-elle l'affûtage ? (sinon on
    # signalerait à tort une "sous-charge" pendant la réduction volontaire)
    in_taper = bool(ctx.race_date and 0 < (ctx.race_date - ctx.today).days <= R("taper.days") + 7)

    worst_pain = max((p.severity_0_10 for p in signals.pain), default=0)
    if worst_pain > R("pain.max_for_intensity") and hard:
        v.append(Violation(rule_id="R01_pain", severity=_sev("pain.max_for_intensity"),
                           detail=f"douleur {worst_pain}/10 et {len(hard)} séance(s) intenses"))
    if signals.illness_symptoms and hard:
        v.append(Violation(rule_id="R02_illness",
                           detail=f"symptômes {signals.illness_symptoms} + intensité planifiée"))

    proj = _project(ctx.state, active, ctx.today)
    if proj:
        v += _acwr_rules(max(p.acwr for p in proj), min(p.acwr for p in proj), ctx, in_taper)
        min_tsb = min(p.tsb for p in proj)
        if min_tsb < R("load.tsb_floor"):
            v.append(Violation(rule_id="R04_tsb", severity=_sev("load.tsb_floor"),
                               detail=f"TSB projeté {min_tsb:.1f}"))

    week = sum(s.planned_load for s in active
               if 0 <= (s.day - ctx.today).days < 7 and not s.is_race)
    if ctx.baseline_week_load > 0 and week > ctx.baseline_week_load * (1 + R("load.catch_up_tolerance")):
        # Exigence de la spec => bloquant même si la tolérance est un placeholder.
        v.append(Violation(rule_id="R05_catch_up",
                           detail=f"semaine {week:.0f} vs plan initial {ctx.baseline_week_load:.0f}"))

    v += _recovery_rules(hard, signals, ctx)
    v += _taper_rules(active, ctx)
    v += _peak_rules(active, ctx)
    v += _week_max_rules(active, ctx, plan_before)
    return GuardrailVerdict(allowed=not any(x.severity == "block" for x in v),
                            violations=v, registry_version=REGISTRY_VERSION)


# v0.50.1 : règles liées à l'état de l'athlète (signaux du message) : elles bloquent toujours, même si le plan
# actuel les enfreint déjà (le coach doit alléger). Les autres (charge, volume, rattrapage…) déjà enfreintes par
# le plan actuel ne bloquent plus une proposition : sinon toute réponse, même « je ne change rien », était
# refusée et le repli de sécurité s'appliquait (semaine entière passée en footing après une séance de seuil).
SIGNAL_RULES = {"R01_pain", "R02_illness", "R07_recovery", "R08_life_stress"}


def tolerate_preexisting(verdict: GuardrailVerdict, before: GuardrailVerdict, tol: set[str] | None = None
                         ) -> GuardrailVerdict:
    """Violations bloquantes déjà présentes dans le plan actuel -> avertissement (sauf signaux de l'athlète).
    `tol` (réactions en tâche de fond) : « * » ou des règles tolérées même si ce sont des signaux."""
    tol = tol or set()
    pre = {v.rule_id for v in before.blocking}
    vs = [v.model_copy(update={"severity": "warn"}) if v.severity == "block" and v.rule_id in pre
          and (v.rule_id not in SIGNAL_RULES or "*" in tol or v.rule_id in tol) else v for v in verdict.violations]
    return verdict.model_copy(update={"violations": vs, "allowed": not any(v.severity == "block" for v in vs)})


def at_risk(signals: Signals | None) -> bool:
    """Signaux qui justifient d'alléger d'office : douleur > 3/10, maladie, grosse fatigue, sommeil très court."""
    if signals is None:
        return False
    worst = max((p.severity_0_10 for p in signals.pain), default=0)
    return bool(worst > 3 or signals.illness_symptoms or (signals.fatigue_1_5 or 0) >= 4
                or (signals.sleep_hours is not None and signals.sleep_hours < 5) or signals.alcohol == "heavy")


def safe_fallback(plan: list[PlannedSession], signals: Signals | None = None, today: date | None = None,
                  days: int = 2) -> list[PlanChange]:
    """Repli si le LLM échoue 2 fois aux garde-fous.

    v0.50.1 : on ne réécrit plus toute la semaine. Sans signal de risque, le plan actuel est GARDÉ (la
    proposition refusée est simplement abandonnée). Avec un signal de risque (douleur, maladie, fatigue…),
    seules les séances intenses des `days` prochains jours passent en footing très facile (coût d'erreur
    asymétrique, spec §5) ; la course reste.
    """
    if not at_risk(signals):
        return []
    start = today or (min((s.day for s in plan), default=None))
    out = []
    for s in plan:
        if s.intensity not in HARD_INTENSITIES or s.is_race or start is None or (s.day - start).days >= days:
            continue
        safe = s.model_copy(update={
            "intensity": "z1",
            "duration_min": min(s.duration_min, 45),
            "elevation_gain_m": 0,
            "planned_load": min(s.planned_load, 35.0),
            "description": "Footing très facile ou repos (repli sécurité).",
        })
        out.append(PlanChange(action="modify", session=safe, reason="fallback_guardrail"))
    return out


def next_days(today: date, n: int) -> list[date]:
    return [today + timedelta(days=i) for i in range(n)]
