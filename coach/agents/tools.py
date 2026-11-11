"""Outils des spécialistes (v0.40) : de vraies fonctions Python, exécutées quand l'agent les appelle.

Chaque outil a un nom, une description (lue par le modèle), un schéma d'entrée Pydantic (→ `input_schema` de
l'API Anthropic) et une fonction. `ToolBox` exécute l'appel, chronomètre (trace `{name, ms}`) et ne laisse
jamais une exception d'outil casser un tour (le résultat devient `{"erreur": ...}`, renvoyé au modèle).
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..guardrails import HARD_INTENSITIES, PlanContext, apply_changes, check
from ..rules_registry import R
from ..schemas import Intensity, PlanChange, PlannedSession, Signals

JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


# --------------------------------------------------------------------------- schémas d'entrée
class NoArgs(BaseModel):
    pass


class KnowledgeIn(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=8,
                            description="Thèmes : load, acwr, missed_sessions, pain, injury, sleep, stress, recovery, "
                                        "nutrition, carbs, gi, gut, hydration, heat, taper, race, trail, ultra, durability, hrv")
    query: str = Field(default="", max_length=300, description="Question en français (thèmes déduits des mots-clés)")
    k: int = Field(default=4, ge=1, le=6)


class DaysIn(BaseModel):
    days: int = Field(default=14, ge=1, le=63)


class PlanDaysIn(BaseModel):
    days: int = Field(default=7, ge=7, le=14)


class WeeksIn(BaseModel):
    weeks: int = Field(default=6, ge=1, le=12)


class CandidateChange(BaseModel):
    action: Literal["modify", "add", "cancel"]
    session_id: str = Field(max_length=120)
    day: date | None = None
    intensity: Intensity | None = None
    duration_min: int | None = Field(default=None, ge=0, le=600)
    planned_load: float | None = Field(default=None, ge=0, le=600)


class DryRunIn(BaseModel):
    changes: list[CandidateChange] = Field(default_factory=list, max_length=7)


class CarbsIn(BaseModel):
    duration_h: float = Field(default=3.0, gt=0, le=48)


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]
    params: type[BaseModel] = NoArgs

    def spec(self) -> dict:
        from ..llm import tool_schema  # v0.48 : schéma autonome (sans $defs)
        return {"name": self.name, "description": self.description, "input_schema": tool_schema(self.params)}


@dataclass
class ToolCtx:
    """Ce qu'un outil peut lire : dépendances du graphe + message du tour."""
    deps: Any                                       # graph.Deps (repo, retriever, data, today)
    athlete_id: str
    signals: Signals
    message: str = ""
    _ctx: Any = None

    @property
    def athlete(self):
        if self._ctx is None:
            self._ctx = self.deps.repo.get(self.athlete_id)
        return self._ctx

    @property
    def data(self):
        from .data import NullData
        return getattr(self.deps, "data", None) or NullData(self.deps.today)


@dataclass
class ToolBox:
    tools: list[Tool]
    calls: list[dict] = field(default_factory=list)
    seen_cards: set[str] = field(default_factory=set)
    degraded: str | None = None          # raison du repli hors ligne de cet agent (FallbackLLM)

    def by_name(self, name: str) -> Tool | None:
        return next((t for t in self.tools if t.name == name), None)

    def call(self, name: str, args: dict | None = None) -> Any:
        t0 = time.perf_counter()
        tool = self.by_name(name)
        try:
            if tool is None:
                out: Any = {"erreur": f"outil inconnu : {name}"}
            else:
                out = tool.fn(**tool.params.model_validate(args or {}).model_dump())
        except Exception as e:                                   # noqa: BLE001 : renvoyé au modèle, jamais levé
            out = {"erreur": f"{type(e).__name__} : {str(e)[:200]}"}
        self.calls.append({"name": name, "ms": round((time.perf_counter() - t0) * 1000, 1)})
        if name == "knowledge_search" and isinstance(out, list):
            self.seen_cards |= {c["id"] for c in out if isinstance(c, dict) and "id" in c}
        return out


# --------------------------------------------------------------------------- utilitaires
def _num(x, nd: int = 2):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, nd)


