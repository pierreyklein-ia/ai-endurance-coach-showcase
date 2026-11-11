"""v0.50.2 — Annule le repli de sécurité d'avant le correctif (semaine entière passée en footing).

Bug : quand le coach échouait 2 fois aux garde-fous (souvent à cause d'une règle déjà enfreinte par le plan
actuel, donc impossible à corriger), `safe_fallback` réécrivait TOUTES les séances des 7 jours en « Footing très
facile ou repos (repli sécurité) ». Le repli ne garde plus que les séances intenses des 48 h, et seulement avec
un signal de risque (douleur, maladie, fatigue…).

Réparation, une seule fois par base : les séances à venir encore marquées par l'ancien repli sont remises comme
dans la plus récente sauvegarde quotidienne (COACH_HOME/backups) où elles ne l'étaient pas : intensité, durée,
D+, texte, charge et titre. L'agenda suit. Sans sauvegarde exploitable, rien n'est touché.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from ..schemas import PlannedSession

log = logging.getLogger("coach.web")
FALLBACK_TXT = "Footing très facile ou repos (repli sécurité)."
KEY = "repairs_v0502_fallback"


def _from_archive(archive: Path) -> tuple[dict[str, PlannedSession], dict]:
    """{session_id: séance} et session_meta lus dans pilot.db d'une archive de sauvegarde."""
    with tempfile.TemporaryDirectory() as td, tarfile.open(archive, "r:gz") as tar:
        m = tar.getmember("pilot.db")
        if not m.isfile():
            return {}, {}
        tar.extract(m, td, filter="data")
        db = sqlite3.connect(Path(td) / "pilot.db")
        try:
            plan = {sid: PlannedSession.model_validate_json(b) for sid, b in db.execute("SELECT session_id, body FROM plan")}
            r = db.execute("SELECT value FROM kv WHERE key='session_meta'").fetchone()
            return plan, (json.loads(r[0]) if r else {})
        finally:
            db.close()


def originals(backups: Path, wanted: set[str]) -> tuple[dict[str, PlannedSession], dict]:
    """Pour chaque séance voulue : la version de la sauvegarde la plus récente où elle n'est pas en repli."""
    found: dict[str, PlannedSession] = {}
    metas: dict = {}
    for arch in sorted(backups.glob("coach-*.tar.gz"), reverse=True):
        try:
            plan, meta = _from_archive(arch)
        except Exception:                                    # noqa: BLE001 : archive illisible, on passe
            log.warning("sauvegarde illisible : %s", arch.name)
            continue
        for sid in wanted - set(found):
            s = plan.get(sid)
            if s is not None and s.description != FALLBACK_TXT:
                found[sid] = s
                if sid in meta:
                    metas[sid] = meta[sid]
        if set(found) == wanted:
            break
    return found, metas


def repair(service) -> int:
    """Une fois par base. Renvoie le nombre de séances remises."""
    store = service.store
    if store.get(KEY):
        return 0
    n = 0
    try:
        today = service.today()
        hit = [s for s in store.plan(today) if s.description == FALLBACK_TXT]
        home = Path(store.path).parent if store.path != ":memory:" else None
        if hit and home and (home / "backups").is_dir():
            found, metas = originals(home / "backups", {s.session_id for s in hit})
            from ..schemas import PlanChange
            changes = [PlanChange(action="modify", session=found[s.session_id].model_copy(update={"day": s.day}),
                                  reason="v0.50.2 : séance remise après le repli de sécurité")
                       for s in hit if s.session_id in found]
            if changes:
                try:
                    service.pilot.deps.calendar.apply("pilote", changes)
                except Exception:                            # noqa: BLE001 : l'agenda n'empêche pas la réparation
                    log.exception("repli annulé : agenda non mis à jour")
                    store.apply(changes)
                meta = dict(store.get("session_meta", {}) or {})
                for c in changes:
                    sid = c.session.session_id
                    if sid in metas:
                        meta[sid] = metas[sid]
                    elif sid in meta:
                        meta[sid].pop("orig", None)
                store.set("session_meta", meta)
                n = len(changes)
                store.add_chat("coach", "reply", f"🛠️ Correctif : j'avais allégé toute ta semaine par erreur après ta "
                               f"séance de seuil. {n} séance(s) remise(s) comme prévu au départ.", {"deterministic": True})
                log.info("repli de sécurité annulé : %d séance(s) remise(s)", n)
    except Exception:                                        # noqa: BLE001 : jamais bloquant au démarrage
        log.exception("annulation du repli de sécurité")
    store.set(KEY, {"n": n})
    return n


__all__ = ["FALLBACK_TXT", "originals", "repair"]
