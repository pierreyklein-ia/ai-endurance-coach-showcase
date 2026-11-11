"""Bot Telegram de test — le vrai usage "headless", depuis ton téléphone.

Long polling : pas de serveur, pas d'URL publique, tourne sur ton Mac.
Bibliothèque standard uniquement (urllib).

  1. Sur Telegram : @BotFather -> /newbot -> copie le token.
  2. export TELEGRAM_BOT_TOKEN="123456:ABC..."
  3. python -m coach.adapters.telegram_bot          # hors-ligne, 0 €
     python -m coach.adapters.telegram_bot --claude # vrais appels (ANTHROPIC_API_KEY)
  4. Envoie /start à ton bot : il te répond ton chat_id.
     export TELEGRAM_ALLOWED_CHAT_IDS="<ton chat_id>" puis relance.

Sécurité : sans liste blanche, le bot ne coache PERSONNE (sinon n'importe qui
trouvant ton bot consommerait tes crédits API). Le chat_id est pseudonymisé
avant d'entrer dans le graphe. Rappel RGPD : les messages de bot Telegram ne
sont pas chiffrés de bout en bout -> athlète fictif ou tes propres données.
"""
from __future__ import annotations

import argparse
import hashlib
import html as _html
import json
import logging
import os
import sys
import time
import urllib.request
from collections.abc import Callable

from ..app import handle_inbound
from ..sandbox import build_sandbox, format_agenda, format_trace
from ..schemas import Channel, InboundMessage

log = logging.getLogger("coach.telegram")
API = "https://api.telegram.org/bot{token}/{method}"
HttpPost = Callable[[str, dict, float], dict]


def _urllib_post(url: str, params: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _urllib_get(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


class TelegramAPI:
    def __init__(self, token: str, http: HttpPost = _urllib_post,
                 http_get: Callable[[str, float], bytes] = _urllib_get):
        self.token, self.http, self.http_get = token, http, http_get

    def download(self, file_id: str) -> bytes:
        """Fichier envoyé au bot (FIT...) : limite Telegram 20 Mo pour les bots."""
        info = self.call("getFile", file_id=file_id)
        return self.http_get(f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}", 60)

    def call(self, method: str, http_timeout: float = 15, **params) -> dict | list:
        resp = self.http(API.format(token=self.token, method=method), params, http_timeout)
        if not resp.get("ok"):
            raise RuntimeError(f"Telegram {method}: {resp.get('description')}")
        return resp["result"]

    def send(self, chat_id: int, text: str) -> None:
        for i in range(0, len(text), 4000):               # limite Telegram : 4096 caractères
            self.call("sendMessage", chat_id=chat_id, text=text[i:i + 4000], parse_mode="HTML")


def esc(text: str) -> str:
    """Échappement minimal pour parse_mode=HTML (& < > uniquement)."""
    return _html.escape(text, quote=False)


def pseudonym(chat_id: int, salt: str) -> str:
    return "tg_" + hashlib.sha256(f"{salt}:{chat_id}".encode()).hexdigest()[:12]


WELCOME = ("Salut, je suis ton coach d'endurance (version de test, athlète fictif : "
           "Diagonale des Fous le 15/10).\nÉcris-moi comme à un coach : ressenti, fatigue, "
           "douleur, contraintes d'agenda.\nCommandes : /agenda /reset /debug")


class CoachBot:
    def __init__(self, api: TelegramAPI, allowed: set[int], use_claude: bool = False,
                 salt: str = "dev-salt", today=None, graph=None, deps=None, sleep: Callable[[float], None] = time.sleep):
        self.api, self.allowed, self.salt = api, allowed, salt
        self.sleep = sleep                                # pause après une coupure réseau (injectable : tests)
        if graph is None:
            graph, deps = build_sandbox(today, use_claude=use_claude)
        self.graph, self.deps = graph, deps
        self.estimated = not use_claude
        self.debug: set[int] = set()
        self.version: dict[int, int] = {}                 # /reset -> nouveau thread

    def _aid(self, chat_id: int) -> str:
        return f"{pseudonym(chat_id, self.salt)}-{self.version.get(chat_id, 0)}"

    def _agenda(self, chat_id: int) -> str:
        return "<pre>" + esc(format_agenda(self.deps.repo.plan(self._aid(chat_id)))) + "</pre>"

    def handle_update(self, upd: dict) -> None:
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            return
        chat_id = msg["chat"]["id"]
        if chat_id not in self.allowed:
            log.warning("chat_id non autorisé : %s", chat_id)
            self.api.send(chat_id, f"Accès non autorisé. Ton chat_id : <code>{chat_id}</code>\n"
                                   "Ajoute-le à TELEGRAM_ALLOWED_CHAT_IDS puis relance le bot.")
            return
        if "voice" in msg or "audio" in msg:
            self.api.send(chat_id, "Les notes vocales arrivent dans la prochaine version "
                                   "(transcription). Écris-moi en texte pour l'instant 🙏")
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if text == "/start":
            self.api.send(chat_id, esc(WELCOME) + "\n\nTon agenda :\n" + self._agenda(chat_id))
            return
        if text == "/agenda":
            self.api.send(chat_id, self._agenda(chat_id))
            return
        if text == "/reset":
            self.version[chat_id] = self.version.get(chat_id, 0) + 1
            self.api.send(chat_id, "Plan et conversation réinitialisés.\n" + self._agenda(chat_id))
            return
        if text == "/debug":
            self.debug ^= {chat_id}
            self.api.send(chat_id, f"Trace technique {'activée' if chat_id in self.debug else 'désactivée'}.")
            return

        n0, trace = len(self.deps.llm.usage_log), []
        out = handle_inbound(self.graph, InboundMessage(athlete_id=self._aid(chat_id),
                                                        channel=Channel.TELEGRAM, text=text), trace)
        reply = esc(out.get("ask") or out.get("reply") or "…")
        if out.get("calendar_ops"):
            reply += "\n\n📅 Agenda mis à jour :\n" + self._agenda(chat_id)
        if chat_id in self.debug:
            reply += "\n\n<i>" + esc(format_trace(trace, self.deps.llm.usage_log[n0:],
                                                          self.estimated)) + "</i>"
        self.api.send(chat_id, reply)

    def run_forever(self) -> None:
        offset = None
        log.info("Bot démarré (Ctrl+C pour arrêter).")
        while True:
            try:
                # timeout=40 : long polling côté Telegram ; http_timeout > 40 côté client
                updates = self.api.call("getUpdates", http_timeout=50, timeout=40, offset=offset,
                                        allowed_updates=["message", "edited_message"])
            except Exception as e:                        # coupure réseau : on réessaie
                log.warning("getUpdates : %s", e)
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
                        self.api.send(chat, f"⚠ Erreur interne : {esc(type(e).__name__)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bot Telegram de test AI Endurance Coach")
    ap.add_argument("--claude", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("⚠ TELEGRAM_BOT_TOKEN absent (voir @BotFather).")
        return 1
    if a.claude and not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠ ANTHROPIC_API_KEY absente.")
        return 1
    allowed = {int(x) for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x}
    if not allowed:
        print("ℹ Aucune liste blanche : le bot répondra uniquement le chat_id à qui lui écrit.")
    CoachBot(TelegramAPI(token), allowed, use_claude=a.claude,
             salt=os.getenv("COACH_PSEUDONYM_SALT", "dev-salt")).run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
