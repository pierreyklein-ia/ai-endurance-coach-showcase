"""Stockage local SQLite du pilote : activités, plan, réglages.

Un seul athlète. Le fichier vit dans ~/.ai_coach/pilot.db (droits 600).
Prod : même schéma dans Postgres, clé athlete_id en plus.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ..schemas import PlanChange, PlannedSession

# Qualité de la source de charge : une meilleure source remplace une moins bonne
# pour la même sortie (ex. FIT envoyé au bot > ligne CSV de l'archive).
SOURCE_RANK = {"estimate": 0, "csv_avg_hr": 1, "hrTSS": 2}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
  id TEXT PRIMARY KEY, start_utc TEXT NOT NULL, local_day TEXT NOT NULL, sport TEXT,
  duration_s REAL, distance_m REAL, dplus_m REAL, avg_hr REAL,
  load REAL NOT NULL, load_source TEXT NOT NULL, origin TEXT, name TEXT);
CREATE INDEX IF NOT EXISTS act_day ON activities(local_day);
CREATE TABLE IF NOT EXISTS plan (
  session_id TEXT PRIMARY KEY, day TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- v0.9 (application web) : retours de séance, fil de discussion, abonnements push
CREATE TABLE IF NOT EXISTS feedback (
  session_id TEXT PRIMARY KEY, day TEXT NOT NULL, status TEXT NOT NULL,
  rpe INTEGER, note TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS fb_day ON feedback(day);
CREATE TABLE IF NOT EXISTS chat (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, role TEXT NOT NULL,
  kind TEXT NOT NULL, text TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, body TEXT NOT NULL, at TEXT NOT NULL);
"""

FEEDBACK_STATUS = ("done", "modified", "skipped")
PROFILES_KEY = "session_profiles"          # v0.43 : {clé d'activité: profil d'intensité}


@dataclass
class Activity:
    start_utc: datetime
    local_day: date
    sport: str
    duration_s: float
    distance_m: float
    dplus_m: float
    avg_hr: float | None
    load: float
    load_source: str
    origin: str = ""
    name: str = ""
    moving_s: float | None = None      # temps en mouvement (v0.10) : vitesses des séances, hors pauses
    suspect: bool = False              # v0.48 : durée invraisemblable (montre oubliée) : hors records et statistiques
    elapsed_s: float | None = None     # v0.48 : temps écoulé brut, quand la durée retenue en diffère

    @property
    def key(self) -> str:
        # Même sortie importée deux fois (archive puis FIT) : même minute de départ.
        return self.start_utc.strftime("%Y%m%dT%H%M")


# --------------------------------------------------------------------------- #
# v0.48 : durées invraisemblables (montre laissée en marche après la sortie)
# --------------------------------------------------------------------------- #
RUN_LABELS = ("Course", "Trail")
SUSPECT_PACE_S_PER_KM = 20 * 60        # plus lent que 20 min/km sur plus de 3 km, sans temps en mouvement
SUSPECT_LONG_S, SUSPECT_LONG_KM = 8 * 3600, 30   # plus de 8 h pour moins de 30 km
PLAUSIBLE_S_PER_KM = 7 * 60            # durée retenue pour une sortie douteuse (charge, volumes)


def effective_duration(elapsed_s: float, moving_s: float | None) -> float:
    """Temps écoulé, sauf s'il dépasse largement le temps en mouvement (plus du double, ou plus de 3 h de plus) :
    alors le temps en mouvement (la montre a tourné pendant une pause, ou après la sortie)."""
    if moving_s and moving_s > 0 and elapsed_s and (elapsed_s > 2 * moving_s or elapsed_s > moving_s + 3 * 3600):
        return float(moving_s)
    return float(elapsed_s or 0.0)


def is_suspect_run(sport: str, duration_s: float, distance_m: float, moving_known: bool) -> bool:
    """Course à pied à l'allure invraisemblable : plus lente que 20 min/km sur plus de 3 km (sans temps en
    mouvement), ou plus de 8 h pour moins de 30 km."""
    if sport not in RUN_LABELS or distance_m <= 0:
        return False
    km = distance_m / 1000
    if duration_s > SUSPECT_LONG_S and km < SUSPECT_LONG_KM:
        return True
    return not moving_known and km > 3 and duration_s / km > SUSPECT_PACE_S_PER_KM


