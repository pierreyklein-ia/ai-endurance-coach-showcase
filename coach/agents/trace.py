"""Traçabilité de l'équipe (v0.40) : ce que montre le message du coach (« détails ») et la trace du simulateur.

    meta = {routing: {specialists, reason, by, ms},
            agents: [{name, label, icon, reason, tools: [{name, ms}], risks, risk_level, veto, veto_reason,
                      evidence_ids, cost_eur, ms}],
            critic: {verdict, reasons, revised, rounds, judge},
            cost_by_agent: {superviseur, charge, ..., coach, critique, extraction}}
"""
from __future__ import annotations

from .roster import ORDER, TEAM

AGENT_OF_NODE = {"supervisor": "superviseur", "critic": "critique", "coach": "coach", "extract": "extraction",
                 "eye_report": "coach", "eye_critic": "critique"}


def agent_of(node: str) -> str:
    if node in AGENT_OF_NODE:
        return AGENT_OF_NODE[node]
    k = node.removeprefix("eye_").removeprefix("spec_")
    return k if k in TEAM else node


def cost_by_agent(usages) -> dict[str, float]:
    from ..sandbox import cost_eur
    groups: dict[str, list] = {}
    for u in usages:
        groups.setdefault(agent_of(u.node), []).append(u)
    return {k: round(cost_eur(v), 5) for k, v in groups.items()}


def team_meta(trace: list, usages) -> dict:
    """Extrait de la trace du graphe (nœud, mise à jour) les infos de l'équipe pour le message du coach."""
    routing, notes, critic = None, [], None
    revised = False
    for node, upd in trace:
        if node == "route" and upd.get("routing"):
            routing = upd["routing"]
        elif node == "specialist":
            notes.extend(upd.get("notes") or [])
        elif node == "critic" and upd.get("critic"):
            critic = upd["critic"]
            revised = revised or critic.get("action") == "revise"
    if routing is None:
        return {}
    costs = cost_by_agent(usages)
    agents = [{"name": n["name"], "label": n["label"], "icon": n.get("icon", ""), "reason": n.get("reason", ""),
               "tools": n.get("tools", []), "risks": n.get("risks", [])[:3], "risk_level": n["risk_level"], "veto": n["veto"],
               "veto_reason": n.get("veto_reason"), "evidence_ids": n["evidence_ids"],
               "cost_eur": costs.get(n["name"], 0.0), "ms": n.get("ms", 0), **({"degraded": True} if n.get("degraded") else {})}
              for n in sorted(notes, key=lambda n: ORDER.index(n["name"]))]
    out = {"routing": {k: routing.get(k) for k in ("specialists", "reason", "by", "ms")}, "agents": agents,
           "cost_by_agent": costs}
    if critic:
        out["critic"] = {"verdict": critic["verdict"], "reasons": critic.get("reasons", [])[:4], "revised": revised,
                         "rounds": critic.get("rounds", 1), "judge": critic.get("judge", "llm")}
    return out


def critic_label(c: dict | None) -> str:
    if not c:
        return ""
    if c["verdict"] == "ok":
        return "validé après 1 révision" if c.get("revised") else "validé"
    return "réserves transmises aux garde-fous" if c.get("revised") else "à revoir"


def team_line(meta: dict) -> str:
    """« Équipe consultée : Nutrition (2 outils) · Prévention ✓ veto · Critique : validé après 1 révision »"""
    if not meta.get("routing"):
        return ""
    ag = meta.get("agents") or []
    parts = [f"{a['label']} ({len(a['tools'])} outil{'s' if len(a['tools']) > 1 else ''})"
             + (" ⛔ veto" if a["veto"] else "") for a in ag]
    txt = "Équipe consultée : " + (" · ".join(parts) if parts else "coach seul")
    if meta.get("critic"):
        txt += f" · Critique : {critic_label(meta['critic'])}"
    return txt


__all__ = ["team_meta", "team_line", "cost_by_agent", "agent_of", "critic_label"]
