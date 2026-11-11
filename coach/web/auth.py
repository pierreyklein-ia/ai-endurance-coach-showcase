"""Authentification de l'application (pilote mono-athlète).

Un code d'accès (COACH_WEB_PASSCODE) donne un cookie de session signé (HMAC-SHA256),
HttpOnly, SameSite=Lax, Secure derrière HTTPS. Pas de base d'utilisateurs : le
produit multi-athlètes passera par un lien magique par e-mail (même cookie signé).
Bibliothèque standard uniquement.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Callable, Mapping
from pathlib import Path

COOKIE = "coach_session"
SESSION_DAYS = 90


def load_secret(home: Path, env: Mapping[str, str] | None = None) -> bytes:
    env = os.environ if env is None else env
    if env.get("COACH_WEB_SECRET"):
        return env["COACH_WEB_SECRET"].encode()
    f = home / "web_secret"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(48))
        f.chmod(0o600)
    return f.read_text().strip().encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def sign(secret: bytes, subject: str = "pilote", days: int = SESSION_DAYS, now: float | None = None) -> str:
    exp = int((now or time.time()) + days * 86400)
    body = _b64(f"v1|{subject}|{exp}".encode())
    mac = _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def verify(secret: bytes, token: str | None, now: float | None = None) -> str | None:
    """Retourne le sujet si le jeton est valide et non expiré, sinon None."""
    if not token or "." not in token:
        return None
    body, mac = token.rsplit(".", 1)
    good = _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(mac, good):
        return None
    try:
        v, subject, exp = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode().split("|")
    except ValueError:
        return None
    if v != "v1" or int(exp) < (now or time.time()):
        return None
    return subject


class Throttle:
    """Ralentit le devinage du code : 5 échecs -> 60 s de blocage."""

    def __init__(self, max_fail: int = 5, lock_s: int = 60, clock: Callable[[], float] = time.time):
        self.max_fail, self.lock_s, self.clock = max_fail, lock_s, clock       # clock : horloge injectable (tests)
        self.fails, self.locked_until = 0, 0.0

    def allowed(self) -> bool:
        return self.clock() >= self.locked_until

    def record(self, ok: bool) -> None:
        if ok:
            self.fails = 0
            return
        self.fails += 1
        if self.fails >= self.max_fail:
            self.fails, self.locked_until = 0, self.clock() + self.lock_s


def check_passcode(given: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(given.strip().encode(), expected.encode())
