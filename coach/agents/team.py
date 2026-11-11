"""Nœuds LangGraph de l'équipe (v0.40) : superviseur → spécialistes en parallèle (Send) → coach → critique.

    evidence -> route (superviseur, small) ─┬─ Send(specialist, charge)     ─┐
                                            ├─ Send(specialist, nutrition)  ─┼─> team (fan-in) -> coach (main)
                                            └─ (aucun : le coach seul)     ──┘        │
    coach -> critic (small, LLM-as-judge + contrôles déterministes) ─┬─ revise (1 fois) -> coach
                                                                     └─ ok -> guard (garde-fous, AUTORITÉ FINALE)

Un spécialiste = un agent outillé (boucle tool use, ≤ 4 allers-retours) avec son prompt, ses outils et une note
structurée `SpecialistNote`. Chaque agent est journalisé sous son propre nom de nœud (coût par agent).
"""
from __future__ import annotations

import json
import logging
import os
import time

from langgraph.types import Send

from ..schemas import Signals
from .offline import critic_checks, health_signals, offline_agent, route_rules, shortcut
from .roster import COACH_TEAM_ADDENDUM, CRITIC_SYSTEM, MAX_ROUTED, ORDER, SUPERVISOR_SYSTEM, TEAM
from .schemas import CriticVerdict, RoutingDecision, SpecialistNote
from .tools import ToolCtx, toolbox

log = logging.getLogger("coach.agents")
MODES = ("multi", "single")


def agents_mode(deps) -> str:
    """`COACH_AGENTS=multi|single` (défaut multi) ; `Deps.agents` l'emporte (tests, évaluation A/B)."""
    m = (getattr(deps, "agents", None) or os.getenv("COACH_AGENTS", "multi")).strip().lower()
    return m if m in MODES else "multi"


def add_notes(old: list | None, new: list | None) -> list:
    """Réducteur du fan-in : les notes des spécialistes parallèles s'ajoutent ; `None` remet à zéro (nouveau message)."""
    if new is None:
        return []
    return (old or []) + list(new)


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def known_card_ids(deps) -> set[str]:
    corpus = getattr(deps.retriever, "corpus", None) or []
    return {e.id for e in corpus}


def card_texts(deps, ids: list[str]) -> list[dict]:
    corpus = {e.id: e for e in getattr(deps.retriever, "corpus", None) or []}
    return [{"id": i, "source": corpus[i].source[:90], "text": corpus[i].text[:420]} for i in ids if i in corpus]


def roster_view(deps) -> dict:
    """Réglages › Application : mode d'agents et composition de l'équipe (rôles, tiers, outils en lecture seule)."""
    from ..llm import MODELS
    mode = agents_mode(deps)
    box = {k: [t.name for t in toolbox(k, ToolCtx(None, "", Signals())).tools] for k in ORDER}
    team = [{"key": "supervisor", "label": "Superviseur", "icon": "🧭", "tier": "small", "model": MODELS["small"],
             "role": "choisit 0 à 3 spécialistes (règles + petit modèle)", "tools": []}]
    team += [{"key": k, "label": TEAM[k].label, "icon": TEAM[k].icon, "tier": "small", "model": MODELS["small"],
              "role": TEAM[k].role.split(". ")[0].removeprefix("Tu es le ") + ".", "tools": box[k]} for k in ORDER]
    team += [{"key": "coach", "label": "Coach", "icon": "🏃", "tier": "main", "model": MODELS["main"],
              "role": "synthèse : seul à parler à l'athlète", "tools": []},
             {"key": "critic", "label": "Critique", "icon": "🧐", "tier": "small", "model": MODELS["small"],
              "role": "relit le brouillon (fondé, cohérent, sûr) : 1 révision au plus", "tools": []}]
    return {"mode": mode, "team": team if mode == "multi" else [t for t in team if t["key"] == "coach"],
            "all": team, "note": ("Équipe d'agents : superviseur, spécialistes outillés en parallèle, coach, critique ; "
                                  "les garde-fous déterministes décident toujours en dernier."
                                  if mode == "multi" else "Mode mono-agent (comparaison) : un seul coach.")}


