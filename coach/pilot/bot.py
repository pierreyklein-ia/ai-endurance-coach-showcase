"""Bot Telegram du pilote : tes vraies données, ton agenda Google, Claude.

Différences avec le bot de test :
- liste blanche OBLIGATOIRE (un seul chat : toi) ;
- /plan génère l'affûtage à partir de ta charge réelle, écrit après ton « oui » ;
- /sync lit tes nouvelles sorties Strava (connecteur officiel, usage personnel) ;
  synchro automatique toutes les 2 h ;
- un fichier FIT/GPX envoyé au bot est importé (app Suunto -> Partager) ;
- plafond de dépense Claude par jour (FinOps).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date

from ..adapters.telegram_bot import CoachBot, esc
from ..app import handle_inbound
from ..classifier import rule_consent
from ..sandbox import cost_eur, format_trace
from ..schemas import Channel, InboundMessage, PlannedSession
from .ingest import activity_from_bytes
from .runtime import Pilot, activity_line

log = logging.getLogger("coach.pilot")
ACCEPTED = (".fit", ".fit.gz", ".gpx", ".gpx.gz")

WELCOME = ("Salut 👋 Mode pilote : tes vraies sorties, ton agenda Google.\n"
           "Écris-moi comme à un coach (ressenti, fatigue, contraintes). Chaque changement "
           "important de plan attend ton « oui ».\n"
           "Commandes : /plan (affûtage Diag) · /agenda · /etat · /sync (Strava) · /debug\n"
           "Tu peux aussi m'envoyer le fichier FIT d'une sortie.")


class PilotBot(CoachBot):
    def __init__(self, api, allowed: set[int], pilot: Pilot, estimated: bool = True,
                 sync_every_min: int = 120, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        super().__init__(api, allowed, graph=pilot.graph, deps=pilot.deps, sleep=sleep)
        self.clock = clock                                # horloge de la synchro périodique (injectable : tests)
        self.pilot, self.estimated = pilot, estimated
        self.spent: dict[date, float] = {}
        self.sync_every_s, self.last_sync = sync_every_min * 60, 0.0

    def _aid(self, chat_id: int) -> str:
        return f"pilote-{self.version.get(chat_id, 0)}"        # /reset = nouvelle conversation, même plan

    def _agenda(self, chat_id: int) -> str:
        return "<pre>" + esc(self.pilot.agenda_text()) + "</pre>"

    # ---------------------------------------------------------------- commandes
    def _sync(self, chat_id: int | None, quiet: bool = False) -> str | None:
        from .strava_mcp import sync_strava
        if self.pilot.strava is None:
            return None if quiet else "Strava n'est pas connecté (lance `python -m coach.pilot strava` sur ton Mac)."
        try:
            res = sync_strava(self.pilot.store, self.pilot.settings, self.pilot.strava, today=self.deps.today())
        except Exception as e:
            while getattr(e, "exceptions", None):          # ExceptionGroup (anyio)
                e = e.exceptions[0]
            log.warning("synchro Strava : %s", e)
            return None if quiet else f"⚠ Synchro Strava impossible : {esc(str(e))[:300]}"
        finally:
            self.last_sync = self.clock()
        n = res.report.new + res.report.upgraded
        if quiet and n == 0:
            return None
        txt = f"🔄 Strava : {n} nouvelle(s) sortie(s)."
        news = self.pilot.store.activities()[-n:] if n else []
        txt += "".join("\n  " + esc(activity_line(a)) for a in news[-5:])
        if res.partial:
            txt += f"\n(partiel : {esc(res.partial)[:200]} — je reprendrai à la prochaine synchro)"
        return txt

    def _document(self, chat_id: int, doc: dict) -> str:
        name = doc.get("file_name") or "sortie.fit"
        if not name.lower().endswith(ACCEPTED):
            return "J'attends un fichier FIT (ou GPX horodaté). App Suunto : séance → ⋯ → télécharger le FIT → Partager."
        if doc.get("file_size", 0) > 20 * 1024 * 1024:
            return "Fichier trop gros pour un bot Telegram (20 Mo max)."
        try:
            a = activity_from_bytes(self.api.download(doc["file_id"]), name, self.pilot.settings)
        except Exception as e:
            return f"Je n'ai pas pu lire ce fichier : {esc(str(e))[:200]}"
        status = self.pilot.store.add_activity(a)
        head = {"new": "✅ Sortie importée", "upgraded": "✅ Sortie mise à jour (FC du fichier)",
                "duplicate": "ℹ Déjà connue"}[status]
        state = next((l for l in self.pilot.status_text().splitlines() if l.startswith("Forme")), "")
        return f"{head} :\n{esc(activity_line(a))}" + (f"\n\n{esc(state)}" if state else "")

    def _plan(self, chat_id: int) -> str:
        try:
            plan, verdict, ti = self.pilot.repo.propose_taper()
        except LookupError as e:
            return f"Je ne peux pas encore construire ton affûtage : {esc(str(e))}."
        if not verdict.allowed:
            return "⛔ Le plan généré ne passe pas les garde-fous :\n" + esc(self.pilot.propose_text(plan, verdict, ti))
        self.pilot.store.set("pending_plan", [x.model_dump(mode="json") for x in plan])
        return ("<pre>" + esc(self.pilot.propose_text(plan, verdict, ti)) + "</pre>\n"
                "Je l'écris dans ton Google Agenda ? (oui / non)")

    def _confirm_plan(self, chat_id: int, text: str) -> str:
        c = rule_consent(text)
        if c == "unclear":
            return "Réponds « oui » pour écrire ce plan dans ton agenda, ou « non » pour l'abandonner."
        plan = [PlannedSession.model_validate(x) for x in self.pilot.store.get("pending_plan")]
        self.pilot.store.set("pending_plan", None)
        if c == "no":
            return "OK, je n'écris rien. Dis-moi ce que tu veux changer, ou relance /plan."
        try:
            ops = self.pilot.write_plan(plan)
        except Exception as e:
            log.exception("écriture agenda")
            return f"⚠ Écriture dans Google Agenda impossible : {esc(str(e))[:300]}"
        where = "ton Google Agenda" if self.pilot.google else "le plan local (Google Agenda non connecté)"
        return f"C'est dans {where} ✅ ({len(ops)} événement(s)).\n\n" + self._agenda(chat_id)

    # ---------------------------------------------------------------- dispatch
    def handle_update(self, upd: dict) -> None:
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            return
        chat_id = msg["chat"]["id"]
        if chat_id not in self.allowed:
            return super().handle_update(upd)               # répond le chat_id, ne coache pas
        text = (msg.get("text") or "").strip()
        if "document" in msg:
            return self.api.send(chat_id, self._document(chat_id, msg["document"]))
        if text == "/start":
            return self.api.send(chat_id, esc(WELCOME) + "\n\n" + self._agenda(chat_id))
        if text == "/plan":
            return self.api.send(chat_id, self._plan(chat_id))
        if text == "/etat":
            return self.api.send(chat_id, "<pre>" + esc(self.pilot.status_text()) + "</pre>")
        if text == "/sync":
            return self.api.send(chat_id, self._sync(chat_id) or "Rien de neuf.")
        if text and self.pilot.store.get("pending_plan") and not text.startswith("/"):
            return self.api.send(chat_id, self._confirm_plan(chat_id, text))
        if text and not text.startswith("/"):
            today = self.deps.today()
            if self.spent.get(today, 0.0) >= self.pilot.settings.daily_budget_eur:
                return self.api.send(chat_id, f"Budget Claude du jour atteint "
                                              f"({self.pilot.settings.daily_budget_eur:.2f} €). "
                                              "Je reprends demain, ou augmente COACH_DAILY_BUDGET_EUR.")
            n0, trace = len(self.deps.llm.usage_log), []
            out = handle_inbound(self.graph, InboundMessage(athlete_id=self._aid(chat_id),
                                                            channel=Channel.TELEGRAM, text=text), trace)
            used = self.deps.llm.usage_log[n0:]
            self.spent[today] = self.spent.get(today, 0.0) + cost_eur(used)
            reply = esc(out.get("ask") or out.get("reply") or "…")
            if out.get("calendar_ops"):
                reply += "\n\n📅 Agenda mis à jour :\n" + self._agenda(chat_id)
            if chat_id in self.debug:
                reply += "\n\n<i>" + esc(format_trace(trace, used, self.estimated)) + "</i>"
            return self.api.send(chat_id, reply)
        return super().handle_update(upd)                   # /agenda /reset /debug

    def tick(self) -> None:
        """Appelé entre deux long-polls : synchro Strava périodique + notification."""
        if self.pilot.strava is None or self.clock() - self.last_sync < self.sync_every_s:
            return
        txt = self._sync(None, quiet=True)
        if txt:
            for chat in self.allowed:
                self.api.send(chat, txt)

    def run_forever(self) -> None:
        offset = None
        log.info("Bot pilote démarré (Ctrl+C pour arrêter).")
        while True:
            try:
                self.tick()
                updates = self.api.call("getUpdates", http_timeout=50, timeout=40, offset=offset,
                                        allowed_updates=["message", "edited_message"])
            except Exception as e:
                log.warning("boucle : %s", e)
                self.sleep(3)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    self.handle_update(u)
                except Exception as e:
                    log.exception("erreur de traitement")
                    chat = (u.get("message") or {}).get("chat", {}).get("id")
                    if chat:
                        self.api.send(chat, f"⚠ Erreur interne : {esc(type(e).__name__)}: {esc(str(e))[:200]}")
