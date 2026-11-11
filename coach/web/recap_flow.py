"""v0.50 — Récap de la semaine (mixin de CoachService).

`maybe_weekly_recap(now)` est appelée par `tick()` chaque minute : le soir du dernier jour de la semaine de l'athlète
(à partir de 20 h, heure locale), le coach poste UN message dans le chat (idempotent par semaine) et une notification
si elles sont activées. Jamais pendant l'accueil ni pendant une proposition de plan en attente. Le contenu est
déterministe (coach/weekly_recap.py) ; une phrase d'encouragement du LLM s'ajoute si le budget du jour le permet.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field

from ..weekly_recap import RecapInput, build, due_week, short_line

log = logging.getLogger("coach.web")
RECAPS_KEY = "weekly_recaps"            # {début de semaine ISO: {"at", "chat_id", "text", "sections", "data"}}
FEELINGS_KEY = "weekly_feelings"        # v0.51 : {début de semaine ISO: {fatigue, difficulty, fun, note, at}}
LLM_BUDGET_SHARE = 0.8                  # phrase du LLM seulement sous 80 % du budget du jour

RECAP_LINE_SYSTEM = """Tu es le coach de course à pied de l'athlète. Voici le récap chiffré de sa semaine (`recap`).
Écris UNE phrase d'encouragement (25 mots au plus), concrète, qui s'appuie sur un fait du récap. Pas de chiffre
inventé, pas de nouvelle consigne d'entraînement, pas d'emoji en excès."""


class RecapLine(BaseModel):
    sentence: str = Field(default="", max_length=300)


class RecapFlow:
    def recap_input(self, week0, now: datetime | None = None) -> RecapInput:
        from ..week_max import WeekMax, from_prefs, rates
        end = week0 + timedelta(days=6)
        plan = [s for s in self.store.plan(week0, end) if s.intensity != "rest"]
        nxt = [s for s in self.store.plan(end + timedelta(days=1), end + timedelta(days=7)) if s.intensity != "rest"]
        meta = self._meta()
        prefs, p = self.prefs(), self.profile()
        diag = self.store.get("diag_full")
        kmh, dph = rates(diag)
        c = self.career_view()
        days = getattr(self, "_career_xp_days", {}) or {}
        span = {(week0 + timedelta(days=i)).isoformat() for i in range(7)}
        trophies = [t["label"] for t in c.get("trophies", []) if t.get("day") in span]
        levels = [lv for lv in c.get("levels", []) if lv.get("day") in span]
        event = next((e for e in c.get("events") or [] if not e.get("done")), None)
        races = []
        if p is not None:
            races = [(p.goal_name, p.goal_date)] + [(e.name, e.date) for e in p.events if e.role == "B" and e.date]
            races = [(n, d) for n, d in races if d and d >= week0]
        return RecapInput(
            week0=week0, today=self.today(), plan=plan, next_plan=nxt,
            titles={sid: m.get("title") for sid, m in meta.items() if m.get("title")},
            feedback=self.store.feedback_for([s.session_id for s in plan]),
            acts=[a for a in self.store.activities(since=week0) if a.local_day <= end],
            last_sync=self.store.get("strava_last_sync"), now=now or datetime.now(),
            wm=from_prefs(prefs, diag), rates=WeekMax(kmh=kmh, dph=dph),
            xp_week=sum(v for d, v in days.items() if d in span), trophies=trophies, levels=levels, event=event,
            long_day=p.long_day if p is not None else None, races=races,
            first_name=self.store.get("athlete_first_name", ""))

    def _recap_sentence(self, r: dict) -> tuple[str, float]:
        """Phrase d'encouragement facultative (LLM, budget permis). ("", 0) hors ligne ou budget serré."""
        llm = self.pilot.deps.llm
        budget = self.pilot.settings.daily_budget_eur
        if type(llm).__name__ == "HeuristicLLM" or self._spent() >= budget * LLM_BUDGET_SHARE:
            return "", 0.0
        out, cost = self._llm("weekly_recap", RECAP_LINE_SYSTEM, {"recap": r["text"]}, RecapLine,
                              lambda: RecapLine())
        return (out.sentence or "").strip(), cost

    def recap_allowed(self) -> bool:
        """Jamais pendant l'accueil, ni pendant une proposition de plan qui attend sa réponse."""
        from .onboarding_flow import OB_STATES
        state = self.store.get("ob_state")
        return not self.needs_onboarding() and state not in OB_STATES and state != "replan"

    def maybe_weekly_recap(self, now: datetime | None = None) -> dict | None:
        """Poste le récap de la semaine si c'est le moment (idempotent). Renvoie le message posté, sinon None."""
        now = now or datetime.now()
        with self.lock:
            if not self.recap_allowed():
                return None
            due = due_week(self.today(), now.hour, self.week_start_day())
            if due is None:
                return None
            week0, _late = due
            done = self.store.get(RECAPS_KEY) or {}
            if week0.isoformat() in done:
                return None
            first = self.store.first_activity_day()
            if not self.store.plan(week0, week0 + timedelta(days=13)) and not (first and first <= week0 + timedelta(days=6)):
                return None                                  # rien à récapituler (compte tout neuf)
            r = build(self.recap_input(week0, now))
            sentence, cost = self._recap_sentence(r)
            text = r["text"] + (f"\n\n{sentence}" if sentence else "")
            msg = self.store.add_chat("coach", "reply", text, {"nodes": ["weekly_recap"], "recap": r["week_start"],
                                                               "cost_eur": round(cost, 4)})
            done[week0.isoformat()] = {"at": now.isoformat(timespec="minutes"), "chat_id": msg["id"], "text": text,
                                       "sections": r["sections"], "data": r["data"], "week_end": r["week_end"],
                                       "sentence": sentence}
            self.store.set(RECAPS_KEY, dict(sorted(done.items())[-12:]))
        if self.notifier and self.notifier.enabled:
            try:
                self.notifier.send("Récap de la semaine", short_line(r), "/#coach", "recap")
            except Exception:                                # noqa: BLE001 : le message est dans le chat
                log.exception("notification du récap")
        return msg

    # ------------------------------------------------------------ v0.51 : ressenti de fin de semaine
    def set_week_feeling(self, week_start: str, fatigue: int, difficulty: int, fun: int, note: str = "") -> dict:
        """Fatigue, difficulté, plaisir (1 à 5) et un mot : gardés par semaine, postés dans le chat ; le coach
        réagit en tâche de fond (ajustement léger proposé, avec confirmation, si la semaine a été trop dure)."""
        w0 = date.fromisoformat(week_start)
        for name, v in (("fatigue", fatigue), ("difficulté", difficulty), ("plaisir", fun)):
            if not 1 <= int(v) <= 5:
                raise ValueError(f"{name} entre 1 et 5")
        note = (note or "").strip()[:500]
        with self.lock:
            feel = dict(self.store.get(FEELINGS_KEY) or {})
            first = w0.isoformat() not in feel
            feel[w0.isoformat()] = {"fatigue": int(fatigue), "difficulty": int(difficulty), "fun": int(fun),
                                    "note": note, "at": datetime.now().isoformat(timespec="minutes")}
            self.store.set(FEELINGS_KEY, dict(sorted(feel.items())[-26:]))
        line = (f"📝 Ressenti de ma semaine du {w0:%d/%m} : fatigue {fatigue}/5, difficulté {difficulty}/5, "
                f"plaisir {fun}/5." + (f" {note}" if note else ""))
        hard = fatigue >= 4 or difficulty >= 5 or fun <= 2
        self._tell_coach([line] if first else [line.replace("📝 Ressenti", "📝 Ressenti corrigé")],
                         "[Ressenti de fin de semaine donné par l'athlète (1 = faible, 5 = élevé). Consigne : 2-3 "
                         "phrases, sans répéter les notes. " + (
                             "La semaine a été dure ou peu agréable : propose un ajustement léger de la semaine à "
                             "venir (avec la confirmation habituelle) seulement s'il est justifié, et demande ce qui "
                             "a pesé." if hard else "Encourage, et dis en une phrase ce qui compte la semaine "
                             "prochaine.") + "]",
                         "Merci pour ton ressenti, je le garde en tête pour la suite.", "feeling")
        return {"week_start": w0.isoformat(), **feel[w0.isoformat()]}

    def recap_summary(self) -> dict:
        done = self.store.get(RECAPS_KEY) or {}
        last = max(done) if done else None
        return {"last": last, "count": len(done)}

    def recap_view(self) -> dict:
        """Dernier récap posté, et l'aperçu de la semaine en cours (bouton « Récap de la semaine »)."""
        with self.lock:
            done = self.store.get(RECAPS_KEY) or {}
            last = None
            feel = self.store.get(FEELINGS_KEY) or {}
            if done:
                k = max(done)
                last = {"week_start": k, **done[k], "feeling": feel.get(k)}
            preview = None
            if not self.needs_onboarding():
                w0 = self.week_start(self.today())
                r = build(self.recap_input(w0))
                preview = {"week_start": r["week_start"], "week_end": r["week_end"], "text": r["text"],
                           "sections": r["sections"], "data": r["data"], "feeling": feel.get(r["week_start"])}
            return {"last": last, "preview": preview}


__all__ = ["FEELINGS_KEY", "RecapFlow", "RECAPS_KEY"]