def plan_context(ctx, today: date) -> PlanContext:
    return PlanContext(state=ctx.load, baseline_week_load=ctx.baseline_week_load, today=today,
                       race_date=ctx.race_date, shock_block_active=ctx.shock_block_active,
                       pre_taper_week_load=ctx.pre_taper_week_load,
                       pre_taper_sessions_per_week=ctx.pre_taper_sessions_per_week,
                       longest_run_30d_min=ctx.longest_run_30d_min,
                       week_max=getattr(ctx, "week_max", None))


def acwr_zone(acwr) -> str:
    if acwr is None:
        return "inconnu (historique court)"
    if acwr > R("acwr.danger_block"):
        return "danger"
    if acwr > R("acwr.danger_warn"):
        return "élevé"
    if acwr > R("acwr.sweet_hi"):
        return "vigilance"
    if acwr < R("acwr.under"):
        return "sous-charge"
    return "zone optimale"


def session_view(s: PlannedSession) -> dict:
    return {"session_id": s.session_id, "day": s.day.isoformat(), "jour": JOURS[s.day.weekday()],
            "intensity": s.intensity, "duration_min": s.duration_min, "planned_load": s.planned_load,
            "is_race": s.is_race, "description": s.description[:80]}


# --------------------------------------------------------------------------- outils
def _knowledge(tc: ToolCtx) -> Tool:
    def run(tags: list[str], query: str, k: int):
        from ..knowledge import tags_for
        tg = set(tags) | tags_for(query or "")
        if not tg:
            tg = tags_for(tc.message) or {"load"}
        ev = tc.deps.retriever.search(query or tc.message, tg, k)
        return [{"id": e.id, "source": e.source[:90], "texte": e.text[:420]} for e in ev
                if not e.text.startswith("[PLACEHOLDER")]
    return Tool("knowledge_search", "Recherche dans la base de fiches scientifiques sourcées (thèmes et/ou question). "
                "Cite ensuite les `id` des fiches utilisées dans evidence_ids.", run, KnowledgeIn)


def _load_state(tc: ToolCtx) -> Tool:
    def run():
        ld = tc.athlete.load
        acwr = _num(ld.acwr)
        return {"ctl": _num(ld.ctl, 1), "atl": _num(ld.atl, 1), "tsb": _num(ld.ctl - ld.atl, 1), "acwr": acwr,
                "zone_acwr": acwr_zone(acwr), "charge_7j": _num(ld.a7, 1), "charge_28j": _num(ld.c28, 1),
                "seuils": {"vigilance": R("acwr.sweet_hi"), "eleve": R("acwr.danger_warn"),
                           "blocage": R("acwr.danger_block")}}
    return Tool("load_state", "État de charge : forme (CTL), fatigue (ATL), fraîcheur (TSB), ACWR et sa zone.", run)


def _plan_7d(tc: ToolCtx) -> Tool:
    def run(days: int):
        ctx = tc.athlete
        today = tc.deps.today()
        week = list(ctx.plan_next_7d)
        later = [s for s in tc.data.plan(today + timedelta(days=7), today + timedelta(days=days - 1))
                 if s.session_id not in {x.session_id for x in week}] if days > 7 else []
        return {"days_to_race": (ctx.race_date - today).days if ctx.race_date else None,
                "sessions": [session_view(s) for s in week],
                "next_days": [session_view(s) for s in later],
                "hard_sessions": sum(1 for s in week if s.intensity in HARD_INTENSITIES),
                "planned_load": round(sum(s.planned_load for s in week if not s.is_race))}
    return Tool("plan_7d", "Plan des 7 prochains jours (séances avec leur titre, intensité, durée, charge prévue) ; "
                "`days` jusqu'à 14 ajoute la semaine suivante en lecture (`next_days`).", run, PlanDaysIn)


def _weekly_loads(tc: ToolCtx) -> Tool:
    def run(weeks: int):
        return tc.data.weekly_loads(weeks)
    return Tool("weekly_loads", "Charge, km et heures par semaine sur les dernières semaines.", run, WeeksIn)


def _recent(tc: ToolCtx) -> Tool:
    def run(days: int):
        acts = tc.data.activities(days)
        return {"count": len(acts), "minutes": sum(a.get("min", 0) for a in acts),
                "km": round(sum(a.get("km", 0) for a in acts), 1),
                "longest_min": max((a.get("min", 0) for a in acts), default=0), "last": acts[-6:]}
    return Tool("recent_activities", "Résumé des sorties réalisées sur les N derniers jours.", run, DaysIn)


