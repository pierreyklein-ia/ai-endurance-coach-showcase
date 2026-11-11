"""Branche le stockage pilote sur le graphe : AthleteRepo + CalendarPort réels."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..graph import AthleteContext
from ..guardrails import PlanContext, apply_changes, check
from ..load_engine import LoadState, pmc
from ..nutrition import prompt_brief as _nutri_brief
from ..schemas import GuardrailVerdict, PlanChange, PlannedSession, Signals
from .config import PilotSettings
from .planner import TaperInputs, pre_taper_inputs, taper_plan
from .store import PilotStore

TAPER_DAYS = 14
HISTORY_DAYS = 120


def load_state(store: PilotStore, today: date) -> tuple[LoadState, int]:
    """État PMC/ACWR à la fin de la veille (+ ce qui est déjà fait aujourd'hui).

    Retourne aussi le nombre de jours d'historique (fiabilité de l'ACWR)."""
    first = store.first_activity_day()
    if first is None:
        return LoadState(ctl=0, atl=0, tsb=0, acwr=float("nan"), a7=0, c28=0), 0
    start = max(first, today - timedelta(days=HISTORY_DAYS))
    # Jour en cours compté seulement s'il y a déjà une sortie : sinon un « 0 » provisoire
    # ferait chuter l'ACWR chaque matin (la séance du jour est projetée par les garde-fous).
    end = today if store.has_activity_on(today) else today - timedelta(days=1)
    loads = store.daily_loads(start, max(end, start))
    seed = sum(loads[:14]) / 14                     # amorce : évite un CTL parti de 0
    states = pmc(loads, ctl0=seed, atl0=seed, a7_0=seed, c28_0=seed)
    return states[-1], (today - first).days + 1


def taper_inputs(store: PilotStore, race_day: date) -> TaperInputs | None:
    """Charge des 28 jours qui précèdent l'affûtage (J-42 à J-15)."""
    t0 = race_day - timedelta(days=TAPER_DAYS)
    lo = t0 - timedelta(days=28)
    if store.first_activity_day() is None or store.first_activity_day() > lo + timedelta(days=7):
        return None                                 # moins de 3 semaines d'historique : on ne devine pas
    acts = [a for a in store.activities(since=lo) if a.local_day < t0]
    runs = [a for a in acts if a.sport in ("Course", "Trail", "Rando")]
    longs = sorted((a.duration_s / 60 for a in runs), reverse=True)[:4]
    hours = sum(a.duration_s for a in runs) / 3600
    dph = (sum(a.dplus_m for a in runs) / hours) if hours > 0 else None
    return pre_taper_inputs(store.daily_loads(lo, t0 - timedelta(days=1)),
                            len({a.local_day for a in acts if a.duration_s >= 20 * 60}), longs, dph)


