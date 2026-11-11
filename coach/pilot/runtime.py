"""Assemblage du pilote : vrais adaptateurs (Claude, Google Agenda, Strava MCP)."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ..adapters.calendar import GoogleCalendarAdapter
from ..checkpoint import sqlite_saver
from ..graph import Deps, build_graph
from ..knowledge import KNOWLEDGE
from ..persona import load as load_persona
from ..persona import persona_block
from ..rag import SEED_CORPUS, TagRetriever
from ..sandbox import JOURS, LABELS, HeuristicLLM, format_agenda
from ..schemas import GuardrailVerdict, PlannedSession
from ..vocab import get as vocab_of
from .config import PilotSettings, coach_home, load_settings
from .repo import PilotCalendar, PilotRepo, load_state, plan_diff
from .store import Activity, PilotStore


@dataclass
class Pilot:
    graph: object
    deps: Deps
    store: PilotStore
    repo: PilotRepo
    google: GoogleCalendarAdapter | None
    settings: PilotSettings
    home: Path
    strava: object | None = None

    # ---------------------------------------------------------------- textes
    def status_text(self) -> str:
        today = self.deps.today()
        state, hist = load_state(self.store, today)
        j = (self.settings.race_date - today).days
        if self.store.first_activity_day() is None:
            return ("Aucune sortie importée pour l'instant. Synchronise Strava (/sync) "
                    "ou envoie-moi un fichier FIT.")
        acwr = "n/d" if math.isnan(state.acwr) else f"{state.acwr:.2f}".replace(".", ",")
        lines = [f"📊 État au {today:%d/%m} — {self.settings.race_name} J-{j}",
                 f"Forme (CTL) {state.ctl:.0f} · Fatigue (ATL) {state.atl:.0f} · "
                 f"Fraîcheur (TSB) {state.ctl - state.atl:+.0f} · ACWR {acwr}",
                 f"Historique : {hist} j" + (" ⚠ moins de 28 j, ACWR peu fiable" if hist < 28 else ""),
                 "", "Dernières sorties :"]
        for a in self.store.activities(since=today - timedelta(days=7))[-6:]:
            lines.append("  " + activity_line(a))
        last = self.store.get("strava_last_sync")
        if last:
            lines.append(f"\nDernière synchro Strava : {last.replace('T', ' ')}")
        return "\n".join(lines)

    def agenda_text(self, days: int = 7) -> str:
        plan = self.repo.plan(days=days)
        return format_agenda(plan) if plan else "  (aucune séance planifiée : /plan pour générer l'affûtage)"

    def propose_text(self, plan: list[PlannedSession], verdict: GuardrailVerdict, ti) -> str:
        head = (f"Plan d'affûtage proposé (base : charge hebdo avant affûtage {ti.pre_taper_week_load:.0f}, "
                f"{fr(ti.sessions_per_week)} séances/sem., sortie longue type {fr(ti.typical_long_min / 60)} h).\n"
                "Volume ≈ -35 % puis -55 %, intensité conservée (Wang et al. 2023).")
        if verdict.violations:
            head += "\nGarde-fous : " + "; ".join(
                f"{'⛔' if v.severity == 'block' else '⚠'} {v.detail}" for v in verdict.violations)
        else:
            head += "\nGarde-fous : ✅"
        race = next((s for s in plan if s.is_race), None)
        before = [s for s in plan if race is None or s.day < race.day]
        after = [s for s in plan if race is not None and s.day > race.day]
        txt = head + "\n\n" + format_agenda(before) + "\n" + race_line(plan)
        if after:
            txt += f"\n  puis récupération : repos du {after[0].day:%d/%m} au {after[-1].day:%d/%m}"
        return txt

    # ---------------------------------------------------------------- actions
    def write_plan(self, plan: list[PlannedSession], from_day=None) -> list[str]:
        """`from_day` (v0.48) : l'ancien plan est remplacé à partir de ce jour, même si la nouvelle 1re séance tombe
        plus tard (sinon une séance de l'ancien plan placée avant restait)."""
        start = min(from_day, plan[0].day) if from_day else plan[0].day
        old = self.store.plan(since=start)
        ops = self.deps.calendar.apply("pilote", plan_diff(old, plan))
        self.repo.adopt(plan, start)
        return ops


def fr(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def activity_line(a: Activity) -> str:
    km = f"{fr(a.distance_m / 1000)} km, " if a.distance_m else ""
    dp = f"D+ {a.dplus_m:.0f} m, " if a.dplus_m else ""
    hr = f"FC {a.avg_hr:.0f}, " if a.avg_hr else ""
    src = {"hrTSS": "", "csv_avg_hr": " (FC moyenne)", "estimate": " (estimée, sans FC)"}.get(a.load_source, "")
    return (f"{JOURS[a.local_day.weekday()]} {a.local_day:%d/%m} {a.sport} {a.duration_s / 60:.0f} min, "
            f"{km}{dp}{hr}charge {a.load:.0f}{src}")


def race_line(plan: list[PlannedSession]) -> str:
    r = next((s for s in plan if s.is_race), None)
    return f"  {JOURS[r.day.weekday()]} {r.day:%d/%m}  🏁 {r.description}" if r else ""


def build_pilot(use_claude: bool = False, *, home: Path | None = None, calendar_service=None,
                llm=None, strava=None, today_fn: Callable[[], date] = date.today,
                settings: PilotSettings | None = None) -> Pilot:
    home = home or coach_home()
    st = settings or load_settings()
    store = PilotStore(home / "pilot.db")
    repo = PilotRepo(store, st, today_fn)
    google = None
    if calendar_service is not None:
        google = GoogleCalendarAdapter(calendar_service, "primary", st.tz, st.session_hour,
                                       race_start=datetime.fromisoformat(st.race_start), race_tz=st.race_tz)
    if llm is None:
        if use_claude:
            from ..llm import AnthropicLLM, FallbackLLM
            llm = FallbackLLM(AnthropicLLM(), HeuristicLLM(today_fn()))
        else:
            llm = HeuristicLLM(today_fn())
    from ..agents.data import StoreData
    deps = Deps(llm=llm, retriever=TagRetriever(SEED_CORPUS + KNOWLEDGE), calendar=PilotCalendar(google, store),
                repo=repo, today=today_fn,
                persona=lambda: persona_block(load_persona(store), vocab_of(store), store.get("athlete_sex")),
                data=StoreData(store, st, today_fn),       # v0.40 : données des outils des spécialistes
                history=lambda: recent_chat(store))        # v0.52 : le coach voit la discussion récente       # v0.40 : données des outils des spécialistes
    graph = build_graph(deps, checkpointer=sqlite_saver(home / "checkpoints.db"))
    return Pilot(graph, deps, store, repo, google, st, home, strava)


def recent_chat(store, n: int = 8) -> list[dict]:
    """v0.52 : les derniers messages du fil (texte tronqué), pour que le coach suive la discussion."""
    out = []
    for m in store.chat(n):
        if m.get("role") not in ("user", "coach") or m.get("kind") == "system":
            continue
        out.append({"role": "athlete" if m["role"] == "user" else "coach", "at": (m.get("at") or "")[:16],
                    "text": (m.get("text") or "")[:700]})
    return out


__all__ = ["Pilot", "build_pilot", "activity_line", "recent_chat", "LABELS"]
