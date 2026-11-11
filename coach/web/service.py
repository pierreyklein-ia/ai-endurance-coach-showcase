"""Logique de l'application, indépendante de HTTP (testable sans serveur).

Le graphe LangGraph, les garde-fous, le calcul de charge et l'agenda sont ceux du
pilote : l'application n'est qu'un nouvel adaptateur de canal (comme Telegram).
Un verrou sérialise les accès (SQLite + graphe ne sont pas faits pour le parallèle,
et un seul athlète écrit).
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from datetime import date, datetime, timedelta

from .. import persona, vocab
from ..agents.trace import team_meta
from ..app import handle_inbound
from ..onboarding import (
    Profile,
    commitments,
)
from ..pilot.ingest import activity_from_bytes
from ..pilot.repo import load_state
from ..pilot.runtime import Pilot
from ..pilot.store import Activity
from ..sandbox import LABELS, cost_eur, format_trace
from ..schemas import Channel, InboundMessage, PlannedSession
from .auto_validate import AutoValidateFlow
from .career_flow import CareerFlow
from .eye_flow import EyeFlow
from .move_flow import MOVED_KEY, MoveFlow, MoveRefused, rebase_changes, two_hard_alert
from .nutrition_flow import NutritionFlow
from .onboarding_flow import OB_STATES, OnboardingFlow
from .push import Notifier
from .race_flow import RaceFlow
from .recap_flow import RecapFlow
from .zones_flow import ZonesFlow

log = logging.getLogger("coach.web")
__all__ = ["CoachService", "MoveRefused", "monday"]


def _fold(t: str) -> str:
    """Minuscules sans accents, pour la recherche."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")
ACCEPTED = (".fit", ".fit.gz", ".gpx", ".gpx.gz")
MAX_UPLOAD = 25 * 1024 * 1024


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _num(x: float, nd: int = 0):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, nd)



class TurnQueue:
    """v0.39.1 : file des tours du coach, servis dans l'ordre des tickets (un message tapé pendant une réaction en
    tâche de fond passe APRÈS elle, même si le thread de la réaction n'a pas encore démarré)."""

    def __init__(self):
        self._c = threading.Condition()
        self._next = self._serving = 0

    def ticket(self) -> int:
        with self._c:
            t = self._next
            self._next += 1
            return t

    def wait(self, t: int, timeout: float = 600.0) -> None:
        end = time.time() + timeout
        with self._c:
            while self._serving < t and time.time() < end:  # filet : jamais bloqué indéfiniment
                self._c.wait(max(0.0, end - time.time()))

    def done(self) -> None:
        with self._c:
            self._serving += 1
            self._c.notify_all()

SYNC_EVERY_MIN = 30          # v0.52.3 : synchro Strava automatique (avant : 2 h)
SYNC_ON_OPEN_S = 5 * 60      # v0.52.3 : à l'ouverture de l'appli, synchro si la dernière a plus de 5 min