@dataclass
class PilotRepo:
    store: PilotStore
    settings: PilotSettings
    today_fn: callable = date.today

    @property
    def race_date(self) -> date:
        return self.settings.race_date

    def plan(self, aid: str = "", days: int = 7) -> list[PlannedSession]:
        today = self.today_fn()
        sessions = self.store.plan(today, today + timedelta(days=days - 1))
        if self.store.has_activity_on(today):       # séance du jour déjà faite
            sessions = [s for s in sessions if s.day != today or s.is_race]
        return sessions

    def get(self, aid: str) -> AthleteContext:
        today = self.today_fn()
        state, hist_days = load_state(self.store, today)
        ti = taper_inputs(self.store, self.race_date)
        baseline = self.store.get("baseline_loads", {})
        base_week = sum(v for d, v in baseline.items()
                        if 0 <= (date.fromisoformat(d) - today).days < 7)
        note = "" if hist_days >= 28 else f" Historique de charge court ({hist_days} j) : ACWR peu fiable."
        return AthleteContext(
            athlete_id=aid, race_date=self.race_date, load=state, plan_next_7d=self.plan(aid),
            baseline_week_load=base_week,
            profile_summary=(f"{self.store.get('athlete_label', 'Ultra-traileur')}, prépare {self.settings.race_name} "
                             f"(départ {self.settings.race_start[:16].replace('T', ' ')}, J-{(self.race_date - today).days}). "
                             + (self.settings.goal + " " if self.settings.goal else "")
                             + self.store.get("diagnosis_brief", "Pilote du projet : ses propres données.") + note
                             + ((" " + _nutri_brief(self.store.get("nutrition_logs"))) if self.store.get("nutrition_logs") else "")
                             + self._phase_brief(today) + self._week_max_brief() + self._zones_brief()
                             + self._feelings_brief()),
            pre_taper_week_load=ti.pre_taper_week_load if ti else None,
            pre_taper_sessions_per_week=ti.sessions_per_week if ti else None,
            longest_run_30d_min=self.longest_run_30d(today), week_max=self.week_max())

    def week_max(self):
        """v0.49 : volume maximum déclaré (préférences) avec la vitesse et le D+/h habituels du bilan."""
        from ..week_max import from_prefs
        try:
            return from_prefs(self.store.get("prefs"), self.store.get("diag_full"))
        except Exception:                                       # noqa: BLE001
            return None

    def _week_max_brief(self) -> str:
        from ..week_max import coach_brief
        prefs = self.store.get("prefs")
        txt = coach_brief(prefs)
        if txt:
            return " " + txt
        # compte existant sans plafond : le coach peut le demander, une seule fois (marqué après le tour)
        if prefs is not None and self.store.get("profile") and not self.store.get("week_max_hint_done"):
            return (" Volume maximum par semaine : jamais demandé à l'athlète. Si l'occasion s'y prête (plan, volume, "
                    "fatigue, emploi du temps), demande-lui une fois le maximum qu'il peut faire par semaine (temps, et "
                    "s'il le sait km et D+) : il peut répondre dans le chat ou dans Carrière › Réglages.")
        return ""

    def _feelings_brief(self) -> str:
        """v0.51 : ressenti de fin de semaine (les deux derniers), pour que le coach ajuste."""
        feel = self.store.get("weekly_feelings") or {}
        out = []
        for k in sorted(feel)[-2:]:
            f = feel[k]
            out.append(f"semaine du {date.fromisoformat(k):%d/%m} : fatigue {f['fatigue']}/5, difficulté "
                       f"{f['difficulty']}/5, plaisir {f['fun']}/5" + (f" (« {f['note'][:120]} »)" if f.get("note") else ""))
        return (" Ressenti de l'athlète : " + " ; ".join(out) + ".") if out else ""

    def _zones_brief(self) -> str:
        """v0.50 : FC repères, zones et cibles par type de séance (Carrière › Réglages › Mes zones)."""
        from ..hr_zones import RESOLVED_KEY, coach_brief
        try:
            txt = coach_brief(self.store.get(RESOLVED_KEY))
        except Exception:                                       # noqa: BLE001
            return ""
        return " " + txt if txt else ""

    def _phase_brief(self, today: date) -> str:
        """v0.38 : phase spécifique de l'objectif principal (en cours ou dans les 3 semaines) pour le coach."""
        from ..race_specific import phase_brief
        try:
            txt = phase_brief(self.store.get("profile"), today)
        except Exception:                                       # noqa: BLE001 : le contexte n'empêche jamais un tour
            return ""
        return " " + txt if txt else ""

    def longest_run_30d(self, today: date) -> float | None:
        """Plus longue sortie de course réalisée dans les 30 derniers jours, en minutes (réalisé seulement)."""
        runs = [a.duration_s / 60 for a in self.store.activities(since=today - timedelta(days=30))
                if a.sport in ("Course", "Trail") and a.local_day < today]
        return max(runs) if runs else None

    # ------------------------------------------------------------ plan d'affûtage
    def propose_taper(self, start: date | None = None) -> tuple[list[PlannedSession], GuardrailVerdict, TaperInputs]:
        today = self.today_fn()
        start = start or today + timedelta(days=1)
        ti = taper_inputs(self.store, self.race_date)
        if ti is None:
            raise LookupError("historique insuffisant : il faut au moins 3 semaines de sorties "
                              "avant le début de l'affûtage (synchronise Strava d'abord)")
        plan = taper_plan(self.race_date, ti, start, self.settings.race_name, self.settings.race_duration_min)
        state, _ = load_state(self.store, today)
        verdict = check(plan, Signals(), PlanContext(
            state=state, baseline_week_load=0, today=start, race_date=self.race_date,
            pre_taper_week_load=ti.pre_taper_week_load, pre_taper_sessions_per_week=ti.sessions_per_week,
            week_max=self.week_max()))
        return plan, verdict, ti

    def adopt(self, plan: list[PlannedSession], from_day=None) -> None:
        """Le plan accepté devient la référence (règle anti-rattrapage R05)."""
        self.store.replace_plan(plan, from_day or plan[0].day)
        self.store.set("baseline_loads", {s.day.isoformat(): s.planned_load for s in plan if not s.is_race})


class PilotCalendar:
    """Écrit dans Google Agenda PUIS met à jour le plan local (même diff)."""

    def __init__(self, google, store: PilotStore):
        self.google, self.store = google, store
        # v0.38 : posé par l'application le temps d'une réponse à une question oui/non, pour que les séances
        # déplacées entre-temps par l'athlète gardent leur jour (coach/web/move_flow.rebase_changes)
        self.rebase = None

    def apply(self, athlete_id: str, changes: list[PlanChange]) -> list[str]:
        if self.rebase:
            changes = self.rebase(changes)
        from ..session_content import harmonize  # v0.50 : l'agenda reçoit le texte cohérent
        changes = [c.model_copy(update={"session": harmonize(c.session)}) for c in changes]
        try:
            ops = self.google.apply(athlete_id, changes) if self.google else []
        except Exception as e:
            if "RefreshError" in type(e).__name__ or "invalid_grant" in str(e):
                raise RuntimeError("accès Google Agenda expiré (7 jours tant que l'appli Google est en "
                                   "mode Test) : relance `python -m coach.pilot google` sur ton Mac") from e
            raise
        self.store.apply(changes)
        return ops


def plan_diff(old: list[PlannedSession], new: list[PlannedSession]) -> list[PlanChange]:
    """Diff complet pour écrire un nouveau plan (annule ce qui disparaît)."""
    new_ids = {s.session_id for s in new}
    out = [PlanChange(action="cancel", session=s, reason="nouveau plan")
           for s in old if s.session_id not in new_ids]
    out += [PlanChange(action="add", session=s, reason="plan d'affûtage") for s in new]
    return out


__all__ = ["PilotRepo", "PilotCalendar", "load_state", "taper_inputs", "plan_diff", "apply_changes"]
