"""Réglages du mode pilote (variables d'environnement, avec valeurs par défaut)."""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DIAG_START_LOCAL = "2026-10-15T22:00:00+04:00"      # Saint-Pierre, heure de La Réunion
RACE_TZ = "Indian/Reunion"


def coach_home(env: Mapping[str, str] | None = None) -> Path:
    """`env` : variables d'environnement (os.environ par défaut ; un dict dans les tests)."""
    env = os.environ if env is None else env
    home = Path(env.get("COACH_HOME", Path.home() / ".ai_coach")).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    try:
        home.chmod(0o700)                            # données de santé : dossier privé
    except OSError:
        pass
    return home


def local_timezone(env: Mapping[str, str] | None = None, localtime: str = "/etc/localtime") -> str:
    """Fuseau IANA de la machine (macOS/Linux : lien /etc/localtime), sinon COACH_TZ."""
    env = os.environ if env is None else env
    if env.get("COACH_TZ"):
        return env["COACH_TZ"]
    try:
        target = os.path.realpath(localtime)
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "Europe/Paris"


@dataclass
class PilotSettings:
    hr_rest: float = 40.0
    hr_max: float = 199.0
    lthr: float | None = None          # FC au seuil ; défaut 93 % de FCmax
    sex: str = "M"
    race_name: str = "Diagonale des Fous"
    race_start: str = DIAG_START_LOCAL
    race_duration_min: int = 2172      # 36,2 h : médiane de ta prévision Race Pacer gelée
    race_tz: str = RACE_TZ             # fuseau du départ (VVX : Europe/Paris)
    goal: str = ""                     # objectif en une phrase, transmis au coach (COACH_GOAL)
    session_hour: int = 18             # heure des séances dans l'agenda
    tz: str = "Europe/Paris"
    daily_budget_eur: float = 0.50     # plafond FinOps du bot en mode --claude

    @property
    def race_date(self) -> date:
        return datetime.fromisoformat(self.race_start).date()

    @property
    def threshold_hr(self) -> float:
        return self.lthr or round(0.93 * self.hr_max)


def load_settings(env: Mapping[str, str] | None = None, repo_root: Path | None = None) -> PilotSettings:
    """Réglages : profil data/athletes/pilote.json puis variables COACH_* (`env` et `repo_root` injectables)."""
    env = os.environ if env is None else env
    s = PilotSettings(tz=local_timezone(env))
    prof = (repo_root or REPO_ROOT) / "data" / "athletes" / "pilote.json"
    if prof.exists():
        d = json.loads(prof.read_text())
        s.hr_rest, s.hr_max = float(d.get("hr_rest", s.hr_rest)), float(d.get("hr_max", s.hr_max))
    for var, attr, cast in [("COACH_HR_REST", "hr_rest", float), ("COACH_HR_MAX", "hr_max", float),
                            ("COACH_LTHR", "lthr", float), ("COACH_SESSION_HOUR", "session_hour", int),
                            ("COACH_DAILY_BUDGET_EUR", "daily_budget_eur", float),
                            ("COACH_RACE_START", "race_start", str), ("COACH_RACE_NAME", "race_name", str),
                            ("COACH_RACE_TZ", "race_tz", str), ("COACH_RACE_DURATION_MIN", "race_duration_min", int),
                            ("COACH_GOAL", "goal", str)]:
        if env.get(var):
            setattr(s, attr, cast(env[var]))
    return s
