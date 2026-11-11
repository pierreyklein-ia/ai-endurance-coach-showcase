"""Notifications Web Push (VAPID) : rappel de séance, synchro, réponse du coach.

Sur iPhone, les notifications d'une application web ne marchent qu'une fois l'app
ajoutée à l'écran d'accueil (iOS 16.4+), servie en HTTPS, et après un « Autoriser »
donné par un geste de l'utilisateur. Les clés VAPID sont créées une fois dans
~/.ai_coach/vapid_private.pem (droits 600).
"""
from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from ..pilot.store import PilotStore

log = logging.getLogger("coach.push")
Sender = Callable[[dict, str], None]          # (abonnement, données JSON) -> lève si échec


class PushGone(Exception):
    """Abonnement expiré ou révoqué (HTTP 404/410) : à supprimer."""


def vapid_paths(home: Path) -> tuple[Path, Path]:
    return home / "vapid_private.pem", home / "vapid_public.txt"


def ensure_vapid(home: Path) -> str | None:
    """Crée les clés si besoin ; retourne la clé publique (base64url) ou None si pywebpush absent."""
    priv, pub = vapid_paths(home)
    if pub.exists() and priv.exists():
        return pub.read_text().strip()
    try:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid
    except ImportError:
        return None
    v = Vapid()
    v.generate_keys()
    v.save_key(str(priv))
    priv.chmod(0o600)
    raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    key = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    pub.write_text(key)
    return key


def pywebpush_sender(home: Path, env: Mapping[str, str] | None = None, webpush=None,
                     error: type[Exception] = Exception) -> Sender | None:
    """Expéditeur réel. `env` (variables d'environnement), `webpush` (fonction d'envoi) et `error` (classe d'erreur
    de la bibliothèque) sont injectables pour les tests (inversion de contrôle) ; par défaut os.environ et pywebpush."""
    if webpush is None:
        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            return None
        error = WebPushException
    priv, _ = vapid_paths(home)
    contact = (os.environ if env is None else env).get("COACH_PUSH_CONTACT", "mailto:coach@localhost")

    def send(sub: dict, data: str) -> None:
        try:
            webpush(subscription_info=sub, data=data, vapid_private_key=str(priv),
                    vapid_claims={"sub": contact}, ttl=12 * 3600)
        except error as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                raise PushGone(str(e)) from e
            raise
    return send


class Notifier:
    def __init__(self, store: PilotStore, sender: Sender | None):
        self.store, self.sender = store, sender

    @property
    def enabled(self) -> bool:
        return self.sender is not None

    def send(self, title: str, body: str, url: str = "/", tag: str = "coach") -> int:
        """Envoie à tous les appareils abonnés ; retourne le nombre d'envois réussis."""
        if not self.sender:
            return 0
        data, ok = json.dumps({"title": title, "body": body, "url": url, "tag": tag}), 0
        for sub in self.store.push_subs():
            try:
                self.sender(sub, data)
                ok += 1
            except PushGone:
                self.store.remove_push_sub(sub["endpoint"])
            except Exception as e:                 # un appareil en panne ne bloque pas les autres
                log.warning("push : %s", e)
        return ok