class CoachService(OnboardingFlow, CareerFlow, MoveFlow, NutritionFlow, RaceFlow, EyeFlow, AutoValidateFlow, ZonesFlow,
                   RecapFlow):
    def __init__(self, pilot: Pilot, notifier: Notifier | None = None, estimated: bool = True,
                 sync_every_min: int | None = None, connector=None, strava_factory=None, background: bool = True,
                 coach_async: bool | None = None):
        self.pilot, self.notifier, self.estimated = pilot, notifier, estimated
        self.connector, self.background = connector, background
        self.strava_factory = strava_factory or (lambda: None)
        self.job: dict = {}
        self.lock = threading.RLock()
        # v0.38 : réactions du coach (déplacement, bilan nutrition) en tâche de fond ; `coach_async=False`
        # les exécute dans la requête (tests déterministes). Ordre des verrous : turn_lock PUIS lock.
        self.coach_async = background if coach_async is None else coach_async
        self.turn_lock = threading.RLock()                  # un seul tour de graphe à la fois
        self._turns = TurnQueue()                           # v0.39.1 : et dans l'ordre d'arrivée des messages
        self._typing_lock = threading.Lock()
        self._typing: list[dict] = []
        self._bg: list[threading.Thread] = []
        if sync_every_min is None:                          # v0.52.3 : toutes les 30 min (COACH_SYNC_MIN, 10 à 240)
            try:
                sync_every_min = int(os.environ.get("COACH_SYNC_MIN", SYNC_EVERY_MIN))
            except ValueError:
                sync_every_min = SYNC_EVERY_MIN
            sync_every_min = max(10, min(240, sync_every_min))
        self.sync_every_s, self.last_sync = sync_every_min * 60, 0.0
        self._sync_bg: threading.Thread | None = None
        data = getattr(self.pilot.deps, "data", None)
        if data is not None and hasattr(data, "race_sim_fn"):    # v0.40 : outil `race_simulation` des spécialistes
            data.race_sim_fn = self.race_sim_nolock
        self.apply_profile()
        try:                                                       # v0.50 : zones FC -> moteur de charge
            self.apply_hr_settings()
        except Exception:                                          # noqa: BLE001 : jamais bloquant au démarrage
            log.exception("zones cardiaques")
        if self.pilot.google is not None:                          # v0.50 : cible FC dans l'agenda
            self.pilot.google.extra_fn = self.calendar_extra
        self.startup_repairs()                                     # v0.48 : montre oubliée, date de l'objectif
        if not self.store.get("sports_relabeled_v025"):           # v0.25 : renfo et trail de l'API Strava
            from ..pilot.ingest import sport_label
            n = self.store.relabel_sports(sport_label)
            self.store.set("sports_relabeled_v025", True)
            if n:
                log.info("%d activités renommées (libellés de sport)", n)

    # ---------------------------------------------------------------- utilitaires
    @property
    def store(self):
        return self.pilot.store

    def today(self) -> date:
        return self.pilot.deps.today()

    def _meta(self) -> dict:
        return self.store.get("session_meta", {})

    def session_view(self, s: PlannedSession, meta: dict, fb: dict) -> dict:
        m = meta.get(s.session_id, {})
        title, detail = m.get("title"), m.get("detail")
        if not title:                                   # séance générée par le coach
            title = "🏁 " + s.description if s.is_race else LABELS.get(s.intensity, s.intensity)
        if not s.is_race:                               # v0.50 : le texte de la séance fait foi (durée cohérente)
            detail = s.description or detail
        else:
            detail = detail if m.get("title") else ""
        terms = []
        if vocab.get(self.store) == "simple" and not s.is_race:      # v0.48 : titres en clair, « C'est quoi ? »
            terms = vocab.terms_in(title, detail or "")
            title = vocab.simple_title(title)
        return {"session_id": s.session_id, "day": s.day.isoformat(), "intensity": s.intensity,
                "kind": m.get("kind", s.intensity), "title": title, "detail": detail or "",
                **({"terms": terms} if terms else {}),
                "duration_min": s.duration_min, "elevation_gain_m": s.elevation_gain_m,
                "planned_load": s.planned_load, "is_race": s.is_race, "key": m.get("key", s.intensity in
                ("threshold", "vo2", "long", "tempo", "hills", "race")), "feedback": fb.get(s.session_id),
                "optional": bool(m.get("optional") or s.session_id.endswith("-bonus")),
                **({"orig_title": m["orig"].get("title")} if (m.get("orig") or {}).get("title")
                   and m["orig"]["title"] != title else {}),       # v0.50.1 : séance transformée par le coach
                "target": self.session_target(s)}                # v0.50 : FC cible, allure, effort

    @staticmethod
    def activity_view(a: Activity) -> dict:
        return {"day": a.local_day.isoformat(), "start": a.start_utc.isoformat(), "sport": a.sport,
                "name": a.name, "minutes": round(a.duration_s / 60), "km": round(a.distance_m / 1000, 1),
                "dplus": round(a.dplus_m or 0), "avg_hr": _num(a.avg_hr), "load": round(a.load),
                "load_source": a.load_source, **({"suspect": True} if a.suspect else {}),
                **({"elapsed_min": round(a.elapsed_s / 60)} if a.elapsed_s else {})}

    def _sessions(self, start: date, end: date) -> list[dict]:
        plan = [s for s in self.store.plan(start, end) if s.intensity != "rest" or s.session_id in self._meta()]
        fb = self.store.feedback_for([s.session_id for s in plan])
        meta = self._meta()
        out = [self.session_view(s, meta, fb) for s in plan]
        self._with_nutrition(out, start, end)               # v0.38 : durée réelle, bilan nutrition
        self._with_auto(out)                                # v0.42 : validée automatiquement (ressenti à compléter)
        return out

    def state(self) -> dict:
        today = self.today()
        st, hist = load_state(self.store, today)
        return {"ctl": _num(st.ctl), "atl": _num(st.atl), "tsb": _num(st.ctl - st.atl),
                "acwr": _num(st.acwr, 2), "history_days": hist,
                "last_sync": self.store.get("strava_last_sync")}

    # ---------------------------------------------------------------- vues
    def me(self) -> dict:
        s, today = self.pilot.settings, self.today()
        prof = self.profile()
        base = {"today": today.isoformat(), "onboarding": self.needs_onboarding(),
                "first_name": self.store.get("athlete_first_name", ""),
                "google": self.pilot.google is not None, "strava": self.pilot.strava is not None,
                "push": bool(self.notifier and self.notifier.enabled),
                "budget_eur": s.daily_budget_eur, "spent_today_eur": round(self._spent(), 3),
                "coach": self.coach_persona()}
        if prof is None and self.needs_onboarding():
            return {**base, "race_name": None, "race_date": None, "days_to_race": None}
        return {**base, "race_name": s.race_name, "race_date": s.race_date.isoformat(),
                "days_to_race": (s.race_date - today).days}

    # v0.36 : coach personnalisable (ton seulement ; garde-fous et plan inchangés)
    def coach_persona(self) -> dict:
        return persona.view(self.store)

    def set_coach_persona(self, p: persona.CoachPersona) -> dict:
        with self.lock:
            return persona.save(self.store, p)

    # ---------------------------------------------------------------- v0.50 : premier jour de la semaine
    def week_start_day(self) -> int:
        return self.prefs().week_start % 7

    def week_start(self, d: date) -> date:
        """Premier jour de la semaine de l'athlète contenant `d` (lundi par défaut)."""
        return d - timedelta(days=(d.weekday() - self.week_start_day()) % 7)

    def _week_meta(self, start: date) -> dict:
        """Infos de semaine du plan (phase, focus, séances clés) : clé = début de semaine ; une semaine du plan
        enregistrée avec un autre premier jour (changé depuis) est retrouvée dans les 7 jours."""
        weeks = self.store.get("weeks", {})
        for i in range(7):
            w = weeks.get((start + timedelta(days=i)).isoformat())
            if w:
                return w
        return {}

    def week_start_view(self) -> dict:
        from ..onboarding import JOURS
        ws = self.week_start_day()
        return {"day": ws, "label": JOURS[ws], "end_label": JOURS[(ws + 6) % 7], "days": JOURS}

    def set_week_start(self, day: int) -> dict:
        if not 0 <= int(day) <= 6:
            raise ValueError("jour entre 0 (lundi) et 6 (dimanche)")
        with self.lock:
            prefs = self.prefs()
            prefs.week_start = int(day)
            self.store.set("prefs", prefs.model_dump(mode="json"))
            if self.profile() is not None:
                self.apply_profile()
        return {"ok": True, **self.week_start_view()}

    def today_view(self) -> dict:
        with self.lock:
            today = self.today()
            week = self._week_meta(self.week_start(today))
            upcoming = self.store.plan(today + timedelta(days=1), today + timedelta(days=14))
            nxt = next((s for s in upcoming if s.intensity != "rest"), None)
            return {**self.me(), "sessions": self._sessions(today, today),
                    "activities": [self.activity_view(a) for a in self.store.activities(since=today)],
                    "state": self.state(), "focus": week.get("focus"), "coach_note": week.get("coachNote"),
                    "next": self.session_view(nxt, self._meta(), {}) if nxt else None}

    def week_view(self, start: date | None = None) -> dict:
        with self.lock:
            start = self.week_start(start or self.today())      # v0.50 : sa semaine (lundi par défaut)
            end = start + timedelta(days=6)
            sessions = self._sessions(start, end)
            acts = [self.activity_view(a) for a in self.store.activities(since=start) if a.local_day <= end]
            days = []
            raw = self.store.plan(start, end)
            for i in range(7):
                d = (start + timedelta(days=i)).isoformat()
                # v0.38 : alerte du jour (deux séances dures le même jour), avec un libellé (pas que la couleur)
                days.append({"date": d, "sessions": [s for s in sessions if s["day"] == d],
                             "activities": [a for a in acts if a["day"] == d],
                             "alert": two_hard_alert([x for x in raw if x.day.isoformat() == d])})
            w = self._week_meta(start)
            planned = sum(s["duration_min"] for s in sessions if not s["optional"])
            done = sum(a["minutes"] for a in acts)
            return {"start": start.isoformat(), "days": days, "phase": w.get("phase"), "focus": w.get("focus"),
                    "coach_note": w.get("coachNote"), "keys": w.get("keys") or [], "review": w.get("review"), "reviewed_at": w.get("reviewedAt"),
                    "planned_min": planned, "done_min": done, "today": self.today().isoformat(),
                    "week_start": self.week_start_day(), "recap": self.recap_summary()}

    def progress_view(self, days: int = 180) -> dict:
        """Onglet Progrès (v0.19)."""
        from ..progress import progress
        days = max(28, min(int(days), 365 * 5 + 2))
        with self.lock:
            today = self.today()
            p = self.profile()
            # v0.37 : même liste que « Mes objectifs » (principal puis secondaires, par date)
            mine = self.goals(with_estimate=False)
            goals = [{**g, "date": date.fromisoformat(g["date"]) if g["date"] else None} for g in mine
                     if g["role"] == "A" or (g["role"] == "B" and g["date"] and not g["done"])] if p else []
            out = progress(self.store.activities(), self.store.plan(), [g for g in goals if g.get("km")], today, days)
            out["goals_without_distance"] = [g["name"] for g in goals if not g.get("km")]
            out["profile"] = self._runner_profile(today, p, out)
            self._goal_est = {c["name"]: c["estimate"] for c in out.get("goals") or [] if c.get("estimate")}
            out["my_goals"] = self.goals()
            return out

    def goals(self, with_estimate: bool = True) -> list[dict]:
        """v0.37 : objectifs classés (principal, secondaires, projets) ; estimation si déjà calculée par Progrès."""
        from ..goals import goals_list
        s = self.pilot.settings
        p = self.profile()
        out = goals_list(p, self.today(), s.race_name if p is None and not self.needs_onboarding() else None,
                         s.race_date if p is None and not self.needs_onboarding() else None)
        if with_estimate:
            est = getattr(self, "_goal_est", {})
            for g in out:
                g["estimate"] = est.get(g["name"]) if not g["done"] else None
        return out

    def _runner_profile(self, today: date, p: Profile | None, prog: dict) -> dict:
        """Radar du profil (v0.20)."""
        from ..load_engine import pmc
        from ..radar import runner_profile
        acts = self.store.activities()
        goal, goal_s = None, None
        if p and p.goal_distance_km:
            goal = {"name": p.goal_name, "km": p.goal_distance_km, "dplus": p.goal_dplus_m}
            card = next((g for g in prog.get("goals", []) if g["name"] == p.goal_name and g.get("estimate")), None)
            goal_s = card["estimate"][1] if card else None
        dec = [{"day": date.fromisoformat(v["day"]), "dec": v["dec"]} for v in (self.store.get("decoupling") or {}).values()]
        first = self.store.first_activity_day()

        def acwr(lo: date, hi: date) -> list[float]:
            if not first:
                return []
            loads = self.store.daily_loads(first, hi)
            seed = sum(loads[:14]) / 14
            st = pmc(loads, ctl0=seed, atl0=seed, a7_0=seed, c28_0=seed)
            k = max(0, (lo - first).days)
            return [s.acwr for s in st[k:] if s.acwr == s.acwr]
        return runner_profile(acts, today, goal, goal_s, getattr(self.pilot.settings, "sex", "M") or "M", dec, acwr)

    def activities_page(self, before: str | None = None, limit: int = 40, sport: str | None = None,
                        q: str | None = None, offset: int = 0) -> dict:
        """Suivi des activités : tout l'historique, page par page (les plus récentes d'abord).
        v0.23 : recherche `q` (nom, sport, date « 12/03/2025 », mois « mars 2025 », distance « 50 km ») et
        pagination par `offset` (plusieurs sorties le même jour ne sont plus sautées)."""
        from ..onboarding import MOIS_NOMS
        with self.lock:
            everything = self.store.activities()
        acts = everything
        if before:
            acts = [a for a in acts if a.local_day.isoformat() < before]
        if sport:
            acts = [a for a in acts if a.sport == sport]
        terms = _fold(q or "").split()
        if terms:
            def hay(a):
                d = a.local_day
                return _fold(f"{a.name or ''} {a.sport} {d:%d/%m/%Y} {d.isoformat()} {MOIS_NOMS[d.month - 1]} {d.year} "
                             f"{a.distance_m / 1000:.0f} km")
            acts = [a for a in acts if all(t in hay(a) for t in terms)]
        acts = list(reversed(acts))
        limit, offset = max(1, min(limit, 200)), max(0, offset)
        page = acts[offset:offset + limit]
        more = offset + limit < len(acts)
        return {"activities": [self.activity_view(a) for a in page], "sports": sorted({a.sport for a in everything}),
                "total": len(acts), "next_offset": offset + limit if more else None,
                "next": page[-1].local_day.isoformat() if more and page else None}

    def memory(self) -> dict:
        """Ce que l'athlète a dit (état actuel) et le plan validé (v0.17)."""
        p = self.profile()
        return {"commitments": commitments(p, self.prefs(), self.today()) if p else [],
                "agreed_plan": self.store.get("agreed_plan"), "has_plan": bool(self.store.plan()),
                "versions": len(self.store.get("agreed_history") or [])}

    def state_view(self) -> dict:
        with self.lock:
            since = self.today() - timedelta(days=14)
            return {**self.state(), "memory": self.memory(), "goals": self.goals(),
                    "nutrition": self.nutrition_summary(),
                    "activities": [self.activity_view(a) for a in reversed(self.store.activities(since=since))]}

    # ---------------------------------------------------------------- retours de séance
    def feedback(self, session_id: str, status: str, rpe: int | None, note: str,
                 notify_coach: bool = False) -> dict:
        with self.lock:
            s = next((x for x in self.store.plan() if x.session_id == session_id), None)
            if s is None:
                raise KeyError("séance inconnue")
            fb = self.store.set_feedback(session_id, s.day, status, rpe, note)
            self._forget_auto(session_id)                   # v0.42 : le retour de l'athlète remplace l'automatique
        out = {"feedback": fb, "messages": []}
        if notify_coach:                                        # chat() hors du verrou (ordre turn_lock -> lock)
            title = self._meta().get(session_id, {}).get("title") or LABELS.get(s.intensity, s.intensity)
            verb = {"done": "faite", "modified": "modifiée", "skipped": "sautée"}[status]
            text = f"Séance du {s.day:%d/%m} ({title}) {verb}" + (f", RPE {rpe}/10" if rpe else "") + \
                   (f". {note.strip()}" if note.strip() else ".")
            try:
                out.update(self.chat(text))
            except Exception as e:                              # le retour est enregistré même si le coach échoue
                log.exception("réponse du coach au retour de séance")
                out["coach_error"] = str(e)[:200] or e.__class__.__name__
        return out

    # ---------------------------------------------------------------- chat
    def _spent(self) -> float:
        return float(self.store.get(f"spent:{self.today().isoformat()}", 0.0))

    def _graph_pending(self) -> bool:
        """Question oui/non du graphe en attente (interrupt du nœud `confirm`)."""
        snap = self.pilot.graph.get_state({"configurable": {"thread_id": self.thread}})
        return any(t.interrupts for t in snap.tasks)

    def pending(self) -> bool:
        if self.store.get("ob_state") == "replan":
            return True
        if self.needs_onboarding() and self.store.get("ob_state") in OB_STATES:
            return True
        return self._graph_pending()

    def history(self, limit: int = 60, before: int | None = None) -> dict:
        with self.lock:
            pend = self.pending()
            quick = self.store.get("ob_quick", []) if pend and (self.needs_onboarding()
                                                                or self.store.get("ob_state") == "replan") else []
            return {"messages": self.store.chat(limit, before), "pending": pend, "quick": quick,
                    "typing": self.typing()}

    def chat(self, text: str, *, context: str = "", coaching_only: bool = False) -> dict:
        """Un message de l'athlète. `context` (v0.37) : précision donnée au coach mais non affichée
        (avertissements des garde-fous après un déplacement) ; `coaching_only` : ni accueil ni correction
        d'échéance, directement le graphe de coaching."""
        text = (text or "").strip()[:4000]
        if not text:
            raise ValueError("message vide")
        ticket = self._turns.ticket()                       # passe après les réactions déjà lancées
        self._turns.wait(ticket)
        try:
            return self._chat_turn(text, context=context, coaching_only=coaching_only)
        finally:
            self._turns.done()

    def _chat_turn(self, text: str, *, context: str, coaching_only: bool) -> dict:
        with self.turn_lock, self.lock:                      # attend la fin d'une réaction du coach en cours
            user = self.store.add_chat("user", "text", text)
            vocab.learn_from_text(self.store, text)            # v0.48 : il dit « VMA », « seuil » : vocabulaire expert
            if not coaching_only and self._graph_pending() and self.store.get("ob_state") in (None, "done"):
                coaching_only = True                         # v0.52 : la question oui/non du coach passe d'abord
            if not coaching_only:
                ans = self._onboarding_turn(text, user)
                if ans is not None:
                    return ans
                ans = self._goal_edit_turn(text, user)         # v0.25 : « c'est pour 2027 », « enlève le semi »
                if ans is not None:
                    return ans
                ans = self._prefs_edit_turn(text, user)        # v0.48 : « l'intensité le mardi, la longue le vendredi »
                if ans is not None:
                    return ans
            return self._coach_turn(user, text, context=context)

    # ---------------------------------------------------------------- réactions du coach en tâche de fond (v0.38)
    def typing(self) -> bool:
        """Le coach écrit (une réaction en tâche de fond est en cours)."""
        return bool(self._typing)

    def wait_coach(self, timeout: float = 30.0) -> bool:
        """Tests : attend la fin des réactions en tâche de fond. True si tout est fini."""
        end = time.time() + timeout
        for t in list(self._bg):
            t.join(max(0.0, end - time.time()))
        self._bg = [t for t in self._bg if t.is_alive()]
        return not self._bg

    def _spawn_coach(self, user: dict, text: str, context: str, fallback, *, what: str = "", post=None,
                     tolerate: set[str] | None = None) -> None:
        """Le coach réagit à `text` (déjà dans le chat, message `user`) sans bloquer la requête.
        `fallback(raison)` : message déterministe du coach si le tour échoue (le coach répond TOUJOURS) ;
        `post(corps)` : complément ajouté à sa réponse (ex. pistes nutrition)."""
        def on_error():
            with self.lock:
                self.store.add_chat("coach", "reply", fallback("erreur"), {"fallback": True})
        self._spawn_job(what, lambda: self._coach_turn(user, text, context=context, fallback=fallback, post=post,
                                                       tolerate=tolerate), on_error)

    def _spawn_job(self, what: str, job, on_error) -> None:
        """v0.40 : tâche du coach en tâche de fond (réaction, Œil du coach), dans l'ordre des messages (TurnQueue),
        avec l'indicateur « le coach écrit » ; `on_error()` garantit une réponse dans le chat."""
        token = {"what": what, "since": time.monotonic()}
        with self._typing_lock:
            self._typing.append(token)
        ticket = self._turns.ticket()                       # pris dans la requête : l'ordre est celui des messages

        def run():
            self._turns.wait(ticket)
            try:
                with self._typing_lock:                         # v0.52 : déplacements en rafale -> une réaction
                    newer = what == "move" and any(x is not token and x["what"] == "move" and x["since"] > token["since"]
                                                   for x in self._typing)
                if not newer:
                    job()
            except Exception:                                   # noqa: BLE001 : le coach répond quoi qu'il arrive
                log.exception("tâche du coach (%s)", what)
                on_error()
            finally:
                self._turns.done()
                with self._typing_lock:
                    self._typing.remove(token)

        if self.coach_async:
            t = threading.Thread(target=run, name=f"coach-{what or 'turn'}", daemon=True)
            self._bg = [x for x in self._bg if x.is_alive()] + [t]
            t.start()
        else:
            run()

    def _coach_turn(self, user: dict, text: str, *, context: str = "", fallback=None, post=None,
                    tolerate: set[str] | None = None) -> dict:
        """Un tour du graphe de coaching pour un message déjà enregistré. En tâche de fond, le verrou `lock`
        n'est PAS tenu pendant l'appel au LLM (les vues et les déplacements restent rapides) : seul
        `turn_lock` sérialise les tours."""
        with self.turn_lock:
            with self.lock:
                budget = self.pilot.settings.daily_budget_eur
                if self._spent() >= budget:
                    if fallback:
                        msg = self.store.add_chat("coach", "reply", fallback("budget"), {"fallback": True})
                    else:
                        msg = self.store.add_chat("coach", "system", f"Budget Claude du jour atteint ({budget:.2f} €). "
                                                  "Je reprends demain (réglage : COACH_DAILY_BUDGET_EUR).")
                    return {"messages": [user, msg], "pending": self.pending(), "calendar_changed": False}
                graph_pending = self._graph_pending()
                moved = (self.store.get(MOVED_KEY) or {}) if graph_pending else {}
            llm = self.pilot.deps.llm
            n0, trace = len(llm.usage_log), []
            f0 = getattr(llm, "failures", 0)
            t0 = time.time()
            cal = self.pilot.deps.calendar
            rebase = moved and hasattr(cal, "rebase")
            if rebase:                                          # « si tu dis oui, je garde ton déplacement »
                cal.rebase = lambda ch: rebase_changes(ch, moved)
            deps = self.pilot.deps
            if tolerate:                                        # choix de l'athlète : pas d'allègement forcé
                deps.tolerated = lambda: set(tolerate)
            try:
                graph_text = (text + "\n\n" + context)[:4000] if context else text
                out = handle_inbound(self.pilot.graph, InboundMessage(athlete_id=self.thread, channel=Channel.WEB,
                                                                      text=graph_text), trace)
            except Exception as e:
                log.exception("tour de coaching")
                with self.lock:
                    if fallback:
                        msg = self.store.add_chat("coach", "reply", fallback(type(e).__name__),
                                                  {"fallback": True, "error": type(e).__name__})
                    else:
                        msg = self.store.add_chat("coach", "system", f"⚠ Erreur interne ({type(e).__name__}). "
                                                  "Ton message est gardé, réessaie dans un instant.")
                    return {"messages": [user, msg], "pending": self.pending(), "calendar_changed": False,
                            "coach_error": str(e)[:200]}
            finally:
                if rebase:
                    cal.rebase = None
                if tolerate:
                    deps.tolerated = lambda: set()
            with self.lock:
                if graph_pending and not self._graph_pending():
                    self.store.set(MOVED_KEY, {})               # la question est tranchée
                used = llm.usage_log[n0:]
                cost = cost_eur(used)
                self.store.set(f"spent:{self.today().isoformat()}", self._spent() + cost)
                ops = out.get("calendar_ops") or []
                meta = {"cost_eur": round(cost, 4), "latency_s": round(time.time() - t0, 2),
                        "nodes": [n for n, _ in trace], "calendar_ops": len(ops),
                        "trace": format_trace(trace, used, self.estimated),
                        **team_meta(trace, used)}               # v0.40 : équipe consultée, outils, critique, coût
                kind = "ask" if "ask" in out else "reply"
                body = out.get("ask") or out.get("reply") or "…"
                if post:
                    body = post(body)
                if getattr(llm, "failures", 0) > f0:             # v0.25 : Claude indisponible, réponse par règles
                    body += (f"\n\n(Réponse simplifiée : {llm.degraded or 'Claude est indisponible'}. "
                             "Réessaie plus tard pour une réponse complète.)")
                    meta["degraded"] = llm.degraded
                    if getattr(llm, "degraded_detail", None):
                        meta["degraded_detail"] = llm.degraded_detail     # v0.48 : visible dans « détails »
                msg = self.store.add_chat("coach", kind, body, meta)
                if self.store.get("profile") and not self.store.get("week_max_hint_done"):
                    self.store.set("week_max_hint_done", True)  # v0.49 : le coach a eu une fois l'occasion de demander
                return {"messages": [user, msg], "pending": kind == "ask", "calendar_changed": bool(ops)}

    # ---------------------------------------------------------------- données
    def upload(self, name: str, data: bytes) -> dict:
        if not name.lower().endswith(ACCEPTED):
            raise ValueError("j'attends un fichier FIT (ou GPX horodaté)")
        if len(data) > MAX_UPLOAD:
            raise ValueError("fichier trop gros (25 Mo max)")
        with self.lock:
            a = activity_from_bytes(data, name, self.pilot.settings)
            status = self.store.add_activity(a)
            self.apply_hr_settings()                           # v0.50 : FC max / seuil estimées à jour
            auto = self.auto_validate()                        # v0.42 : la séance prévue du jour est validée
            return {"status": status, "activity": self.activity_view(a), "auto_validated": auto}

    def sync(self, progress=None) -> dict:
        from ..pilot.strava_api import HISTORY_YEARS, backfill_strava_api, durability_backfill, sync_strava_api
        from ..pilot.strava_mcp import sync_strava
        if self.pilot.strava is None:
            return {"ok": False, "message": "Strava n'est pas connecté : État → Connecter Strava.", "new": 0}
        with self.lock:
            try:
                api = getattr(self.pilot.strava, "kind", "") == "api"
                first = self.store.get("strava_api_after" if api else "strava_last_start_local") is None
                kw = ({"days": 365 * HISTORY_YEARS + 1, "max_new": 5000, "stream_days": 42} if api else
                      {"days": 270, "max_new": 600, "stream_days": 42}) if first else {}
                if api and progress:
                    kw["progress"] = progress
                fn = sync_strava_api if api else sync_strava
                res = fn(self.store, self.pilot.settings, self.pilot.strava, today=self.today(), **kw)
                if api and first and res.partial is None:
                    self.store.set("strava_history_years", HISTORY_YEARS)
                elif api and (self.store.get("strava_history_years") or 0) < HISTORY_YEARS and res.partial is None:
                    back = backfill_strava_api(self.store, self.pilot.settings, self.pilot.strava,
                                               today=self.today(), progress=progress)
                    res.report.new += back.report.new           # v0.18 : historique complété une fois
                    res.partial = res.partial or back.partial
                if api and not self.store.get("decoupling_backfilled") and res.partial is None:
                    durability_backfill(self.store, self.pilot.strava, today=self.today())   # v0.20 : radar
            except Exception as e:
                while getattr(e, "exceptions", None):          # ExceptionGroup (anyio)
                    e = e.exceptions[0]
                log.warning("synchro Strava : %s", e)
                return {"ok": False, "message": f"Synchro Strava impossible : {str(e)[:200]}", "new": 0}
            finally:
                self.last_sync = time.time()
            n = res.report.new + res.report.upgraded
            try:                                               # v0.50 : zones Strava et estimations FC
                self.refresh_strava_zones()
                self.apply_hr_settings()
            except Exception:                                  # noqa: BLE001 : la synchro reste réussie
                log.exception("zones cardiaques après synchro")
            try:
                auto = self.auto_validate()                    # v0.42 : séances prévues validées par les sorties
            except Exception:                                  # noqa: BLE001 : la synchro reste réussie
                log.exception("validation automatique")
                auto = []
            news = self.store.activities()[-n:] if n else []
            return {"ok": True, "new": n, "partial": res.partial or None,
                    "message": (f"{n} nouvelle(s) sortie(s)." if n else "Rien de neuf.")
                    + (f" {len(auto)} séance(s) validée(s) automatiquement." if auto else ""),
                    "auto_validated": auto,
                    "activities": [self.activity_view(a) for a in news[-5:]]}

    def sync_if_stale(self, max_age_s: float = SYNC_ON_OPEN_S) -> bool:
        """v0.52.3 : l'athlète ouvre l'appli : synchro Strava en tâche de fond si la dernière est ancienne
        (la page se recharge ensuite d'elle-même). True si une synchro est lancée."""
        if (self.pilot.strava is None or self.job.get("running") or self.needs_onboarding()
                or time.time() - self.last_sync < max_age_s or (self._sync_bg and self._sync_bg.is_alive())):
            return False
        self.last_sync = time.time()                       # pas de 2e lancement pendant celle-ci

        def job():
            try:
                self.sync()
            except Exception:                                # noqa: BLE001 : la synchro périodique reprendra
                log.exception("synchro à l'ouverture")
        if not self.background:
            job()
            return True
        self._sync_bg = threading.Thread(target=job, name="sync-open", daemon=True)
        self._sync_bg.start()
        return True

    def syncing(self) -> bool:
        return bool(self._sync_bg and self._sync_bg.is_alive())

    # ---------------------------------------------------------------- tâches de fond
    def tick(self, now: datetime | None = None) -> list[str]:
        """Appelée chaque minute : synchro périodique, récap de la semaine, rappel du matin, relance du soir."""
        now = now or datetime.now()
        done: list[str] = []
        if (self.pilot.strava is not None and not self.job.get("running") and not self.needs_onboarding()
                and time.time() - self.last_sync >= self.sync_every_s):
            r = self.sync()
            if r.get("new") and self.notifier:
                self.notifier.send("Nouvelle sortie", r["message"], "/#etat", "sync")
                done.append("sync")
        try:                                                # v0.50 : récap du soir du dernier jour de la semaine
            if self.maybe_weekly_recap(now):
                done.append("recap")
        except Exception:                                   # noqa: BLE001 : la boucle continue
            log.exception("récap de la semaine")
        if not self.notifier or not self.notifier.enabled:
            return done
        today = self.today()
        sessions = [s for s in self._sessions(today, today) if s["intensity"] != "rest" or s["kind"] != "repos"]
        key_am, key_pm = f"push_am:{today}", f"push_pm:{today}"
        hour_am = int(self.store.get("push_hour_am", 7))
        if sessions and now.hour >= hour_am and not self.store.get(key_am):
            s = sessions[0]
            body = f"{s['title']} · {s['duration_min']} min" if s["duration_min"] else s["title"]
            self.notifier.send("Séance du jour", body, "/#aujourdhui", "am")
            self.store.set(key_am, True)
            done.append("am")
        pending = [s for s in sessions if not s["feedback"]]
        auto = [s for s in sessions if (s["feedback"] or {}).get("auto") and not s["feedback"].get("rpe")]
        if (pending or auto) and now.hour >= 21 and not self.store.get(key_pm):
            if pending:
                self.notifier.send("Comment s'est passée ta séance ?", "Valide-la en 10 secondes : statut, RPE, une note.",
                                   "/#aujourdhui", "pm")
            else:                                           # v0.42 : validée par la sortie, ressenti à compléter
                self.notifier.send("Séance validée automatiquement ✓", "Ajoute ton ressenti (RPE, une note) : "
                                   "le coach s'ajuste à tes retours.", "/#aujourdhui", "pm")
            self.store.set(key_pm, True)
            done.append("pm")
        return done
