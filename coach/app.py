"""Point d'entrée canal-agnostique (terminal, Telegram, WhatsApp...).

Piège classique en headless : quand le graphe attend une confirmation
(interrupt), la réponse "oui" de l'athlète serait classée NOISE par le triage.
Il faut donc tester l'existence d'un interrupt en attente AVANT le triage et
reprendre le thread avec Command(resume=...).
"""
from __future__ import annotations

from langgraph.types import Command

from .schemas import InboundMessage

_RESET = {"decision": None, "verdict": None, "signals": None, "evidence": [],
          "final_changes": [], "calendar_ops": [], "reply": "", "attempts": 0,
          "emergency": False, "distress": False,
          # v0.40 : équipe d'agents (`notes: None` = remise à zéro par le réducteur du fan-in)
          "routing": None, "notes": None, "team_cards": [], "critic": None}


def handle_inbound(graph, msg: InboundMessage, trace: list | None = None) -> dict:
    """Retourne {"reply": str, "calendar_ops": [...]} ou {"ask": str}.

    `trace` (optionnel) reçoit la liste (nœud, mise à jour) dans l'ordre
    d'exécution : c'est ce qu'affichent le simulateur et le bot en mode debug.
    """
    config = {"configurable": {"thread_id": msg.athlete_id}}
    snapshot = graph.get_state(config)
    # NB : après un 2e interrupt dans le même nœud, `snapshot.next` peut être vide :
    # on se fie aux interrupts en attente, pas à `next`.
    pending = any(t.interrupts for t in snapshot.tasks)
    payload = Command(resume=msg.text) if pending else {**_RESET, "inbound": msg}

    asked = None
    for chunk in graph.stream(payload, config, stream_mode="updates"):
        for node, update in chunk.items():
            if node == "__interrupt__":
                asked = update[0].value["message"]
            elif trace is not None:
                trace.append((node, update or {}))

    if asked is not None:
        return {"ask": asked}
    values = graph.get_state(config).values
    return {"reply": values.get("reply", ""), "calendar_ops": values.get("calendar_ops", [])}