# --------------------------------------------------------------------------- spécialiste (réutilisé par l'Œil)
def run_specialist(deps, key: str, athlete_id: str, message: str, signals: Signals, *, mode: str = "chat",
                   reason: str = "") -> dict:
    sp = TEAM[key]
    today = deps.today()
    ctx = deps.repo.get(athlete_id)
    box = toolbox(key, ToolCtx(deps, athlete_id, signals, message, ctx))
    payload = {"mode": mode, "today": str(today), "message": message, "consigne": reason,
               "days_to_race": (ctx.race_date - today).days if ctx.race_date else None,
               "signals": signals.model_dump(mode="json", exclude_none=True)}
    if mode == "eye":
        payload["consigne"] = ("Analyse approfondie des 6 à 8 dernières semaines : lis toutes tes données "
                               "(fenêtres de 8 semaines) et produis une section complète.")
    node = ("eye_" if mode == "eye" else "") + sp.node
    user = json.dumps(payload, ensure_ascii=False, default=str)
    t0 = time.perf_counter()
    degraded = None
    fn = getattr(deps.llm, "tool_loop", None)
    try:
        res = (fn(node=node, tier="small", system=sp.system, user=user, tools=box, schema=SpecialistNote)
               if fn else offline_agent(node, user, box, SpecialistNote))
    except Exception as e:                                       # noqa: BLE001 : un spécialiste ne casse pas le tour
        log.warning("spécialiste %s en échec (%s) : version hors ligne", key, e)
        degraded = type(e).__name__
        res = offline_agent(node, user, box, SpecialistNote)
    note: SpecialistNote = res.output                            # type: ignore[assignment]
    degraded = degraded or box.degraded
    known = known_card_ids(deps)
    cited = [c for c in dict.fromkeys(note.evidence_ids) if c in known]
    return {"name": key, "label": sp.label, "icon": sp.icon, "node": node, "reason": reason,
            "tools": list(box.calls), "findings": note.findings, "recommendations": note.recommendations,
            "risks": note.risks,
            "risk_level": note.risk_level, "veto": bool(note.veto and key == "prevention"),
            "veto_reason": note.veto_reason if key == "prevention" else None, "evidence_ids": cited,
            "invalid_cards": [c for c in note.evidence_ids if c not in known], "confidence": note.confidence,
            "ms": _ms(t0), "steps": getattr(res, "steps", 1), **({"degraded": degraded} if degraded else {})}


def compact_note(n: dict) -> dict:
    """Ce que reçoivent le coach et le critique (pas la trace technique)."""
    return {k: n.get(k) for k in ("name", "label", "findings", "recommendations", "risks", "risk_level", "veto",
                                  "veto_reason", "evidence_ids", "confidence")}


def team_payload(s: dict) -> dict:
    notes = sorted(s.get("notes") or [], key=lambda n: ORDER.index(n["name"]))
    out = {"team": [compact_note(n) for n in notes], "team_cards": s.get("team_cards") or [],
           "routing": (s.get("routing") or {}).get("reason")}
    crit = s.get("critic") or {}
    if crit.get("revised") and crit.get("feedback"):
        out["critic_feedback"] = crit["feedback"]
    return out


