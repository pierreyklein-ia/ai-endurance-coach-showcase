"""Application web installable (v0.11).

  python -m coach.web serve [--claude] [--port 8000]   # l'application (Mac ou serveur)
  python -m coach.web import-carnet <export>          # ton plan VVX (export du Carnet) -> plan local
  python -m coach.web push-test                       # envoie une notification de test
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m coach.web", description="AI Endurance Coach — application web")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--claude", action="store_true", help="vrais appels Claude (ANTHROPIC_API_KEY)")
    s.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = accès via Tailscale/tunnel uniquement")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-strava", action="store_true")
    s.add_argument("--no-google", action="store_true")
    i = sub.add_parser("import-carnet")
    i.add_argument("path")
    i.add_argument("--from", dest="from_day", type=date.fromisoformat, default=None,
                   help="n'importer qu'à partir de cette date (défaut : tout)")
    i.add_argument("--write-calendar", action="store_true", help="écrire aussi dans Google Agenda")
    sub.add_parser("push-test")
    a = ap.parse_args(argv)

    from ..pilot.config import coach_home, load_settings
    from ..pilot.runtime import build_pilot
    home, st = coach_home(), load_settings()

    if a.cmd == "import-carnet":
        from ..pilot.carnet import apply_carnet, parse_carnet
        cal = None
        if a.write_calendar:
            from ..adapters.google_auth import calendar_service
            cal = calendar_service(home, True)
        pilot = build_pilot(home=home, settings=st, calendar_service=cal)
        imp = parse_carnet(a.path)
        if a.write_calendar:
            plan = [s for s in imp.sessions if a.from_day is None or s.day >= a.from_day]
            pilot.write_plan(plan)
        n = apply_carnet(pilot.store, imp, a.from_day)
        print(f"✅ {n} séance(s) importée(s), {len(imp.feedback)} retour(s), {len(imp.weeks)} semaine(s)."
              + (f" Ignoré : {len(imp.skipped)}" if imp.skipped else ""))
        for x in imp.skipped[:5]:
            print("  -", x)
        return 0

    from .push import Notifier, ensure_vapid, pywebpush_sender
    public_key = ensure_vapid(home)
    sender = pywebpush_sender(home) if public_key else None

    if a.cmd == "push-test":
        pilot = build_pilot(home=home, settings=st)
        n = Notifier(pilot.store, sender).send("AI Endurance Coach", "Notification de test ✅")
        print(f"{n} appareil(s) notifié(s)." if sender else "✗ pywebpush absent : pip install pywebpush")
        return 0

    # ----------------------------------------------------------------- serve
    passcode = os.getenv("COACH_WEB_PASSCODE", "")
    if len(passcode) < 8:
        print("✗ Choisis un code d'accès d'au moins 8 caractères : export COACH_WEB_PASSCODE=\"…\"")
        return 1
    if a.claude and not os.getenv("ANTHROPIC_API_KEY"):
        print("✗ ANTHROPIC_API_KEY absente.")
        return 1
    import uvicorn

    from .app import create_app
    from .auth import load_secret
    from .service import CoachService
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    svc = None
    if not a.no_google and (home / "google_token.json").exists():       # Google Agenda : optionnel
        try:
            from ..adapters.google_auth import calendar_service
            svc = calendar_service(home, False)
        except Exception as e:
            print(f"⚠ Google Agenda non connecté ({e}) : le plan reste local.")
    strava, connector, factory = None, None, None
    cid, csecret = os.getenv("COACH_STRAVA_CLIENT_ID", ""), os.getenv("COACH_STRAVA_CLIENT_SECRET", "")
    if not a.no_strava and cid and csecret:
        from ..pilot.strava_api import StravaAPI, StravaAPIConnector
        connector = StravaAPIConnector(home, cid, csecret)
        factory = lambda: StravaAPI(home, cid, csecret)          # noqa: E731
        if connector.connected():
            strava = factory()
    elif not a.no_strava:
        print("⚠ Strava non configuré (COACH_STRAVA_CLIENT_ID / _SECRET) : relance « bash install.sh ».")
    pilot = build_pilot(a.claude, home=home, settings=st, calendar_service=svc, strava=strava)
    service = CoachService(pilot, Notifier(pilot.store, sender), estimated=not a.claude,
                           connector=connector, strava_factory=factory)
    app = create_app(service, passcode=passcode, secret=load_secret(home), public_key=public_key)
    print(f"Application · {'Claude' if a.claude else 'hors-ligne'} · agenda {'Google' if svc else 'local'} · "
          f"Strava {'connecté' if strava else 'à connecter dans l’app'} · notifications {'oui' if sender else 'non'} · "
          + ("premier lancement : l'application va te guider" if service.needs_onboarding()
             else f"{st.race_name} J-{(st.race_date - date.today()).days}"))
    print(f"→ http://{a.host}:{a.port}  (téléphone : via Tailscale, voir GUIDE_APP.md)")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning", proxy_headers=True,
                forwarded_allow_ips=os.getenv("COACH_TRUSTED_PROXIES", "127.0.0.1"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