def _adherence(tc: ToolCtx) -> Tool:
    def run(weeks: int):
        return tc.data.adherence(weeks)
    return Tool("plan_adherence", "Séances prévues / faites / sautées par semaine.", run, WeeksIn)


def _dry_run(tc: ToolCtx) -> Tool:
    def run(changes: list[dict]):
        ctx = tc.athlete
        today = tc.deps.today()
        by_id = {s.session_id: s for s in ctx.plan_next_7d}
        out: list[PlanChange] = []
        for c in changes:
            base = by_id.get(c["session_id"])
            if c["action"] in ("modify", "cancel") and base is None:
                continue
            if base is None:
                if not c.get("day") or not c.get("intensity"):
                    continue
                base = PlannedSession(session_id=c["session_id"], day=c["day"], intensity=c["intensity"],
                                      duration_min=c.get("duration_min") or 60, planned_load=c.get("planned_load") or 60)
            upd = {k: c[k] for k in ("day", "intensity", "duration_min", "planned_load") if c.get(k) is not None}
            out.append(PlanChange(action=c["action"], session=base.model_copy(update=upd), reason="essai"))
        v = check(apply_changes(ctx.plan_next_7d, out), tc.signals, plan_context(ctx, today),
                  plan_before=ctx.plan_next_7d)
        return {"allowed": v.allowed, "violations": [{"rule": x.rule_id, "detail": x.detail, "severity": x.severity}
                                                     for x in v.violations]}
    return Tool("guardrails_dry_run", "Teste un changement candidat contre les garde-fous déterministes (sans "
                "l'appliquer) : autorisé ou non, et pourquoi.", run, DryRunIn)


def _wellbeing(tc: ToolCtx) -> Tool:
    def run():
        s = tc.signals
        return {"pain": [p.model_dump() for p in s.pain], "illness": s.illness_symptoms, "sleep_hours": s.sleep_hours,
                "sleep_quality_1_5": s.sleep_quality_1_5, "fatigue_1_5": s.fatigue_1_5,
                "life_stress_1_5": s.life_stress_1_5, "emotional_distress_1_5": s.emotional_distress_1_5,
                "alcohol": s.alcohol, "rpe_last_session": s.rpe_last_session,
                "seuils": {"douleur_max_intensite": R("pain.max_for_intensity"),
                           "sommeil_min_h": R("recovery.min_sleep_hours"),
                           "stress_max_intensite": R("recovery.max_life_stress_for_intensity")}}
    return Tool("wellbeing_signals", "Signaux du jour extraits du message : douleur, maladie, sommeil, fatigue, "
                "stress, alcool, RPE, avec les seuils du registre.", run)


def _feedback(tc: ToolCtx) -> Tool:
    def run(days: int):
        fb = tc.data.feedback(days)
        rpes = [f["rpe"] for f in fb if f.get("rpe")]
        return {"count": len(fb), "rpe_avg": round(sum(rpes) / len(rpes), 1) if rpes else None,
                "skipped": sum(1 for f in fb if f.get("status") == "skipped"), "last": fb[-6:]}
    return Tool("recent_feedback", "Retours de séance récents : statut, RPE, notes de l'athlète.", run, DaysIn)


def _drift(tc: ToolCtx) -> Tool:
    def run():
        d = tc.data.hr_drift()
        return {"available": bool(d), "values": d}
    return Tool("hr_drift", "Dérive cardiaque (découplage FC / allure) des dernières sorties longues, si mesurée.", run)


def _nutri_logs(tc: ToolCtx) -> Tool:
    def run():
        from .. import nutrition as nu
        logs = tc.data.nutrition_logs()
        s = nu.summary(logs)
        return {"count": s["count"], "trained_max_g_h": s["trained_max_g_h"], "last": s["last"][:3],
                "brief": nu.prompt_brief(logs)}
    return Tool("nutrition_logs", "Bilans nutrition des sorties longues : g/h, digestion, symptômes, maximum toléré.", run)


