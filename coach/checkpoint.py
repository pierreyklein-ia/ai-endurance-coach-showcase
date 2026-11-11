"""Sérialisation des checkpoints : liste blanche explicite des types du domaine.

LangGraph va bloquer la désérialisation msgpack de types non déclarés
(protection contre l'injection d'objets). On déclare nos schémas Pydantic.
Prod : PostgresSaver(conn, serde=SERDE) — thread_id = athlete_id.
"""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

DOMAIN_TYPES = [("coach.schemas", n) for n in (
    "CoachDecision", "GuardrailVerdict", "Signals", "Channel", "InboundMessage", "Route",
    "PlanChange", "PlannedSession", "Pain", "Violation")]

SERDE = JsonPlusSerializer(allowed_msgpack_modules=DOMAIN_TYPES)


def memory_saver() -> InMemorySaver:
    return InMemorySaver(serde=SERDE)


def sqlite_saver(path):
    """Checkpoints persistants (pilote) : une conversation en attente de « oui »
    survit à un redémarrage du bot. Paquet : langgraph-checkpoint-sqlite."""
    import sqlite3
    from pathlib import Path

    from langgraph.checkpoint.sqlite import SqliteSaver
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        Path(path).chmod(0o600)
    except OSError:
        pass
    return SqliteSaver(conn, serde=SERDE)
