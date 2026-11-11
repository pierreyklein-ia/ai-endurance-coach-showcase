"""Import du « Carnet VVX » (page de suivi tenue pendant la prépa) dans le plan local.

Le carnet est une base de documents : `sessions/{AAAA-MM-JJ}-{n}` et `weeks/{lundi}`.
On l'exporte en JSON (un fichier par document, ou une liste dans un seul fichier),
puis :

    python -m coach.web import-carnet <dossier_ou_fichier> [--write-calendar]

Chaque séance devient une `PlannedSession` (charge estimée comme dans l'affûtage).
Le titre, le détail et le type d'origine sont gardés à part (`session_meta`) pour
l'affichage dans l'application ; les retours déjà saisis (statut, RPE, note) sont
repris ; le focus, la note du coach et le bilan de chaque semaine aussi (`weeks`).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..schemas import PlannedSession
from .planner import estimate_load
from .store import FEEDBACK_STATUS, PilotStore

# type du carnet -> intensité du moteur (renfo/mobilité : pas de charge de course)
KIND_TO_INTENSITY = {
    "ef": "z2", "sl": "long", "vma": "vo2", "seuil": "threshold", "allure": "tempo",
    "fartlek": "tempo", "cote": "hills", "marche": "z1", "velo": "z1", "test": "threshold",
    "course": "threshold", "renfo": "rest", "mobilite": "rest", "repos": "rest",
}
DPLUS = re.compile(r"(\d[\d\s ]{0,6})\s*m?\s*D\+", re.I)


@dataclass
class CarnetImport:
    sessions: list[PlannedSession] = field(default_factory=list)
    meta: dict[str, dict] = field(default_factory=dict)
    feedback: list[dict] = field(default_factory=list)
    weeks: dict[str, dict] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


def _docs(path: Path) -> list[tuple[str, str, dict]]:
    """(collection, id, data) depuis un dossier d'export ou un fichier JSON."""
    out = []
    files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    for f in files:
        raw = json.loads(f.read_text())
        items = raw if isinstance(raw, list) else [raw]
        for it in items:
            data = it.get("data", it)
            doc_id = it.get("id") or f.stem
            coll = it.get("collection") or f.parent.name
            if coll not in ("sessions", "weeks"):
                coll = "weeks" if "keys" in data and "date" not in data else "sessions"
            out.append((coll, doc_id, data))
    return out


def _dplus(text: str) -> int:
    m = DPLUS.search(text or "")
    return int(re.sub(r"\D", "", m.group(1))) if m else 0


def parse_carnet(path: str | Path, race_word: str = "VVX") -> CarnetImport:
    res = CarnetImport()
    for coll, doc_id, d in _docs(Path(path)):
        if coll == "weeks":
            res.weeks[doc_id] = {k: d.get(k) for k in ("phase", "hours", "focus", "keys", "test",
                                                          "coachNote", "review", "reviewedAt")}
            continue
        try:
            day = date.fromisoformat(d["date"])
            kind = d.get("kind", "ef")
            minutes = int(d.get("minutes") or 0)
        except (KeyError, ValueError) as e:
            res.skipped.append(f"{doc_id} : {e}")
            continue
        title, detail = d.get("title", ""), d.get("detail", "")
        is_race = kind == "course" and race_word.lower() in title.lower()
        intensity = "race" if is_race else KIND_TO_INTENSITY.get(kind, "z2")
        dp = _dplus(f"{title} {detail}")
        sid = f"carnet-{doc_id}"
        res.sessions.append(PlannedSession(
            session_id=sid, day=day, intensity=intensity, duration_min=min(minutes, 4000),
            elevation_gain_m=dp, planned_load=estimate_load(intensity, minutes, dp),
            description=f"{title} — {detail}".strip(" —")[:900], is_race=is_race))
        res.meta[sid] = {"kind": kind, "title": title, "detail": detail, "key": bool(d.get("key")),
                         "order": d.get("order", 1)}
        if d.get("status") in FEEDBACK_STATUS:
            res.feedback.append({"session_id": sid, "day": day, "status": d["status"],
                                 "rpe": d.get("rpe"), "note": d.get("note") or ""})
    res.sessions.sort(key=lambda s: (s.day, res.meta[s.session_id]["order"]))
    return res


def apply_carnet(store: PilotStore, imp: CarnetImport, from_day: date | None = None) -> int:
    """Remplace le plan local à partir de `from_day` (défaut : première séance importée)."""
    sessions = [s for s in imp.sessions if from_day is None or s.day >= from_day]
    if not sessions:
        return 0
    store.replace_plan(sessions, sessions[0].day)
    meta = store.get("session_meta", {})
    meta.update({s.session_id: imp.meta[s.session_id] for s in sessions})
    store.set("session_meta", meta)
    store.set("baseline_loads", {**store.get("baseline_loads", {}),
                                 **{s.day.isoformat(): sum(x.planned_load for x in sessions if x.day == s.day
                                                           and not x.is_race) for s in sessions}})
    weeks = store.get("weeks", {})
    weeks.update(imp.weeks)
    store.set("weeks", weeks)
    known = store.feedback_for([f["session_id"] for f in imp.feedback])
    for f in imp.feedback:
        if f["session_id"] not in known:            # ne jamais écraser un retour saisi dans l'app
            store.set_feedback(f["session_id"], f["day"], f["status"], f["rpe"], f["note"])
    return len(sessions)