def _palette(tc: ToolCtx) -> Tool:
    def run():
        from .. import nutrition as nu
        from ..nutrition_foods import BY_ID, FAMILY_LABELS, FOODS
        s = nu.summary(tc.data.nutrition_logs())
        name = lambda i: BY_ID[i].name if i in BY_ID else i    # noqa: E731
        return {"tolerated": [name(i) for i in s["tolerated"][:6]], "avoid": [name(i) for i in s["avoid"][:6]],
                "palette": {lbl: [f"{f.name} ({f.carbs_g:.0f} g)" for f in FOODS if f.family == fam][:3]
                            for fam, lbl in FAMILY_LABELS.items()}}
    return Tool("food_palette", "Aliments qui passent bien / à éviter chez l'athlète, et palette d'aliments d'effort.", run)


def _target(tc: ToolCtx) -> Tool:
    def run(duration_h: float):
        from .. import nutrition as nu
        from ..nutrition_foods import target_carbs_g_h
        from ..race_nutrition import MAX_G_H
        trained = nu.summary(tc.data.nutrition_logs())["trained_max_g_h"]
        lo, hi = target_carbs_g_h(duration_h, trained)
        return {"range_g_h": [lo, hi], "cap_g_h": MAX_G_H, "trained_max_g_h": trained}
    return Tool("target_carbs", "Fourchette de glucides (g/h) pour une durée d'effort, plafonnée à ce que "
                "l'athlète a toléré (+10 g/h) et à 120 g/h.", run, CarbsIn)


def _race_nutri(tc: ToolCtx) -> Tool:
    def run():
        r = tc.data.race_sim()
        return (r or {}).get("nutrition") or {"available": False}
    return Tool("race_nutrition_plan", "Plan nutrition de l'objectif principal (simulateur) : g/h, eau, sodium.", run)


def _goal(tc: ToolCtx) -> Tool:
    def run():
        g = tc.data.main_goal()
        if g:
            return g
        ctx = tc.athlete
        if ctx.race_date:
            return {"name": "objectif principal", "date": ctx.race_date.isoformat(),
                    "days_left": (ctx.race_date - tc.deps.today()).days}
        return {"available": False}
    return Tool("main_goal", "Objectif principal : nom, date, jours restants, distance, D+.", run)


def _sim(tc: ToolCtx) -> Tool:
    def run():
        return tc.data.race_sim() or {"available": False}
    return Tool("race_simulation", "Simulateur de course de l'objectif : temps estimé (3 scénarios), marges sur les "
                "barrières horaires (scénario prudent), plus grosses montées du profil.", run)


def _phase(tc: ToolCtx) -> Tool:
    def run():
        ctx = tc.athlete
        j = (ctx.race_date - tc.deps.today()).days if ctx.race_date else None
        phase = ("après course" if j is not None and j < 0 else "affûtage" if j is not None and j <= 15
                 else "spécifique" if j is not None and j <= 70 else "foncière" if j is not None else "inconnue")
        return {"days_to_race": j, "phase": phase, "brief": tc.data.phase_brief()}
    return Tool("specific_phase", "Phase de préparation (foncière, spécifique, affûtage) et consignes de la phase.", run)


def _estimates(tc: ToolCtx) -> Tool:
    def run():
        return tc.data.progress_estimates() or {"available": False}
    return Tool("progress_estimates", "Chronos estimés (10 km, semi, marathon) et temps estimé de l'objectif.", run)


# --------------------------------------------------------------------------- boîtes par spécialiste
FACTORIES: dict[str, list[Callable[[ToolCtx], Tool]]] = {
    "charge": [_load_state, _plan_7d, _weekly_loads, _recent, _adherence, _dry_run, _knowledge],
    "nutrition": [_nutri_logs, _palette, _target, _race_nutri, _knowledge],
    "course": [_goal, _sim, _phase, _estimates, _knowledge],
    "prevention": [_wellbeing, _feedback, _drift, _load_state, _knowledge],
}


def toolbox(key: str, tc: ToolCtx) -> ToolBox:
    return ToolBox([f(tc) for f in FACTORIES[key]])


def days_ago(today: date, n: int) -> date:
    return today - timedelta(days=n)


__all__ = ["Tool", "ToolBox", "ToolCtx", "toolbox", "plan_context", "acwr_zone", "FACTORIES"]
