"""v0.52 — Réparation unique : préférences modifiées par une correction mal comprise, avant acceptation.

Bug : une phrase comme « on fait pas de double seuil jeudi mais une sortie longue avec allure marathon ? » partait
dans la correction de préférences (alors qu'une question oui/non du coach attendait sa réponse) ; l'extraction
déplaçait le club au jeudi, retirait le double seuil et réduisait le volume, et ces réglages étaient ENREGISTRÉS
avant même que l'athlète accepte le plan proposé (« Garder l'ancien plan » ne les remettait pas).

Réparation (une fois par base, seulement si une proposition de plan attend encore sa réponse) : les réglages
qui contredisent le plan validé (`agreed_plan.commitments`) sont remis, la proposition est retirée.
"""
from __future__ import annotations

import logging
import re

from ..onboarding import JOURS

log = logging.getLogger("coach.web")
KEY = "repairs_v052_prefs"


def restore_from_commitments(prefs: dict, said: list[str]) -> tuple[dict, list[str]]:
    """Réglages remis d'après les engagements du plan validé. Fonction pure : (réglages, ce qui a changé)."""
    out, changed = dict(prefs), []
    q = next((re.search(r"Séance d'intensité (au club )?le (\w+)", c) for c in said
              if c.startswith("Séance d'intensité")), None)
    if q and q.group(2) in JOURS:
        day, club = JOURS.index(q.group(2)), bool(q.group(1))
        if out.get("quality_days") != [day] or bool(out.get("club_quality")) != club:
            out["quality_days"], out["club_quality"] = [day], club
            changed.append(f"séance d'intensité{' au club' if club else ''} le {q.group(2)}")
    dbl = any(c.startswith("Aime le double seuil") for c in said)
    if bool(out.get("double_threshold")) != dbl:
        out["double_threshold"] = dbl
        changed.append("double seuil " + ("gardé" if dbl else "retiré"))
    if not any(re.match(r"Préfère (plus|moins) de volume", c) for c in said) \
            and abs(float(out.get("volume_factor") or 1.0) - 1.0) > 0.04:
        out["volume_factor"] = 1.0
        changed.append("volume normal")
    return out, changed


def _graph_pending(service) -> bool:
    try:
        return bool(service._graph_pending())
    except Exception:                                       # noqa: BLE001
        return False


def repair_v052(service) -> list[str]:
    store = service.store
    if store.get(KEY):
        return []
    changed: list[str] = []
    try:
        agreed = store.get("agreed_plan") or {}
        if store.get("ob_state") == "replan" and store.get("pending_onboarding_plan") and agreed.get("commitments"):
            prefs, changed = restore_from_commitments(store.get("prefs") or {}, agreed["commitments"])
            store.set("ob_state", "done")
            store.set("ob_quick", [])
            store.set("pending_onboarding_plan", None)
            if changed:
                store.set("prefs", prefs)
                service.apply_profile()
            store.add_chat("coach", "reply", "🛠️ Correctif : j'avais mal compris ton dernier message et modifié tes "
                           "réglages avant que tu acceptes. Ton plan validé reste en place"
                           + (" et tes réglages sont remis : " + ", ".join(changed) if changed else "")
                           + ". La proposition de plan en attente est retirée."
                           + (" Ma question de tout à l'heure (mise à jour de ton agenda) attend toujours ta réponse : "
                              "oui ou non ?" if _graph_pending(service) else ""), {"deterministic": True})
            log.info("v0.52 : réglages remis (%s)", ", ".join(changed) or "aucun")
    except Exception:                                       # noqa: BLE001 : jamais bloquant au démarrage
        log.exception("réparation des réglages v0.52")
    store.set(KEY, {"changed": changed})
    return changed


KEY_REFUSED = "repairs_v0521_prefs"
OLD_REFUSAL = "tes corrections sont bien enregistrées"


def repair_v0521(service) -> list[str]:
    """v0.52.1, une fois : l'athlète a gardé l'ancien plan AVANT la v0.52 (l'ancienne réponse gardait les réglages
    mal extraits « pour le prochain bloc »). Si ce refus est postérieur au dernier plan validé, les réglages qui
    contredisent ce plan validé sont remis."""
    store = service.store
    if store.get(KEY_REFUSED):
        return []
    changed: list[str] = []
    try:
        agreed = store.get("agreed_plan") or {}
        at = (agreed.get("adopted_at") or "")[:16]
        refused = [m for m in store.chat(200) if m["role"] == "coach" and OLD_REFUSAL in (m.get("text") or "")
                   and (m.get("at") or "")[:16] >= at]
        if agreed.get("commitments") and refused and store.get("ob_state") in (None, "done"):
            prefs, changed = restore_from_commitments(store.get("prefs") or {}, agreed["commitments"])
            if changed:
                store.set("prefs", prefs)
                service.apply_profile()
                store.add_chat("coach", "reply", "🛠️ Correctif : quand tu as gardé l'ancien plan, j'avais quand même "
                               "gardé des réglages mal compris. Ils sont remis comme dans ton plan validé : "
                               + ", ".join(changed) + ".", {"deterministic": True})
                log.info("v0.52.1 : réglages remis (%s)", ", ".join(changed))
    except Exception:                                       # noqa: BLE001 : jamais bloquant au démarrage
        log.exception("réparation des réglages v0.52.1")
    store.set(KEY_REFUSED, {"changed": changed})
    return changed


__all__ = ["restore_from_commitments", "repair_v052", "repair_v0521"]
