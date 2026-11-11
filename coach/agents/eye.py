"""« Œil du coach » (v0.40) : l'analyse approfondie, usage phare de TOUTE l'équipe d'agents.

    plan (superviseur : les 4 spécialistes) ─ Send ×4 (fenêtres de 8 semaines) ─> report (coach, tier main)
        -> critic (small : 3 priorités citées, pas > 120 g/h, pas de rattrapage, veto respecté) ─┬─ revise (1 fois)
                                                                                                 └─ END
Débloqué au niveau de carrière `EYE_LEVEL`, une fois tous les `EYE_COOLDOWN_DAYS` jours (coach/web/eye_flow.py).
"""
from __future__ import annotations

import json
import os
import time
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from ..schemas import Signals
from .offline import eye_checks
from .roster import EYE_REPORT_SYSTEM, ORDER, TEAM
from .schemas import CriticVerdict, EyeReport
from .team import add_notes, card_texts, compact_note, known_card_ids, run_specialist


def _env_int(name: str, default: int, lo: int = 1) -> int:
    try:
        return max(lo, int(os.getenv(name, default)))
    except ValueError:
        return default


EYE_LEVEL = _env_int("COACH_EYE_LEVEL", 7)                  # niveau de carrière qui débloque l'Œil du coach
EYE_COOLDOWN_DAYS = _env_int("COACH_EYE_EVERY_DAYS", 30)    # une analyse tous les 30 jours au plus
EYE_KEY = "eye_report"
EYE_MESSAGE = "Analyse de mes 8 dernières semaines : charge, assiduité, ressentis, nutrition, objectif et course."


class EyeState(TypedDict, total=False):
    athlete_id: str
    routing: dict
    notes: Annotated[list, add_notes]
    spec: str
    report: dict | None
    critic: dict | None
    rounds: int


def build_eye_graph(deps):
    def n_plan(s):
        return {"routing": {"specialists": list(ORDER), "by": "règle",
                            "reason": "Œil du coach : toute l'équipe analyse les 8 dernières semaines"}, "notes": None}

    def fan_out(s):
        return [Send("specialist", {"athlete_id": s["athlete_id"], "spec": k}) for k in s["routing"]["specialists"]]

    def n_specialist(s):
        k = s["spec"]
        return {"notes": [run_specialist(deps, k, s["athlete_id"], EYE_MESSAGE, Signals(), mode="eye",
                                         reason="section de l'Œil du coach")]}

    def n_report(s):
        notes = sorted(s.get("notes") or [], key=lambda n: ORDER.index(n["name"]))
        ids = list(dict.fromkeys(c for n in notes for c in n["evidence_ids"]))
        payload = {"today": str(deps.today()), "team": [compact_note(n) for n in notes],
                   "team_cards": card_texts(deps, ids[:10])}
        crit = s.get("critic") or {}
        if crit.get("action") == "revise":
            payload["critic_feedback"] = crit["reasons"]
        rep = deps.llm.structured(node="eye_report", tier="main", system=EYE_REPORT_SYSTEM + deps.persona(),
                                  user=json.dumps(payload, ensure_ascii=False, default=str), schema=EyeReport)
        known = known_card_ids(deps)
        prios = [p.model_copy(update={"cards": [c for c in p.cards if c in known]}) for p in rep.priorities[:3]]
        return {"report": rep.model_copy(update={"priorities": prios}).model_dump(), "rounds": s.get("rounds", 0) + 1}

    def n_critic(s):
        rep = EyeReport.model_validate(s["report"])
        notes = [compact_note(n) for n in s.get("notes") or []]
        known = {c for n in notes for c in n["evidence_ids"]} or known_card_ids(deps)
        det = eye_checks(rep, notes, known)
        try:
            v = deps.llm.structured(node="eye_critic", tier="small", system=(
                "Tu es le critique de l'équipe. Vérifie le rapport « Œil du coach » : fondé sur les sections des "
                "spécialistes, 3 priorités concrètes et justifiées par des fiches fournies, aucun conseil dangereux "
                "(rattrapage, > 120 g/h, intensité malgré un veto). verdict ok | revise + raisons courtes."),
                user=json.dumps({"draft": rep.model_dump(), "team": notes, "known_cards": sorted(known)},
                                ensure_ascii=False, default=str), schema=CriticVerdict)
            llm = v.reasons if v.verdict == "revise" else []
        except Exception:                                        # noqa: BLE001
            llm = []
        reasons = list(dict.fromkeys(det + llm))
        verdict = "revise" if reasons else "ok"
        prev = s.get("critic") or {}
        can = verdict == "revise" and not prev.get("revised")
        return {"critic": {"verdict": verdict, "reasons": reasons, "revised": bool(prev.get("revised") or can),
                           "action": "revise" if can else "done", "rounds": prev.get("rounds", 0) + 1}}

    g = StateGraph(EyeState)
    g.add_node("plan", n_plan)
    g.add_node("specialist", n_specialist)
    g.add_node("report", n_report)
    g.add_node("critic", n_critic)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", fan_out, ["specialist"])
    g.add_edge("specialist", "report")
    g.add_edge("report", "critic")
    g.add_conditional_edges("critic", lambda s: "report" if s["critic"]["action"] == "revise" else END,
                            ["report", END])
    return g.compile()


def run_eye(deps, athlete_id: str) -> dict:
    """Exécute l'Œil du coach. Retourne {report, notes, critic, routing, trace, ms}."""
    graph = build_eye_graph(deps)
    trace: list = []
    t0 = time.perf_counter()
    for chunk in graph.stream({"athlete_id": athlete_id, "notes": None}, stream_mode="updates"):
        for node, upd in chunk.items():
            trace.append((node, upd or {}))
    notes = sorted((n for node, u in trace if node == "specialist" for n in u.get("notes") or []),
                   key=lambda n: ORDER.index(n["name"]))
    report = next(u["report"] for node, u in reversed(trace) if node == "report")
    critic = next((u["critic"] for node, u in reversed(trace) if node == "critic"), None)
    return {"report": report, "notes": notes, "critic": critic,
            "routing": next(u["routing"] for node, u in trace if node == "plan"), "trace": trace,
            "ms": round((time.perf_counter() - t0) * 1000, 1), "labels": {k: TEAM[k].label for k in ORDER}}


__all__ = ["EYE_LEVEL", "EYE_COOLDOWN_DAYS", "EYE_KEY", "build_eye_graph", "run_eye"]
