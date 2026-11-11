"""Parcours d'accueil et changements d'objectif (extrait de service.py en v0.31)."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from ..diagnosis import Diagnosis, diagnose
from ..onboarding import (
    GOAL_EDIT_SYSTEM,
    GOAL_SYSTEM,
    JOURS,
    MOIS_NOMS,
    PROFILE_SYSTEM,
    RECAP_SYSTEM,
    TURN_SYSTEM,
    Event,
    GoalEdit,
    GoalsExtract,
    OnboardingMessage,
    Prefs,
    Profile,
    ProfileExtract,
    TurnExtract,
    apply_profile_fields,
    apply_turn,
    check_block,
    commitments,
    events_message,
    history_brief,
    initial_block,
    looks_like_goal_edit,
    looks_like_prefs_edit,
    mentions_event,
    parse_date_fr,
    parse_goal,
    parse_goal_edit,
    parse_profile,
    parse_turn,
    profile_questions,
    proposal_message,
    recap_message,
    sanitize_turn,
    session_meta,
    week_meta,
)
from ..sandbox import cost_eur
from ..schemas import PlannedSession

log = logging.getLogger("coach.web")
PREFS_BEFORE_KEY = "prefs_before_replan"   # v0.52 : réglages d'avant une correction en attente

THREAD = "pilote-web"
OB_STATES = ("ask_goal", "confirm_goal", "ask_profile", "ask_volume", "negotiate")


class OnboardingFlow:
    """Accueil d'un nouvel athlète, discussion du plan, correction d'une échéance (mixin de CoachService)."""

    # Parcours : connexion Strava -> lecture de l'historique -> bilan dans le chat -> « ta prochaine
    # échéance ? » -> proposition -> discussion (chaque demande recalcule le plan, dans les bornes du
    # code) -> « ça me va » -> plan écrit. Le LLM extrait et rédige ; le plan est calculé par le code.
    @property
    def thread(self) -> str:
        return self.store.get("thread_id", THREAD)

    def profile(self) -> Profile | None:
        p = self.store.get("profile")
        return Profile.model_validate(p) if p else None

    def prefs(self) -> Prefs:
        return Prefs.model_validate(self.store.get("prefs") or {})

    def needs_onboarding(self) -> bool:
        return not self.store.get("onboarded") and not self.store.plan()

    def apply_profile(self) -> None:
        """L'objectif donné dans le chat remplace les réglages par défaut (nom, date, fuseau de la course)."""
        p = self.profile()
        if p is None:
            return
        st = self.pilot.settings
        st.race_name, st.race_start, st.race_tz = p.goal_name, f"{p.goal_date.isoformat()}T07:00:00", st.tz
        bits = [p.goal_kind] + ([f"{p.goal_distance_km:g} km"] if p.goal_distance_km else []) + \
               ([f"{p.goal_dplus_m} m D+"] if p.goal_dplus_m else [])
        st.goal = f"Objectif : {', '.join(bits)}. {p.days_per_week} jours d'entraînement par semaine."
        said = [c for c in commitments(p, self.prefs(), self.today())[1:]   # v0.17 : le coach garde ce qu'il a dit
                if not c.startswith("Volume maximum")]               # v0.49 : phrase dédiée dans le profil du coach
        if said:
            st.goal += (" Préférences et contraintes de l'athlète (à respecter ; ce ne sont PAS des séances "
                        "prévues, le plan réel est plan_next_7d) : " + " ; ".join(said) + ".")
        if self.pilot.google is not None:
            self.pilot.google.race_start, self.pilot.google.race_tz = datetime.fromisoformat(st.race_start), st.tz
        name = self.store.get("athlete_first_name", "")
        self.store.set("athlete_label", "Coureur" + (f" ({name})" if name else ""))

    def onboarding_view(self) -> dict:
        job = dict(self.job)
        if job.get("running"):                               # pas de verrou : la synchro le tient
            return {"step": "syncing", "strava": True, "strava_connected": True, "job": job,
                    "connect": self.connector.status if self.connector else None}
        with self.lock:
            acts = self.store.activities()
            state = self.store.get("ob_state")
            if not self.needs_onboarding():
                step = "done"
            elif state in OB_STATES:
                step = "chat"
            else:
                step = "connect"
            return {"step": step, "activities": len(acts),
                    "first_day": acts[0].local_day.isoformat() if acts else None,
                    "last_day": acts[-1].local_day.isoformat() if acts else None,
                    "strava": self.connector is not None or self.pilot.strava is not None,
                    "strava_connected": self.pilot.strava is not None, "job": job,
                    "connect": self.connector.status if self.connector else None,
                    "connect_error": self.connector.error if self.connector else None}

    # -- étape 1 : Strava
    def connect_strava(self, origin: str, force: bool = False) -> dict:
        if self.connector is None:
            return {"status": "error", "message": "Strava n'est pas configuré sur ce serveur : relance « bash install.sh » "
                                                  "et renseigne le Client ID et le Client Secret de ton application Strava."}
        if not force and self.pilot.strava is not None and self.connector.connected():
            if not self.store.get("athlete_first_name") and hasattr(self.pilot.strava, "athlete"):
                try:
                    self.store.set("athlete_first_name", str(self.pilot.strava.athlete().get("firstname") or "")[:40])
                except Exception:                            # noqa: BLE001
                    pass
            self.start_initial_sync()
            return {"status": "connected"}
        return self.connector.start(origin.rstrip("/") + "/strava/callback", self._strava_connected)

    def _strava_connected(self, profile: dict) -> None:
        self.pilot.strava = self.strava_factory()
        name = str(profile.get("firstname") or profile.get("first_name") or "")[:40]
        if name:
            self.store.set("athlete_first_name", name)
        if str(profile.get("sex") or "").upper()[:1] in ("M", "F"):     # v0.52 : accords du coach
            self.store.set("athlete_sex", str(profile["sex"]).upper()[:1])
        if self.needs_onboarding():
            self.start_initial_sync()

    def start_initial_sync(self) -> None:
        if self.job.get("running"):
            return
        self.job = {"running": True, "started": time.time(), "message": "Lecture de ton historique Strava…"}

        def run():
            try:
                r = self.sync(progress=lambda m: self.job.update(message=m))
                if not r.get("ok"):
                    self.job = {"running": False, "error": r.get("message")}
                    return
                self.job = {"running": True, "started": self.job["started"], "message": "Analyse de tes sorties…"}
                if self.needs_onboarding() and self.store.activities():
                    self.start_conversation()
                self.job = {"running": False, "done": True, "new": r.get("new")}
            except Exception as e:                           # noqa: BLE001
                log.exception("première synchro")
                self.job = {"running": False, "error": f"{type(e).__name__} : {str(e)[:200]}"}

        if self.background:
            threading.Thread(target=run, name="first-sync", daemon=True).start()
        else:
            run()

    # -- étape 2 : bilan + question de l'échéance
    def _llm(self, node: str, system: str, payload: dict, schema, fallback):
        llm = self.pilot.deps.llm
        n0 = len(llm.usage_log)
        try:
            out = llm.structured(node=node, tier="main", system=system + self.pilot.deps.persona(),
                                 user=json.dumps(payload, ensure_ascii=False, default=str), schema=schema)
        except Exception:
            log.exception("LLM %s", node)
            out = fallback()
        cost = cost_eur(llm.usage_log[n0:])
        self.store.set(f"spent:{self.today().isoformat()}", self._spent() + cost)
        return out, cost

    def start_conversation(self) -> dict:
        with self.lock:
            acts = self.store.activities()
            if not acts:
                raise ValueError("pas encore de données : connecte Strava ou importe des fichiers FIT")
            today, t0 = self.today(), time.time()
            d = diagnose(acts, today)
            self._store_diag(d)
            draft = recap_message(d, self.store.get("athlete_first_name", ""))
            out, cost = self._llm("onboard_recap", RECAP_SYSTEM, {"draft": draft},
                                  OnboardingMessage, lambda: OnboardingMessage(message=draft))
            self.store.set("ob_state", "ask_goal")
            self.store.set("ob_quick", [])
            msg = self.store.add_chat("coach", "ask", out.message, {
                "cost_eur": round(cost, 4), "latency_s": round(time.time() - t0, 2),
                "nodes": ["diagnosis", "onboard_recap"], "trace": f"bilan={d.focus} · {len(acts)} activités"})
            return {"messages": [msg], "pending": True, "quick": []}

    def _store_diag(self, d: Diagnosis) -> None:
        self.store.set("diag_full", d.model_dump(mode="json"))
        self.store.set("diagnosis", {"focus": d.focus, "vma_kmh": d.vma_kmh, "paces": d.paces,
                                     "findings": [f.model_dump() for f in d.findings], "at": d.today.isoformat()})
        self.store.set("diagnosis_brief", "Bilan de départ : " + "; ".join(f.title for f in d.findings) +
                       f". Focus : {d.focus}." + (f" Allures repères (estimées) : seuil {d.paces.get('seuil')}/km, "
                                                   f"VMA {d.paces.get('vma')}/km." if d.paces else "")
                       + (" " + history_brief(d) if history_brief(d) else ""))

    def startup_repairs(self) -> None:
        """v0.48, une seule fois par base : (1) bilan de départ recalculé si une sortie a une durée aberrante (montre
        oubliée : la règle de lecture `sanitize` s'applique aux sorties déjà importées) ; (2) date de l'objectif
        corrigée quand le catalogue vérifié connaît le format et que la date enregistrée est un autre jour du même
        événement (ex. 1er jour de la VVX au lieu du samedi du 21 km)."""
        if not self.store.get("repairs_v050_durations"):
            self.repair_plan_texts()
        self._fetch_athlete_sex()
        from .prefs_repair import repair_v052, repair_v0521
        repair_v052(self)                                        # v0.52 : une fois, préférences mal extraites
        repair_v0521(self)                                       # v0.52.1 : refus fait avant la v0.52
        from .fallback_repair import repair as repair_fallback
        repair_fallback(self)                                    # v0.50.2 : une fois, avant le réalignement des titres
        try:
            self.repair_session_meta()                           # v0.50.1 : idempotent, à chaque démarrage
        except Exception:                                        # noqa: BLE001 : jamais bloquant au démarrage
            log.exception("réparation des titres de séance")
        if self.store.get("repairs_v048"):
            return
        try:
            acts = self.store.activities()
            odd = [a for a in acts if a.suspect or a.elapsed_s]
            if odd and self.store.get("diag_full"):
                old = self._diag()
                self._store_diag(diagnose(acts, old.today))
                log.info("bilan recalculé : %d sortie(s) à durée aberrante (%s)", len(odd),
                         ", ".join(f"{a.local_day} {a.name}"[:60] for a in odd[:5]))
            p = self.profile()
            if p is not None:
                from ..race_lookup import catalog_event, from_catalog
                today = self.today()
                f = from_catalog(p.goal_name, p.goal_distance_km, today, p.goal_date.year)
                days = {r["date"] for r in catalog_event(p.goal_name, today, p.goal_date.year)}
                if f and not f.ambiguous and f.date != p.goal_date and p.goal_date.isoformat() in days:
                    log.info("date de l'objectif corrigée : %s -> %s (%s)", p.goal_date, f.date, f.name)
                    p.goal_date = f.date
                    p.goal_dplus_m = p.goal_dplus_m or f.dplus_m
                    self.store.set("profile", p.model_dump(mode="json"))
                    self.apply_profile()
                    self.store.add_chat("coach", "reply", f"J'ai corrigé la date de ton objectif : {p.goal_name} a lieu "
                                        f"le {JOURS[f.date.weekday()]} {f.date.day} {MOIS_NOMS[f.date.month - 1]} "
                                        f"{f.date.year}" + (f" (départ {f.start})" if f.start else "")
                                        + ", pas le premier jour de l'événement. Désolé pour l'erreur !", {})
            from .. import vocab
            # compte existant : vocabulaire figé (expert pour le pilote, simple pour une coureuse de club)
            if self.store.get(vocab.KEY) is None and (p is not None or self.store.get("onboarded")):
                self.store.set(vocab.KEY, vocab.infer(self.store))
        except Exception:                                        # noqa: BLE001 : jamais bloquant au démarrage
            log.exception("réparations v0.48")
        self.store.set("repairs_v048", True)

    def _fetch_athlete_sex(self) -> None:
        """v0.52 : le sexe du profil Strava (accords du coach), lu une fois en tâche de fond."""
        if self.store.get("athlete_sex") or not hasattr(self.pilot.strava, "athlete"):
            return

        def job():
            try:
                sex = str(self.pilot.strava.athlete().get("sex") or "").upper()[:1]
                if sex in ("M", "F"):
                    self.store.set("athlete_sex", sex)
            except Exception:                                # noqa: BLE001 : sans accord explicite, rien de grave
                log.info("profil Strava : sexe non lu")
        threading.Thread(target=job, daemon=True).start()

    def repair_plan_texts(self) -> int:
        """v0.50, une fois par base : les séances déjà enregistrées dont le texte ne correspond plus à la durée
        (« Seuil · 40 min » décrit en ~58 min) ont leur texte réécrit d'après la durée (moins de répétitions,
        échauffement plus court). Dates et durées inchangées ; l'agenda suit si possible."""
        from ..schemas import PlanChange
        from ..session_content import harmonize
        n = 0
        try:
            changes = []
            for s in self.store.plan():
                s2 = harmonize(s)
                if s2.description != s.description:
                    changes.append(PlanChange(action="modify", session=s2, reason="v0.50 : texte aligné sur la durée"))
            if changes:
                try:
                    self.pilot.deps.calendar.apply("pilote", changes)
                except Exception:                                # noqa: BLE001 : l'agenda n'empêche pas la réparation
                    log.exception("réparation des textes : agenda non mis à jour")
                    self.store.apply(changes)
                meta = self.store.get("session_meta", {})
                for c in changes:
                    if c.session.session_id in meta:
                        meta[c.session.session_id]["detail"] = c.session.description
                self.store.set("session_meta", meta)
                n = len(changes)
                log.info("%d séance(s) : texte aligné sur la durée", n)
        except Exception:                                        # noqa: BLE001 : jamais bloquant au démarrage
            log.exception("réparation des textes de séance")
        self.store.set("repairs_v050_durations", {"n": n})
        return n

    # -- étape 3 : la conversation
    def _history_for_llm(self, n: int = 6) -> list[dict]:
        return [{"role": m["role"], "text": m["text"][:1500]} for m in self.store.chat(n)]

    def _diag(self) -> Diagnosis:
        return Diagnosis.model_validate(self.store.get("diag_full"))

    def _onboarding_turn(self, text: str, user: dict) -> dict | None:
        state = self.store.get("ob_state")
        if state == "replan":                                   # v0.25 : nouveau plan proposé après une correction
            return self._replan_turn(text, user)
        if state not in OB_STATES or not self.needs_onboarding():
            return None
        if state in ("ask_goal", "confirm_goal"):
            return self._goal_turn(text, user, state)
        if state == "ask_profile":
            return self._profile_turn(text, user)
        if state == "ask_volume":
            return self._volume_turn(text, user)
        return self._negotiate_turn(text, user)

    # -- échéances : extraction, recherche, confirmation
    def _goal_turn(self, text: str, user: dict, state: str) -> dict:
        from ..race_lookup import lookup
        today, t0 = self.today(), time.time()
        cands = self.store.get("ob_events") if state == "confirm_goal" else None
        g, cost = self._llm("onboard_goal", GOAL_SYSTEM,
                            {"aujourd_hui": f"{JOURS[today.weekday()]} {today.isoformat()}", "message": text,
                             "candidats": cands, "historique": self._history_for_llm(2)},
                            GoalsExtract, lambda: parse_goal(text, today, cands))
        if g.confirmed and cands:                    # validation : on garde les échéances trouvées telles quelles
            events = [Event.model_validate(c) for c in cands]
        else:
            events = [Event.model_validate({**e.model_dump(), "kind": e.kind or "trail"}) for e in g.events]
        fmt = self.store.get("ob_formats") if state == "confirm_goal" else None
        if fmt:                                      # v0.48 : réponse à « lequel des formats ? »
            from ..race_lookup import from_catalog
            pick = from_catalog(f"{fmt['event']} {text}", None, today, fmt.get("year"))
            if pick and not pick.ambiguous:
                base = Event.model_validate(fmt["main"])
                base.name, base.date, base.source, base.start = pick.name, pick.date, pick.source, pick.start
                base.distance_km, base.dplus_m, base.kind = pick.distance_km, pick.dplus_m, pick.kind
                events, g.confirmed = [base] + [Event.model_validate(o) for o in fmt.get("others", [])], False
            self.store.set("ob_formats", None)
        if not (g.confirmed and cands) and not parse_date_fr(text, today, weekdays=False):
            for e in events:                         # v0.48 : une date que l'athlète n'a pas donnée est à vérifier
                if e.date and not e.source:
                    e.date = None
        if not events:
            msg = self.store.add_chat("coach", "ask", (g.reply.strip() + "\n\n" if g.reply else "")
                                      + "Quelle est ta course principale ? Son nom suffit, je cherche le reste.",
                                      {"cost_eur": round(cost, 4)})
            return {"messages": [user, msg], "pending": True, "quick": []}
        if g.days_per_week or g.long_day is not None:
            self.store.set("ob_layout", {"days_per_week": g.days_per_week, "long_day": g.long_day})
        main = next((e for e in events if e.role == "A"), events[0])
        main.role = "A"
        others = [e for e in events if e is not main]
        if g.confirmed and cands and main.date and main.date > today + timedelta(days=6):
            return self._ask_profile(user, main, others, g.reply or "Parfait, c'est noté.", cost, t0)
        # recherche des dates manquantes (objectif principal et courses secondaires)
        looked, formats = [], None
        for e in [main] + [o for o in others if o.role == "B"]:
            if e.date is None or (e.dplus_m is None and e.kind != "route"):
                f = lookup(e.name, e.distance_km, today, self.pilot.deps.llm, e.year)
                if f and f.ambiguous:                # v0.48 : plusieurs formats, jamais le 1er jour par défaut
                    if e is main and e.date is None:
                        formats = f
                    continue
                if f:
                    cost += f.cost_eur
                    e.name, e.date, e.source = f.name, e.date or f.date, f.source
                    e.start = e.start or f.start
                    e.distance_km = e.distance_km or f.distance_km
                    e.dplus_m = e.dplus_m or f.dplus_m
                    e.kind = f.kind or e.kind
                    looked.append(e.name)
        if formats is not None:
            from ..race_lookup import options_text
            self.store.set("ob_events", [x.model_dump(mode="json") for x in [main] + others])
            self.store.set("ob_formats", {"event": main.name, "year": main.year,
                                          "main": main.model_dump(mode="json"),
                                          "others": [o.model_dump(mode="json") for o in others]})
            self.store.set("ob_state", "confirm_goal")
            quick = [o.name.split(" — ")[-1] for o in formats.options][:6]
            self.store.set("ob_quick", quick)
            msg = self.store.add_chat("coach", "ask", (g.reply.strip() + "\n\n" if g.reply else "")
                                      + options_text(formats), {"cost_eur": round(cost, 4),
                                                                "nodes": ["onboard_goal", "race_lookup"]})
            return {"messages": [user, msg], "pending": True, "quick": quick}
        if main.date is None or main.date <= today + timedelta(days=6):
            self.store.set("ob_events", [x.model_dump(mode="json") for x in [main] + others])
            self.store.set("ob_state", "confirm_goal")
            ask = (g.reply.strip() + "\n\n" if g.reply else "") + \
                f"Je n'ai pas trouvé la date de {main.name}. Tu peux me la donner (et le D+ si tu l'as) ?"
            msg = self.store.add_chat("coach", "ask", ask, {"cost_eur": round(cost, 4)})
            return {"messages": [user, msg], "pending": True, "quick": []}
        self.store.set("ob_events", [x.model_dump(mode="json") for x in [main] + others])
        self.store.set("ob_state", "confirm_goal")
        intro = (g.reply.strip() + "\n\n") if g.reply else ""
        msg = self.store.add_chat("coach", "ask", intro + events_message(main, others), {
            "cost_eur": round(cost, 4), "latency_s": round(time.time() - t0, 2),
            "nodes": ["onboard_goal", "race_lookup"], "trace": "recherche : " + (", ".join(looked) or "aucune")})
        quick = ["Oui, c'est ça", "Non, je corrige"]
        self.store.set("ob_quick", quick)
        return {"messages": [user, msg], "pending": True, "quick": quick}

    # -- questions sur son quotidien (v0.15) : jours, repos, renfo, autres sports, Strava/cardio, vacances
    def _ask_profile(self, user: dict, main: Event, others: list[Event], reply: str, cost: float, t0: float) -> dict:
        self.store.set("ob_events", [x.model_dump(mode="json") for x in [main] + others])
        self.store.set("ob_state", "ask_profile")
        lay = self.store.get("ob_layout") or {}
        known = bool(lay.get("days_per_week")) and lay.get("long_day") is not None
        text = (reply.strip() + "\n\n" if reply else "") + profile_questions(self._diag(), layout_known=known)
        quick = ["Passe, valeurs par défaut"]
        self.store.set("ob_quick", quick)
        msg = self.store.add_chat("coach", "ask", text, {"cost_eur": round(cost, 4),
                                                          "latency_s": round(time.time() - t0, 2),
                                                          "nodes": ["onboard_goal", "profile_questions"]})
        return {"messages": [user, msg], "pending": True, "quick": quick}

    def _profile_turn(self, text: str, user: dict) -> dict:
        today, t0 = self.today(), time.time()
        x, cost = self._llm("onboard_profile", PROFILE_SYSTEM,
                            {"aujourd_hui": f"{JOURS[today.weekday()]} {today.isoformat()}", "message": text,
                             "historique": self._history_for_llm(2)},
                            ProfileExtract, lambda: parse_profile(text, today))
        if text.strip().lower().startswith("passe"):
            x = ProfileExtract(skip=True, reply="Pas de souci, je pars sur des valeurs par défaut.")
        evs = [Event.model_validate(e) for e in self.store.get("ob_events") or []]
        main, others = evs[0], evs[1:]
        if x.skip:                                  # « passe » : plafond estimé d'après l'historique (v0.49)
            return self._start_plan(user, main, others, x, cost, t0, infer_max=True)
        if x.max_week_hours or x.max_week_km or x.max_week_dplus:
            return self._start_plan(user, main, others, x, cost, t0)
        return self._ask_volume(user, x, cost, t0)

    # -- v0.49 : le volume maximum par semaine (temps, et s'il le dit km et D+)
    def _ask_volume(self, user: dict, x: ProfileExtract, cost: float, t0: float) -> dict:
        from ..week_max import QUICK, best_4w_avg_min, question
        self.store.set("ob_profile_x", x.model_dump(mode="json"))
        self.store.set("ob_state", "ask_volume")
        self.store.set("ob_quick", QUICK)
        best = best_4w_avg_min(self.store.activities(since=self.today() - timedelta(weeks=27)), self.today())
        text = ((x.reply or "").strip() + "\n\n" if x.reply else "") + question(best)
        msg = self.store.add_chat("coach", "ask", text, {"cost_eur": round(cost, 4),
                                                          "latency_s": round(time.time() - t0, 2),
                                                          "nodes": ["onboard_profile", "week_max_question"]})
        return {"messages": [user, msg], "pending": True, "quick": QUICK}

    def _volume_turn(self, text: str, user: dict) -> dict:
        from ..week_max import WEEK_MAX_SYSTEM, WeekMaxExtract, parse_week_max
        t0 = time.time()
        w, cost = self._llm("onboard_week_max", WEEK_MAX_SYSTEM, {"message": text,
                                                                  "historique": self._history_for_llm(2)},
                            WeekMaxExtract, lambda: parse_week_max(text))
        x = ProfileExtract.model_validate(self.store.get("ob_profile_x") or {})
        self.store.set("ob_profile_x", None)
        x.max_week_hours, x.max_week_km, x.max_week_dplus = w.hours, w.km, w.dplus_m
        x.reply = (w.reply or "").strip()
        evs = [Event.model_validate(e) for e in self.store.get("ob_events") or []]
        main, others = evs[0], evs[1:]
        given = bool(w.hours or w.km or w.dplus_m)
        # « je ne sais pas » (ou réponse incomprise) : estimé d'après l'historique ; « pas de limite » : aucun
        return self._start_plan(user, main, others, x, cost, t0, infer_max=not given and not w.no_limit)

    def _start_plan(self, user: dict, main: Event, others: list[Event], x: ProfileExtract, cost: float,
                    t0: float, infer_max: bool = False) -> dict:
        lay = self.store.get("ob_layout") or {}
        p = Profile(first_name=self.store.get("athlete_first_name", ""), goal_name=main.name[:120],
                    goal_date=main.date, goal_kind=main.kind or "trail", goal_distance_km=main.distance_km,
                    goal_dplus_m=main.dplus_m, days_per_week=max(2, min(7, lay.get("days_per_week") or 5)),
                    long_day=lay.get("long_day") if lay.get("long_day") is not None else 6, events=others)
        p, prefs, taken = apply_profile_fields(x, p, Prefs(), self.today(), defaults=True)
        if x.skip or not taken:
            taken = taken or [f"{p.days_per_week} jours de course par semaine, sortie longue le {JOURS[p.long_day]} "
                              "(par défaut)"]
        if infer_max:
            from ..week_max import h_txt, infer
            today = self.today()
            mins, best = infer(self.store.activities(since=today - timedelta(weeks=27)), today,
                               self._diag().typical_week_min)
            prefs.max_week_min, prefs.max_week_inferred = mins, True
            taken.append(f"volume maximum estimé à {h_txt(mins)} par semaine"
                         + (f" (1,3 × ta meilleure période récente : {h_txt(best)} par semaine sur 4 semaines)"
                            if best >= 30 else " (je n'ai pas assez d'historique : valeur prudente)")
                         + " : dis-moi si tu peux plus ou moins, ou change-le dans Carrière › Réglages")
        self.store.set("profile", p.model_dump(mode="json"))
        self.store.set("prefs", prefs.model_dump(mode="json"))
        self.apply_profile()
        self.store.set("ob_state", "negotiate")
        return self._propose(user, (x.reply or "").strip(), taken, first=True, cost=cost, t0=t0)

    # -- discussion du plan
    def _negotiate_turn(self, text: str, user: dict) -> dict:
        from ..knowledge import cards, tags_for
        today, t0 = self.today(), time.time()
        p, prefs = self.profile(), self.prefs()
        pend = self.store.get("pending_onboarding_plan") or {}
        fiches = cards(tags_for(text) | {"speed"} | ({"recovery_ultra"} if self._diag().recent_big_effort else set()))
        t, cost = self._llm("onboard_turn", TURN_SYSTEM,
                            {"aujourd_hui": f"{JOURS[today.weekday()]} {today.isoformat()}",
                             "plan_propose": pend.get("text", ""), "preferences": prefs.model_dump(mode="json"),
                             "objectif": p.model_dump(mode="json"), "bilan": self.store.get("diagnosis_brief", ""),
                             "fiches": fiches, "historique": self._history_for_llm(4), "message": text},
                            TurnExtract, lambda: parse_turn(text, today))
        if t.intent == "accept":
            return self._adopt(user)
        p2, prefs2, changed = apply_turn(t, p, prefs, today)
        if not changed:
            follow = {"reject": "D'accord. Qu'est-ce que tu voudrais changer ?",
                      "question": "Tu veux que je change quelque chose, ou le plan te va ?"}.get(
                t.intent, "Dis-moi ce que tu veux changer, ou réponds « ça me va » pour valider.")
            reply = ((t.reply or "").strip() + "\n\n" + follow).strip() if t.intent != "reject" else follow
            msg = self.store.add_chat("coach", "ask", reply, {"cost_eur": round(cost, 4),
                                                              "latency_s": round(time.time() - t0, 2),
                                                              "nodes": ["onboard_turn"],
                                                              "trace": f"intent={t.intent} · fiches : "
                                                                       + ", ".join(f["id"] for f in fiches)})
            return {"messages": [user, msg], "pending": True, "quick": self.store.get("ob_quick", [])}
        self.store.set("profile", p2.model_dump(mode="json"))
        self.store.set("prefs", prefs2.model_dump(mode="json"))
        self.apply_profile()
        return self._propose(user, (t.reply or "").strip(), changed, first=False, cost=cost, t0=t0,
                             fiches=[f["id"] for f in fiches])

    def _propose(self, user: dict, intro: str, changed: list[str], first: bool, cost: float, t0: float,
                 fiches: list[str] | None = None) -> dict:
        today = self.today()
        start = today + timedelta(days=1)
        d, p, prefs = self._diag(), self.profile(), self.prefs()
        first_day = self.store.first_activity_day()
        hist = self.store.daily_loads(first_day, today - timedelta(days=1)) if first_day and first_day < today else []
        done = self._done_this_week(start)
        block = initial_block(d, p, start, prefs, done_week=done)
        warnings = check_block(hist, block, start)
        tries = 0
        while warnings and tries < 3:                    # garde-fou : on baisse le volume plutôt que de forcer
            prefs.volume_factor = round(prefs.volume_factor * 0.9, 3)
            prefs.weekly_hours = prefs.weekly_hours * 0.9 if prefs.weekly_hours else None
            block = initial_block(d, p, start, prefs, done_week=done)
            warnings = check_block(hist, block, start)
            tries += 1
        if tries:
            self.store.set("prefs", prefs.model_dump(mode="json"))
            block.notes.append("J'ai réduit le volume pour que la charge augmente sans à-coup (contrôle ACWR).")
        plan_text = proposal_message(d, p, block, warnings, first=first, today=today, taken=changed, prefs=prefs)
        parts = [x for x in [intro, plan_text] if x]
        text = "\n\n".join(parts)
        self.store.set("pending_onboarding_plan", {
            "sessions": [x.model_dump(mode="json") for x in block.sessions],
            "session_meta": session_meta(block), "weeks": week_meta(block), "text": plan_text,
            "from": start.isoformat()})
        quick = ["Ça me va", "Je veux reprendre plus tôt", "Moins de volume"]
        self.store.set("ob_quick", quick)
        msg = self.store.add_chat("coach", "ask", text, {
            "cost_eur": round(cost, 4), "latency_s": round(time.time() - t0, 2),
            "nodes": ["onboard_goal" if first else "onboard_turn", "block", "guard"],
            "trace": f"{len(block.sessions)} séances · intensité le {block.intensity_from:%d/%m} · garde-fou "
                     + ("✅" if not warnings else "⚠ " + "; ".join(warnings))
                     + (f" · pris en compte : {', '.join(changed)}" if changed else "")
                     + (f" · fiches : {', '.join(fiches)}" if fiches else "")})
        return {"messages": [user, msg], "pending": True, "quick": quick}

    def _done_this_week(self, start: date) -> tuple[float, float, float]:
        """v0.49 : minutes, km et D+ déjà courus la semaine de `start` (avant `start`) : comptés dans le plafond."""
        from ..diagnosis import is_run
        monday = start - timedelta(days=(start.weekday() - self.prefs().week_start) % 7)   # v0.50 : sa semaine
        runs = [a for a in self.store.activities(since=monday) if a.local_day < start and is_run(a)]
        return (sum(a.duration_s for a in runs) / 60, sum(a.distance_m for a in runs) / 1000,
                float(sum(a.dplus_m or 0 for a in runs)))

    # -- corrections d'échéances après l'accueil (v0.25)
    def _events(self) -> tuple[Profile | None, list[Event]]:
        p = self.profile()
        if not p:
            return None, []
        main = Event(name=p.goal_name, date=p.goal_date, role="A", kind=p.goal_kind or "trail",
                     distance_km=p.goal_distance_km, dplus_m=p.goal_dplus_m)
        return p, [main] + list(p.events or [])

    def _goal_edit_turn(self, text: str, user: dict) -> dict | None:
        from ..race_lookup import lookup
        p, events = self._events()
        today = self.today()
        if not p or not looks_like_goal_edit(text, events, today):
            return None
        t0 = time.time()
        g, cost = self._llm("goal_edit", GOAL_EDIT_SYSTEM,
                            {"aujourd_hui": f"{JOURS[today.weekday()]} {today.isoformat()}", "message": text,
                             "echeances": [e.model_dump(mode="json") for e in events],
                             "historique": self._history_for_llm(2)},
                            GoalEdit, lambda: parse_goal_edit(text, events, today))
        if not g.edits:
            return None                                          # pas une correction : coaching habituel
        notes, unknown = [], []
        for ed in g.edits:
            tgt = next((e for e in events if e.name == ed.target), None) or mentions_event(ed.target or ed.name, events)
            if ed.action == "remove":
                if tgt is not None and tgt is not events[0]:
                    events.remove(tgt); notes.append(f"{tgt.name} retiré")
                elif tgt is events[0]:
                    notes.append(f"{tgt.name} est ton objectif principal : dis-moi lequel le remplace")
                continue
            if ed.action == "add" or tgt is None:
                tgt = Event(name=(ed.name or ed.target or "Course")[:120], role=ed.role or "B", kind=ed.kind or "trail")
                events.append(tgt); notes.append(f"{tgt.name} ajouté")
            else:
                notes.append(f"{tgt.name} corrigé")
            for k in ("distance_km", "dplus_m", "kind", "note"):
                if getattr(ed, k):
                    setattr(tgt, k, getattr(ed, k))
            if ed.role and tgt is not events[0]:
                tgt.role = ed.role
            if ed.date:
                tgt.date, tgt.year, tgt.source = ed.date, ed.date.year, "toi"
            elif ed.year:
                tgt.year = ed.year
                f = lookup(tgt.name, tgt.distance_km, today, self.pilot.deps.llm, ed.year)
                if f and not f.ambiguous:            # v0.48 : jamais le 1er jour d'un événement par défaut
                    cost += f.cost_eur
                    tgt.date, tgt.source, tgt.start = f.date, f.source, f.start
                    tgt.dplus_m = tgt.dplus_m or f.dplus_m
                elif tgt.date and tgt.date.year != ed.year:
                    tgt.date = None
                    unknown.append(tgt.name)
            elif ed.distance_km and ed.action == "update":   # v0.48 : « c'est le 21 km » : le jour du format
                f = lookup(f"{tgt.name} {ed.distance_km:g} km", ed.distance_km, today, self.pilot.deps.llm, tgt.year)
                if f and not f.ambiguous:
                    cost += f.cost_eur
                    tgt.name, tgt.date, tgt.source, tgt.start = f.name, f.date, f.source, f.start
                    tgt.dplus_m = f.dplus_m or tgt.dplus_m
            if tgt.date and tgt.date <= today:
                tgt.date = None
                unknown.append(tgt.name)
        main, others = events[0], events[1:]
        p.goal_date, p.goal_distance_km, p.goal_dplus_m = main.date or p.goal_date, main.distance_km, main.dplus_m
        p.goal_name = main.name[:120] or p.goal_name
        p.events = others
        self.store.set("profile", p.model_dump(mode="json"))
        self.apply_profile()
        lines = [g.reply.strip() or "C'est noté."]
        for e in events:
            when = f"{JOURS[e.date.weekday()]} {e.date:%d/%m/%Y}" if e.date else (f"{e.year}, date à préciser"
                                                                                   if e.year else "date à préciser")
            lines.append(f"• {'🎯 ' if e is main else ''}{e.name} : {when}"
                         + (f" (source : {e.source})" if e.source and e.source != "toi" and e.date else ""))
        if unknown:
            lines.append("Je n'ai pas trouvé la date exacte de " + ", ".join(unknown)
                         + " : donne-la-moi quand elle sera connue.")
        intro = "\n".join(lines)
        if not self.store.plan(since=today):
            msg = self.store.add_chat("coach", "reply", intro, {"cost_eur": round(cost, 4), "nodes": ["goal_edit"]})
            return {"messages": [user, msg], "pending": False}
        self.store.set("ob_state", "replan")
        out = self._propose(user, intro, notes, first=False, cost=cost, t0=t0)
        quick = ["Ça me va", "Garder l'ancien plan"]
        self.store.set("ob_quick", quick)
        out["quick"] = quick
        return out

    def _prefs_edit_turn(self, text: str, user: dict) -> dict | None:
        """v0.48 : après l'accueil, « garde l'intensité le mardi, la longue le vendredi, le bonus le week-end » met à
        jour les préférences enregistrées et propose le plan recalculé (comme une correction d'échéance)."""
        p = self.profile()
        if not p or self.store.get("ob_state") not in (None, "done") or not looks_like_prefs_edit(text):
            return None
        today, t0 = self.today(), time.time()
        prefs = self.prefs()
        t, cost = self._llm("prefs_edit", TURN_SYSTEM,
                            {"aujourd_hui": f"{JOURS[today.weekday()]} {today.isoformat()}",
                             "plan_propose": "", "preferences": prefs.model_dump(mode="json"),
                             "objectif": p.model_dump(mode="json"), "bilan": self.store.get("diagnosis_brief", ""),
                             "fiches": [], "historique": self._history_for_llm(4), "message": text},
                            TurnExtract, lambda: parse_turn(text, today))
        p2, prefs2, changed = apply_turn(sanitize_turn(t, text, prefs), p, prefs.model_copy(deep=True), today)
        if not changed:
            return None
        # v0.52 : réglages d'avant gardés : « Garder l'ancien plan » les remet (une extraction ratée ne reste pas)
        self.store.set(PREFS_BEFORE_KEY, {"prefs": prefs.model_dump(mode="json"), "profile": p.model_dump(mode="json")})
        self.store.set("profile", p2.model_dump(mode="json"))
        self.store.set("prefs", prefs2.model_dump(mode="json"))
        self.apply_profile()
        if not self.store.plan(since=today):
            msg = self.store.add_chat("coach", "reply", (t.reply or "C'est noté.").strip() + "\n"
                                      + "\n".join(f"✓ {c}" for c in changed),
                                      {"cost_eur": round(cost, 4), "nodes": ["prefs_edit"]})
            return {"messages": [user, msg], "pending": False}
        self.store.set("ob_state", "replan")
        out = self._propose(user, (t.reply or "").strip(), changed, first=False, cost=cost, t0=t0)
        quick = ["Ça me va", "Garder l'ancien plan"]
        self.store.set("ob_quick", quick)
        out["quick"] = quick
        return out

    # -- v0.49 : volume maximum dans Carrière › Réglages
    def week_max_view(self) -> dict:
        from ..week_max import best_4w_avg_min, describe
        with self.lock:
            prefs, today = self.prefs(), self.today()
            best = best_4w_avg_min(self.store.activities(since=today - timedelta(weeks=27)), today)
            return {"minutes": prefs.max_week_min, "km": prefs.max_week_km, "dplus": prefs.max_week_dplus,
                    "inferred": prefs.max_week_inferred,
                    "text": describe(prefs.max_week_min, prefs.max_week_km, prefs.max_week_dplus),
                    "best_4w_min": round(best) if best else None, "has_profile": self.profile() is not None}

    def set_week_max(self, minutes: int | None, km: float | None, dplus: int | None) -> dict:
        """Enregistre le plafond (None partout = pas de plafond). Si le plan à venir le dépasse, le coach propose
        dans le chat le plan recalculé (comme une correction de préférences)."""
        from ..week_max import WeekMaxExtract, clamp, describe, fit_plan, from_prefs
        with self.turn_lock, self.lock:
            p, prefs, today = self.profile(), self.prefs(), self.today()
            mn, k, dp = clamp(WeekMaxExtract(hours=minutes / 60 if minutes else None, km=km, dplus_m=dplus))
            prefs.max_week_min, prefs.max_week_km, prefs.max_week_dplus = mn, k, dp
            prefs.max_week_inferred = False
            if prefs.weekly_hours and mn and prefs.weekly_hours * 60 > mn:
                prefs.weekly_hours = mn / 60
            self.store.set("prefs", prefs.model_dump(mode="json"))
            self.store.set("week_max_hint_done", True)
            if p:
                self.apply_profile()
            txt = describe(mn, k, dp)
            out = {"ok": True, "text": txt, "replan": False, "messages": []}
            wm = from_prefs(prefs, self.store.get("diag_full"))
            upcoming = [s for s in self.store.plan(since=today + timedelta(days=1)) if not s.is_race]
            over = bool(wm and upcoming and fit_plan(upcoming, wm, today + timedelta(days=1),
                                                     self._done_this_week(today + timedelta(days=1)))[1])
            if not (p and over and self.store.get("diag_full") and self.store.get("ob_state") in (None, "done")):
                note = (f"C'est noté : ton volume maximum est de {txt}. Je n'irai jamais au-delà." if txt
                        else "C'est noté : pas de volume maximum, je reste guidé par ton historique.")
                if p:
                    out["messages"] = [self.store.add_chat("coach", "reply", note, {"nodes": ["week_max"]})]
                return out
            self.store.set("ob_state", "replan")
            r = self._propose(None, f"C'est noté : ton volume maximum est de {txt}. Ton plan actuel va au-delà "
                              "certaines semaines, voici le plan recalculé.", [f"volume maximum : {txt}"],
                              first=False, cost=0.0, t0=time.time())
            quick = ["Ça me va", "Garder l'ancien plan"]
            self.store.set("ob_quick", quick)
            return {**out, "replan": True, "messages": [m for m in r["messages"] if m], "quick": quick}

    def _replan_turn(self, text: str, user: dict) -> dict:
        if re.search(r"garder|ancien plan|annul|non merci", text.lower()):
            self.store.set("ob_state", "done"); self.store.set("ob_quick", [])
            self.store.set("pending_onboarding_plan", None)
            before = self.store.get(PREFS_BEFORE_KEY)
            if before:                                       # v0.52 : réglages d'avant la correction remis
                self.store.set("prefs", before["prefs"]); self.store.set("profile", before["profile"])
                self.store.set(PREFS_BEFORE_KEY, None)
                self.apply_profile()
                txt = "D'accord, je garde le plan actuel et tes réglages d'avant."
            else:
                txt = ("D'accord, je garde le plan actuel (tes corrections sont bien enregistrées et serviront pour "
                       "le prochain bloc).")
            msg = self.store.add_chat("coach", "reply", txt)
            return {"messages": [user, msg], "pending": False, "quick": []}
        return self._negotiate_turn(text, user)

    def _adopt(self, user: dict) -> dict:
        pend = self.store.get("pending_onboarding_plan") or {}
        today = self.today()
        sessions = [PlannedSession.model_validate(x) for x in pend.get("sessions", [])]
        sessions = [x for x in sessions if x.day > today or (x.day == today and not self.store.has_activity_on(today))]
        if not sessions:
            msg = self.store.add_chat("coach", "ask", "Je n'ai pas de plan en attente : dis-moi ta prochaine échéance.")
            self.store.set("ob_state", "ask_goal")
            return {"messages": [user, msg], "pending": True, "quick": []}
        frm = pend.get("from")
        ops = self.pilot.write_plan(sessions, max(today + timedelta(days=1), date.fromisoformat(frm)) if frm else None)
        self.store.set("session_meta", {**self._meta(), **pend["session_meta"]})
        self.store.set("weeks", {**self.store.get("weeks", {}), **pend["weeks"]})
        self.store.set("pending_onboarding_plan", None)
        self.store.set(PREFS_BEFORE_KEY, None)
        p, prefs = self.profile(), self.prefs()
        agreed = {"adopted_at": datetime.now().isoformat(timespec="minutes"), "goal": p.goal_name if p else "",
                  "prefs": prefs.model_dump(mode="json"),              # v0.52 : réglages du plan validé
                  "commitments": commitments(p, prefs, today) if p else [], "plan_text": pend.get("text", ""),
                  "sessions": len(sessions), "first_day": sessions[0].day.isoformat(),
                  "last_day": sessions[-1].day.isoformat()}
        self.store.set("agreed_plan", agreed)                       # v0.17 : le plan validé est gardé
        self.store.set("agreed_history", (self.store.get("agreed_history") or [])[-9:] + [agreed])
        self.store.set("ob_state", "done")
        self.store.set("ob_quick", [])
        self.store.set("onboarded", True)
        nxt = sessions[0]
        text = (f"C'est parti ✅ {len(sessions)} séances jusqu'au {sessions[-1].day:%d/%m}"
                + (", aussi dans ton agenda" if self.pilot.google else "") + ". "
                + f"Première séance : {pend['session_meta'][nxt.session_id]['title'].lower()}, {nxt.duration_min} min, "
                  f"{JOURS[nxt.day.weekday()]} {nxt.day:%d/%m}.\n\n"
                + "Après chaque séance, valide-la dans « Aujourd'hui » (comment ça s'est passé, effort ressenti, "
                  "une note) : je m'ajuste à tes retours. Et tu peux m'écrire ici à tout moment."
                + ("\n\n🎁 Cadeau de bienvenue : ton premier « Œil du coach » est offert. Toute l'équipe (prévention, "
                   "charge, nutrition, stratégie de course) analyse ton entraînement et t'en tire 3 priorités : "
                   "lance-le dans Progrès." if not self.store.get("eye_welcome_used") and not self.store.get("eye_report") else ""))
        msg = self.store.add_chat("coach", "reply", text, {"calendar_ops": len(ops)})
        return {"messages": [user, msg], "pending": False, "calendar_changed": True, "quick": []}

    def reset(self) -> None:
        """Repart de zéro (test) : efface sorties, plan, discussion et réglages ; garde la connexion Strava."""
        import uuid

        from ..backup import snapshot
        with self.lock:
            try:                                  # v0.17 : rien n'est perdu, même en recommençant à zéro
                home = Path(self.store.path).parent
                self.store.db.commit()
                snapshot(home, home / "backups")
            except Exception:                     # noqa: BLE001
                log.exception("sauvegarde avant remise à zéro")
            db = self.store.db
            for t in ("activities", "plan", "feedback", "chat"):
                db.execute(f"DELETE FROM {t}")
            db.execute("DELETE FROM kv")
            db.commit()
            self.store.set("thread_id", f"web-{uuid.uuid4().hex[:8]}")
            self.job = {}
