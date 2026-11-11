"""Mode pilote en ligne de commande.

  python -m coach.pilot google            # connecter ton Google Agenda (navigateur)
  python -m coach.pilot strava            # connecter Strava (connecteur officiel) + 1re synchro
  python -m coach.pilot import <archive>  # alternative : archive Strava (zip) ou dossier de FIT
  python -m coach.pilot status            # charge, ACWR, dernières sorties
  python -m coach.pilot plan [--write]    # affûtage proposé (et écrit dans l'agenda avec --write)
  python -m coach.pilot bot --claude      # le bot Telegram sur ton téléphone
  python -m coach.pilot clear-calendar    # retire TOUT ce que le coach a écrit dans l'agenda
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date


def _google(home, interactive=True):
    from ..adapters.google_auth import calendar_service
    return calendar_service(home, interactive)


def main(argv=None) -> int:
    try:
        return _main(argv)
    except (FileNotFoundError, RuntimeError, LookupError, PermissionError) as e:
        print(f"✗ {e}")
        return 1
    except KeyboardInterrupt:
        print("\nArrêt.")
        return 0


def _main(argv=None) -> int:
    from .config import coach_home, load_settings
    ap = argparse.ArgumentParser(prog="python -m coach.pilot", description="AI Endurance Coach — pilote")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("google", help="connecter Google Agenda")
    s = sub.add_parser("strava", help="connecter Strava et synchroniser")
    s.add_argument("--days", type=int, default=120)
    i = sub.add_parser("import", help="importer une archive Strava ou un dossier de FIT/GPX")
    i.add_argument("path")
    i.add_argument("--days", type=int, default=180)
    sub.add_parser("status")
    p = sub.add_parser("plan")
    p.add_argument("--write", action="store_true", help="écrire dans Google Agenda")
    p.add_argument("--start", type=date.fromisoformat, default=None)
    b = sub.add_parser("bot")
    b.add_argument("--claude", action="store_true", help="vrais appels Claude")
    b.add_argument("--no-strava", action="store_true")
    sub.add_parser("clear-calendar")
    a = ap.parse_args(argv)

    home, st = coach_home(), load_settings()
    print(f"Données pilote : {home}  ·  fuseau {st.tz}  ·  FC repos {st.hr_rest:.0f} / max {st.hr_max:.0f}"
          f" / seuil {st.threshold_hr:.0f}")

    if a.cmd == "google":
        from ..adapters.calendar import APP_TAG
        svc = _google(home)
        try:                                   # lecture du fuseau : peut être refusée par le scope minimal
            tz = svc.calendars().get(calendarId="primary").execute().get("timeZone", "?")
        except Exception:
            tz = "?"
        n = len(svc.events().list(calendarId="primary", privateExtendedProperty=f"coach_app={APP_TAG}",
                                  maxResults=250).execute().get("items", []))
        print(f"✅ Google Agenda connecté (fuseau de l'agenda : {tz}) — {n} événement(s) du coach déjà présents.")
        if tz not in ("?", st.tz):
            print(f"⚠ Ton Mac est en {st.tz} : export COACH_TZ={tz} pour aligner les heures de séance.")
        return 0

    from .runtime import build_pilot
    if a.cmd == "strava":
        from .strava_mcp import StravaMCP, sync_strava
        pilot = build_pilot(home=home, settings=st)
        client = StravaMCP(home)
        print("Connexion au connecteur officiel Strava (usage personnel, §3.5 de la politique API)…")
        try:
            res = sync_strava(pilot.store, st, client, days=a.days)
        except Exception as e:                       # erreurs réseau/OAuth souvent emballées (ExceptionGroup)
            while getattr(e, "exceptions", None):
                e = e.exceptions[0]
            print(f"✗ Connexion Strava impossible : {type(e).__name__}: {e}\n"
                  "  Copie ce message à ton coach technique ; plan B : archive Strava (GUIDE_PILOTE.md §3).")
            return 1
        r = res.report
        print(f"✅ {res.listed} sortie(s) lue(s) : {r.new} nouvelle(s), {r.upgraded} mise(s) à jour, "
              f"{r.duplicate} déjà connue(s) · sources {r.by_source}")
        if res.partial:
            print(f"⚠ synchro partielle ({res.partial}) : relance la commande plus tard, elle reprend où elle s'est arrêtée.")
        print(pilot.status_text())
        return 0

    if a.cmd == "import":
        from .ingest import import_archive
        pilot = build_pilot(home=home, settings=st)
        r = import_archive(a.path, pilot.store, st, days=a.days)
        print(f"✅ {r.new} nouvelle(s), {r.upgraded} mise(s) à jour, {r.duplicate} déjà connue(s) · {r.by_source}")
        for s_ in r.skipped[:10]:
            print("  -", s_)
        print(pilot.status_text())
        return 0

    if a.cmd == "status":
        pilot = build_pilot(home=home, settings=st)
        print(pilot.status_text())
        print("\nAgenda (7 j) :\n" + pilot.agenda_text())
        return 0

    if a.cmd == "plan":
        pilot = build_pilot(home=home, settings=st, calendar_service=_google(home) if a.write else None)
        try:
            plan, verdict, ti = pilot.repo.propose_taper(a.start)
        except LookupError as e:
            print(f"✗ {e}")
            return 1
        print(pilot.propose_text(plan, verdict, ti))
        if a.write:
            if not verdict.allowed:
                print("⛔ garde-fous bloquants : rien n'est écrit.")
                return 1
            ops = pilot.write_plan(plan)
            print(f"✅ écrit dans Google Agenda ({len(ops)} événement(s)).")
        else:
            print("\n(aperçu : ajoute --write pour l'écrire dans ton agenda)")
        return 0

    if a.cmd == "clear-calendar":
        pilot = build_pilot(home=home, settings=st, calendar_service=_google(home))
        n = pilot.google.clear_all()
        print(f"✅ {n} événement(s) du coach supprimé(s). Tes autres événements n'ont pas été touchés.")
        return 0

    if a.cmd == "bot":
        from ..adapters.telegram_bot import TelegramAPI
        from .bot import PilotBot
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        allowed = {int(x) for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x}
        if not token or not allowed:
            print("✗ Il faut TELEGRAM_BOT_TOKEN et TELEGRAM_ALLOWED_CHAT_IDS (ton chat_id : "
                  "lance d'abord `python -m coach.adapters.telegram_bot` et envoie /start).")
            return 1
        if a.claude and not os.getenv("ANTHROPIC_API_KEY"):
            print("✗ ANTHROPIC_API_KEY absente.")
            return 1
        try:
            svc = _google(home, interactive=False)
        except Exception as e:
            print(f"⚠ Google Agenda non connecté ({e}) : les séances resteront dans le plan local.")
            svc = None
        strava = None
        if not a.no_strava and (home / "strava_mcp.json").exists():
            from .strava_mcp import StravaMCP
            strava = StravaMCP(home, interactive=False)
        pilot = build_pilot(a.claude, home=home, settings=st, calendar_service=svc, strava=strava)
        print(f"Bot pilote · {'Claude' if a.claude else 'hors-ligne'} · agenda {'Google' if svc else 'local'}"
              f" · Strava {'connecté' if strava else 'non connecté'} · budget {st.daily_budget_eur:.2f} €/jour")
        PilotBot(TelegramAPI(token), allowed, pilot, estimated=not a.claude).run_forever()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