# --------------------------------------------------------------------------- nœuds
def make_team_nodes(deps, max_attempts: int):
    def n_route(s):
        msg, sig = s["inbound"].text, s["signals"]
        ctx = deps.repo.get(s["inbound"].athlete_id)
        j = (ctx.race_date - deps.today()).days if ctx.race_date else None
        t0 = time.perf_counter()
        rules = route_rules(msg, sig, j)
        sc = shortcut(msg, sig)
        if sc is not None:
            dec, by = sc, "règle"
        elif not rules.specialists and "?" not in msg:
            dec, by = rules, "règle"
        else:
            try:
                dec = deps.llm.structured(node="supervisor", tier="small", system=SUPERVISOR_SYSTEM,
                                          user=json.dumps({"message": msg, "days_to_race": j,
                                                           "signals": sig.model_dump(mode="json", exclude_none=True),
                                                           "candidats_regles": rules.specialists},
                                                          ensure_ascii=False, default=str),
                                          schema=RoutingDecision)
                by = "superviseur"
            except Exception as e:                               # noqa: BLE001
                log.warning("superviseur en échec (%s) : routage par règles", e)
                dec, by = rules, "règle (repli)"
        picked = [k for k in dict.fromkeys(dec.specialists) if k in TEAM]
        reason = dec.reason
        if health_signals(sig) and "prevention" not in picked:      # filet de sécurité : santé => Prévention
            picked.insert(0, "prevention")
            reason += " (+ Prévention : signal de santé)"
        picked = sorted(picked, key=ORDER.index)[:MAX_ROUTED]
        return {"routing": {"specialists": picked, "reason": reason, "by": by, "ms": _ms(t0)}, "notes": None,
                "critic": None}

    def fan_out(s):
        specs = (s.get("routing") or {}).get("specialists") or []
        if not specs:
            return "team"
        return [Send("specialist", {"inbound": s["inbound"], "signals": s["signals"], "spec": k,
                                    "spec_reason": s["routing"]["reason"]}) for k in specs]

    def n_specialist(s):
        k = s["spec"]
        why = next((p.split(" : ", 1)[1] for p in (s.get("spec_reason") or "").split(" ; ")
                    if p.startswith(TEAM[k].label + " : ")), s.get("spec_reason") or "")
        return {"notes": [run_specialist(deps, k, s["inbound"].athlete_id, s["inbound"].text, s["signals"],
                                         reason=why)]}

    def n_team(s):
        ids = list(dict.fromkeys(c for n in sorted(s.get("notes") or [], key=lambda n: ORDER.index(n["name"]))
                                 for c in n["evidence_ids"]))
        return {"team_cards": card_texts(deps, ids[:8])}

    def n_critic(s):
        d = s["decision"]
        notes = [compact_note(n) for n in s.get("notes") or []]
        prev = s.get("critic") or {}
        ctx = deps.repo.get(s["inbound"].athlete_id)
        known = known_card_ids(deps) or {e["id"] for e in s.get("evidence") or []}
        plan_ids = {p.session_id for p in ctx.plan_next_7d}
        t0 = time.perf_counter()
        det = critic_checks(d, notes, known, s["signals"], plan_ids)
        payload = {"athlete_message": s["inbound"].text, "draft": d.model_dump(mode="json"), "team": notes,
                   "team_cards": s.get("team_cards") or [], "signals": s["signals"].model_dump(mode="json", exclude_none=True),
                   "known_cards": sorted(known), "plan_ids": sorted(plan_ids),
                   "persona": deps.persona()[:800]}
        llm_reasons, judge, remarks = [], "llm", []
        if not notes and not d.changes:                          # coût : rien à confronter, contrôles du code seuls
            judge = "règles"
        else:
            try:
                v = deps.llm.structured(node="critic", tier="small", system=CRITIC_SYSTEM,
                                        user=json.dumps(payload, ensure_ascii=False, default=str), schema=CriticVerdict)
                llm_reasons = v.reasons if v.verdict == "revise" else []
                remarks = [] if v.verdict == "revise" else v.reasons
            except Exception as e:                               # noqa: BLE001 : le contrôle déterministe reste
                log.warning("critique LLM en échec (%s) : contrôles déterministes seuls", e)
                judge = "règles (repli)"
        reasons = list(dict.fromkeys(det + llm_reasons))
        verdict = "revise" if reasons else "ok"
        can = verdict == "revise" and not prev.get("revised") and s.get("attempts", 0) < max_attempts
        return {"critic": {"verdict": verdict, "reasons": reasons or remarks[:3], "deterministic": det,
                           "revised": bool(prev.get("revised") or can), "rounds": prev.get("rounds", 0) + 1,
                           "action": "revise" if can else "guard", "judge": judge, "ms": _ms(t0),
                           "feedback": reasons if can else prev.get("feedback")}}

    def after_coach(s) -> str:
        return "critic" if s.get("verdict") is None else "guard"

    def after_critic(s) -> str:
        return "coach" if (s.get("critic") or {}).get("action") == "revise" else "guard"

    return {"route": n_route, "specialist": n_specialist, "team": n_team, "critic": n_critic,
            "fan_out": fan_out, "after_coach": after_coach, "after_critic": after_critic}


__all__ = ["agents_mode", "roster_view", "add_notes", "run_specialist", "make_team_nodes", "team_payload", "compact_note",
           "COACH_TEAM_ADDENDUM", "known_card_ids", "card_texts"]
