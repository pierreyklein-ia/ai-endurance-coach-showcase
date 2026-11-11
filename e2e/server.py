"""Serveur déterministe pour les tests d'interface (Playwright, dossier e2e/).

    python e2e/server.py --port 8931

- coach hors ligne (HeuristicLLM : 0 €, réponses reproductibles), date du jour figée (TODAY) ;
- faux Google Agenda (tests.fakes_google.Svc) et faux Strava (tests.test_onboarding.FakeStrava/FakeConnector) ;
- deux états de départ, préparés une seule fois au démarrage puis copiés à chaque remise à zéro :
  « fresh » (nouvel athlète, rien de fait) et « onboarded » (accueil terminé, plan en place, VVX 85 km) ;
- POST /__e2e/reset?state=fresh|onboarded remet l'application dans cet état (chaque test part du même point).

Aucun réseau, aucune clé : les variables GEMINI_API_KEY / ANTHROPIC_API_KEY sont retirées au démarrage.
Réservé aux tests : n'est jamais lancé par l'application.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import sys
import tempfile
import threading
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "COACH_WEB_SECRET"):
    os.environ.pop(k, None)
os.environ["COACH_STT"] = "off"                         # dictée serveur coupée : pas de modèle à télécharger

import uvicorn  # noqa: E402

from coach.pilot.config import PilotSettings  # noqa: E402
from coach.pilot.runtime import build_pilot  # noqa: E402
from coach.sandbox import HeuristicLLM  # noqa: E402
from coach.schemas import PlannedSession  # noqa: E402
from coach.web.app import create_app  # noqa: E402
from coach.web.push import Notifier  # noqa: E402
from coach.web.service import CoachService  # noqa: E402
from tests.fakes_google import Svc  # noqa: E402
from tests.test_onboarding import FakeConnector, FakeStrava, trail_runner  # noqa: E402

TODAY = date(2026, 11, 4)                               # mercredi
PASSCODE = "code-test-1234"
SETTINGS = PilotSettings(tz="Europe/Paris")
ACTS = trail_runner(TODAY)
ONBOARDING = ("La VVX 85 km", "Oui, c'est ça",
              "Je cours en trail, 4 jours par semaine, sortie longue le dimanche, renfo le mercredi.",
              "6 h max, 1500 m D+", "Ça me va")


class Sent:
    """Faux expéditeur de notifications : garde les envois en mémoire."""

    def __init__(self):
        self.sent = []

    def __call__(self, sub, data):
        self.sent.append(data)


def make_service(home: Path, onboarded: bool) -> CoachService:
    p = build_pilot(home=home, calendar_service=Svc(), today_fn=lambda: TODAY,
                    settings=dataclasses.replace(SETTINGS), llm=HeuristicLLM(TODAY))
    svc = CoachService(p, Notifier(p.store, Sent()), connector=FakeConnector(),
                       strava_factory=lambda: FakeStrava(ACTS), background=False)
    if onboarded:
        p.strava = FakeStrava(ACTS)
        svc.connector.status = "connected"
    svc.last_sync = float("inf")        # v0.52.3 : pas de synchro automatique (ouverture, minuteur) pendant les tests
    return svc


def prepare(root: Path) -> dict[str, Path]:
    """Prépare les deux états une fois (quelques secondes) ; les remises à zéro ne font que copier."""
    fresh, done = root / "fresh", root / "onboarded"
    fresh.mkdir()
    done.mkdir()
    svc = make_service(done, onboarded=False)
    svc.coach_async = False
    svc.connect_strava("http://127.0.0.1")
    for text in ONBOARDING:
        svc.chat(text)
    assert svc.store.plan(), "le plan de départ n'a pas été créé"
    # une sortie longue aujourd'hui (bilan nutrition proposé) et un footing hier (à lier à la sortie d'hier)
    svc.store.upsert_session(PlannedSession(session_id="e2e-long", day=TODAY, intensity="long", duration_min=120,
                                            elevation_gain_m=600, planned_load=140))
    svc.store.upsert_session(PlannedSession(session_id="e2e-hier", day=TODAY - timedelta(days=1), intensity="z2",
                                            duration_min=60, planned_load=50))
    meta = svc.store.get("session_meta") or {}
    meta.update({"e2e-long": {"title": "Sortie longue vallonnée", "kind": "long"},
                 "e2e-hier": {"title": "Footing d'hier", "kind": "footing"}})
    svc.store.set("session_meta", meta)
    svc.store.db.close()
    make_service(fresh, onboarded=False).store.db.close()
    return {"fresh": fresh, "onboarded": done}


class Switch:
    """Application ASGI qui délègue à l'application du moment ; /__e2e/reset en construit une neuve."""

    def __init__(self, templates: dict[str, Path], work: Path):
        self.templates, self.work, self.n, self.lock = templates, work, 0, threading.Lock()
        self.app = None
        self.reset("onboarded")

    def reset(self, state: str) -> None:
        with self.lock:
            self.n += 1
            home = self.work / f"run-{self.n}"
            shutil.copytree(self.templates[state], home)
            svc = make_service(home, onboarded=state == "onboarded")
            svc.coach_async = True                       # « le coach écrit… » visible dans le chat
            self.app = create_app(svc, passcode=PASSCODE, secret=b"e2e-secret-" + b"x" * 21, public_key="BPUBKEY",
                                  background=False)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/__e2e/reset":
            qs = dict(x.split("=", 1) for x in scope.get("query_string", b"").decode().split("&") if "=" in x)
            state = qs.get("state", "onboarded")
            if state not in self.templates:
                await _plain(send, 400, b"etat inconnu")
                return
            self.reset(state)
            await _plain(send, 200, b"ok")
            return
        if scope["type"] == "lifespan":                  # pas de tâche de fond (background=False)
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        await self.app(scope, receive, send)


async def _plain(send, status: int, body: bytes) -> None:
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": body})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8931)
    a = ap.parse_args()
    if not (ROOT / "coach" / "web" / "static" / "app" / "index.html").exists():
        sys.exit("interface absente : lance d'abord « npm run build » dans frontend/")
    root = Path(tempfile.mkdtemp(prefix="coach-e2e-"))
    try:
        app = Switch(prepare(root), root)
        print(f"serveur e2e prêt sur http://127.0.0.1:{a.port} (TODAY={TODAY + timedelta(0)})", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