def sanitize(a: Activity) -> Activity:
    """Durée et charge retenues (idempotent) : temps en mouvement si le temps écoulé est aberrant ; une sortie
    douteuse est marquée `suspect` et sa durée ramenée à une allure plausible (7 min/km), la charge suivant la
    durée. Les données brutes restent en base (règle appliquée à la lecture : les anciennes sorties aussi)."""
    raw = a.duration_s or 0.0
    dur = effective_duration(raw, a.moving_s)
    if is_suspect_run(a.sport, dur, a.distance_m or 0.0, bool(a.moving_s)):
        a.suspect = True
        dur = min(dur, a.distance_m / 1000 * PLAUSIBLE_S_PER_KM)
    if raw and dur < raw - 1:
        a.elapsed_s = a.elapsed_s or raw
        a.load = round(a.load * dur / raw, 1)
        a.duration_s = dur
    return a


class _Rows:
    """Résultat d'une requête, déjà lu (les lignes ne sont plus lues hors du verrou)."""

    def __init__(self, cur):
        self._rows = cur.fetchall() if cur.description is not None else []
        self.rowcount, self.lastrowid = cur.rowcount, cur.lastrowid

    def __iter__(self):
        return iter(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _SafeConn:
    """v0.40 : connexion SQLite partagée entre fils (requêtes web, tours du coach en tâche de fond, outils des
    spécialistes en parallèle) : chaque requête est exécutée ET lue sous un verrou (plus d'« API misuse »)."""

    def __init__(self, conn: sqlite3.Connection):
        self._c, self._lock = conn, threading.RLock()

    def execute(self, sql: str, args=()):
        with self._lock:
            return _Rows(self._c.execute(sql, args))

    def executescript(self, sql: str):
        with self._lock:
            return self._c.executescript(sql)

    def commit(self):
        with self._lock:
            self._c.commit()

    def __getattr__(self, k):                 # backup, close, … : rares, appelés sous le verrou du service
        return getattr(self._c, k)


class PilotStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.db = _SafeConn(sqlite3.connect(self.path, check_same_thread=False))
        self.db.executescript(_SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(activities)")}
        if "moving_s" not in cols:                      # migration v0.10
            self.db.execute("ALTER TABLE activities ADD COLUMN moving_s REAL")
        if self.path != ":memory:":
            try:
                Path(self.path).chmod(0o600)
            except OSError:
                pass

    # ---------------------------------------------------------------- activités
    def add_activity(self, a: Activity) -> str:
        """Retourne 'new', 'upgraded' (meilleure source) ou 'duplicate'."""
        row = self.db.execute("SELECT load_source FROM activities WHERE id=?", (a.key,)).fetchone()
        if row and SOURCE_RANK.get(row[0], 0) >= SOURCE_RANK.get(a.load_source, 0):
            return "duplicate"
        self.db.execute(
            "INSERT OR REPLACE INTO activities (id, start_utc, local_day, sport, duration_s, distance_m, dplus_m, "
            "avg_hr, load, load_source, origin, name, moving_s) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (a.key, a.start_utc.isoformat(), a.local_day.isoformat(), a.sport, a.duration_s,
             a.distance_m, a.dplus_m, a.avg_hr, a.load, a.load_source, a.origin, a.name, a.moving_s))
        self.db.commit()
        prof = getattr(a, "profile", None)                       # v0.43 : profil d'intensité (session_type)
        if prof:
            items = self.get(PROFILES_KEY) or {}
            items[a.key] = prof
            self.set(PROFILES_KEY, dict(list(items.items())[-300:]))
        return "upgraded" if row else "new"

    def relabel_sports(self, label) -> int:
        """v0.25 : recalcule le libellé des sports déjà importés (« Weighttraining » → « Renfo »)."""
        n = 0
        for (sport,) in self.db.execute("SELECT DISTINCT sport FROM activities").fetchall():
            new = label(sport)
            if new != sport:
                n += self.db.execute("UPDATE activities SET sport=? WHERE sport=?", (new, sport)).rowcount
        self.db.commit()
        return n

    def activities(self, since: date | None = None) -> list[Activity]:
        q = "SELECT start_utc, local_day, sport, duration_s, distance_m, dplus_m, avg_hr, load, " \
            "load_source, origin, name, moving_s FROM activities"
        args: tuple = ()
        if since:
            q, args = q + " WHERE local_day >= ?", (since.isoformat(),)
        out = []
        for r in self.db.execute(q + " ORDER BY start_utc", args):
            out.append(sanitize(Activity(datetime.fromisoformat(r[0]), date.fromisoformat(r[1]), r[2], r[3] or 0.0,
                                         r[4] or 0.0, r[5], r[6], r[7], r[8], r[9] or "", r[10] or "", r[11])))
        return out

    def daily_loads(self, start: date, end: date) -> list[float]:
        """Charge par jour, de `start` à `end` inclus (0 les jours sans sortie). v0.48 : charge retenue après
        `sanitize` (une montre oubliée 24 h ne pèse plus comme un ultra)."""
        per: dict[str, float] = {}
        for r in self.db.execute(
                "SELECT local_day, sport, duration_s, distance_m, load, moving_s FROM activities "
                "WHERE local_day BETWEEN ? AND ?", (start.isoformat(), end.isoformat())):
            a = sanitize(Activity(datetime.min, date.fromisoformat(r[0]), r[1], r[2] or 0.0, r[3] or 0.0, 0.0, None,
                                  r[4] or 0.0, "", moving_s=r[5]))
            per[r[0]] = per.get(r[0], 0.0) + a.load
        n = (end - start).days + 1
        return [float(per.get((start + timedelta(days=i)).isoformat(), 0.0)) for i in range(n)]

    def fingerprint(self) -> tuple:
        """Empreinte bon marché des données qui changent un calcul dérivé (v0.31 : cache de la carrière)."""
        q = lambda sql: tuple(self.db.execute(sql).fetchone())      # noqa: E731
        return (q("SELECT COUNT(*), MAX(start_utc), TOTAL(load), TOTAL(duration_s) FROM activities"),
                q("SELECT COUNT(*), MAX(at) FROM feedback"),
                q("SELECT COUNT(*), MAX(day), TOTAL(LENGTH(body)) FROM plan"))

    def first_activity_day(self) -> date | None:
        r = self.db.execute("SELECT MIN(local_day) FROM activities").fetchone()
        return date.fromisoformat(r[0]) if r and r[0] else None

    def has_activity_on(self, day: date) -> bool:
        return self.db.execute("SELECT 1 FROM activities WHERE local_day=? LIMIT 1",
                               (day.isoformat(),)).fetchone() is not None

    # ---------------------------------------------------------------- plan
    def plan(self, since: date | None = None, until: date | None = None) -> list[PlannedSession]:
        q, args = "SELECT body FROM plan WHERE 1=1", []
        if since:
            q += " AND day >= ?"; args.append(since.isoformat())
        if until:
            q += " AND day <= ?"; args.append(until.isoformat())
        return [PlannedSession.model_validate_json(b) for (b,) in self.db.execute(q + " ORDER BY day", args)]

    def upsert_session(self, s: PlannedSession) -> None:
        from ..session_content import harmonize
        s = harmonize(s)                        # v0.50 : jamais de texte en désaccord avec la durée
        self.db.execute("INSERT OR REPLACE INTO plan VALUES (?,?,?)",
                        (s.session_id, s.day.isoformat(), s.model_dump_json()))

    def session(self, session_id: str) -> PlannedSession | None:
        r = self.db.execute("SELECT body FROM plan WHERE session_id=?", (session_id,)).fetchone()
        return PlannedSession.model_validate_json(r[0]) if r else None

    def apply(self, changes: list[PlanChange]) -> None:
        from ..session_meta import sync
        meta, touched = None, False
        for c in changes:
            if c.action == "cancel":
                self.db.execute("DELETE FROM plan WHERE session_id=?", (c.session.session_id,))
            elif c.action != "keep":
                old = self.session(c.session.session_id)
                if old is not None and old.intensity != c.session.intensity:
                    meta = meta if meta is not None else dict(self.get("session_meta", {}) or {})
                    touched = sync(meta, old, c.session) or touched   # v0.50.1 : le titre suit l'intensité
                self.upsert_session(c.session)
        if touched:
            self.set("session_meta", meta)
        self.db.commit()

    def replace_plan(self, sessions: list[PlannedSession], from_day: date) -> None:
        self.db.execute("DELETE FROM plan WHERE day >= ?", (from_day.isoformat(),))
        for s in sessions:
            self.upsert_session(s)
        self.db.commit()

    # ---------------------------------------------------------------- retours de séance (v0.9)
    def set_feedback(self, session_id: str, day: date, status: str, rpe: int | None, note: str) -> dict:
        if status not in FEEDBACK_STATUS:
            raise ValueError(f"statut inconnu : {status}")
        if rpe is not None and not 1 <= int(rpe) <= 10:
            raise ValueError("RPE entre 1 et 10")
        at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.db.execute("INSERT OR REPLACE INTO feedback VALUES (?,?,?,?,?,?)",
                        (session_id, day.isoformat(), status, rpe, note.strip()[:2000], at))
        self.db.commit()
        return self.feedback_for([session_id])[session_id]

    def rename_activities(self, names: dict[str, str]) -> int:
        """v0.45 : {clé d'activité: nom} -> met à jour les noms changés sur Strava après la synchro."""
        n = 0
        for key, name in names.items():
            cur = self.db.execute("UPDATE activities SET name=? WHERE id=? AND COALESCE(name,'')<>?", (name, key, name))
            n += cur.rowcount or 0
        if n:
            self.db.commit()
        return n

    def delete_feedback(self, session_id: str) -> None:
        """v0.44 : séance déliée de sa sortie : elle redevient « à valider »."""
        self.db.execute("DELETE FROM feedback WHERE session_id=?", (session_id,))
        self.db.commit()

    def feedback_for(self, session_ids: list[str]) -> dict[str, dict]:
        if not session_ids:
            return {}
        q = "SELECT session_id, day, status, rpe, note, at FROM feedback WHERE session_id IN ({})".format(",".join("?" * len(session_ids)))
        return {r[0]: {"session_id": r[0], "day": r[1], "status": r[2], "rpe": r[3], "note": r[4], "at": r[5]}
                for r in self.db.execute(q, session_ids)}

    def all_feedback(self) -> list[dict]:
        """Tous les retours de séance (v0.24 : carrière)."""
        return [{"session_id": r[0], "day": r[1], "status": r[2], "rpe": r[3], "note": r[4], "at": r[5]}
                for r in self.db.execute("SELECT session_id, day, status, rpe, note, at FROM feedback ORDER BY day")]

    # ---------------------------------------------------------------- fil de discussion (v0.9)
    def add_chat(self, role: str, kind: str, text: str, meta: dict | None = None) -> dict:
        at = datetime.now().astimezone().isoformat(timespec="seconds")
        cur = self.db.execute("INSERT INTO chat (at, role, kind, text, meta) VALUES (?,?,?,?,?)",
                              (at, role, kind, text, json.dumps(meta or {}, default=str)))
        self.db.commit()
        return {"id": cur.lastrowid, "at": at, "role": role, "kind": kind, "text": text, "meta": meta or {}}

    def chat(self, limit: int = 50, before: int | None = None) -> list[dict]:
        q, args = "SELECT id, at, role, kind, text, meta FROM chat", []
        if before:
            q, args = q + " WHERE id < ?", [before]
        rows = self.db.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [{"id": r[0], "at": r[1], "role": r[2], "kind": r[3], "text": r[4], "meta": json.loads(r[5])}
                for r in reversed(rows)]

    # ---------------------------------------------------------------- abonnements push (v0.9)
    def add_push_sub(self, sub: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO push_subs VALUES (?,?,?)",
                        (sub["endpoint"], json.dumps(sub), datetime.now().astimezone().isoformat()))
        self.db.commit()

    def push_subs(self) -> list[dict]:
        return [json.loads(b) for (b,) in self.db.execute("SELECT body FROM push_subs")]

    def remove_push_sub(self, endpoint: str) -> None:
        self.db.execute("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))
        self.db.commit()

    # ---------------------------------------------------------------- réglages
    def get(self, key: str, default=None):
        r = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value, default=str)))
        self.db.commit()


def activity_dict(a: Activity) -> dict:
    d = asdict(a)
    d["start_utc"], d["local_day"] = a.start_utc.isoformat(), a.local_day.isoformat()
    return d
