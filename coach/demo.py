"""Simulateur de conversation en terminal : tu joues l'athlète, le coach répond.

    python -m coach.demo                 # hors-ligne, 0 €, LLM heuristique
    python -m coach.demo --claude        # vrais appels Claude (ANTHROPIC_API_KEY)
    python -m coach.demo --date 2026-10-10   # rejouer à une autre date (ex. J-5)

Commandes : /agenda  /reset  /aide  /quit
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

from .app import handle_inbound
from .sandbox import build_sandbox, format_agenda, format_trace
from .schemas import Channel, InboundMessage

AID = "athlete_demo"
AIDE = """Messages à essayer :
  ok merci 👍                                          -> filtré, 0 token
  Je me sens en forme, je peux en rajouter ?            -> refus argumenté (affûtage)
  Ma fille a été malade toute la nuit, je suis épuisé  -> séance du jour allégée
  Mon genou me fait mal, 5/10 depuis la descente       -> changement majeur : confirmation
     puis « oui mais pas samedi » (on redemande) et « ouais vas-y »
  J'ai raté 3 jours, je peux rattraper ce week-end ?   -> garde-fous bloquent, repli prudent
  J'ai une douleur à la poitrine dans la côte           -> urgence, 0 token, séances suspendues
  J'ai fait une soirée avec beaucoup d'alcool, je m'entraîne comment demain ? -> pas d'intensité 36 h
  J'ai passé le seuil de patience avec ma femme, qu'est-ce que je peux faire ?  -> empathie + hors périmètre
     + proposition d'alléger la séance (confirmation)
(/reset entre deux scénarios : chaque réponse modifie le plan.)
Commandes : /agenda  /reset  /aide  /quit"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Simulateur AI Endurance Coach")
    ap.add_argument("--claude", action="store_true", help="utiliser de vrais appels Claude")
    ap.add_argument("--date", type=date.fromisoformat, default=None,
                    help="date simulée (AAAA-MM-JJ), par défaut aujourd'hui")
    ap.add_argument("--quiet", action="store_true", help="masquer la trace technique")
    a = ap.parse_args(argv)

    if a.claude and not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠ ANTHROPIC_API_KEY absente : export ANTHROPIC_API_KEY=sk-ant-... ou lance sans --claude.")
        return 1
    graph, deps = build_sandbox(a.date, use_claude=a.claude)
    today = deps.today()
    mode = "Claude (Haiku 4.5 + Sonnet 5)" if a.claude else "hors-ligne (heuristique, 0 €)"
    print(f"AI Endurance Coach — simulateur · mode {mode}")
    print(f"Athlète FICTIF · date simulée {today:%d/%m/%Y} · Diagonale des Fous J-{(deps.repo.race_date - today).days}")
    print("Tape /aide pour des exemples.\n")
    print("Ton agenda (7 prochains jours) :\n" + format_agenda(deps.repo.plan(AID)) + "\n")

    while True:
        try:
            text = input("toi   > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text in ("/quit", "/q", "/exit"):
            return 0
        if text == "/aide":
            print(AIDE + "\n")
            continue
        if text == "/agenda":
            print(format_agenda(deps.repo.plan(AID)) + "\n")
            continue
        if text == "/reset":
            graph, deps = build_sandbox(a.date, use_claude=a.claude)
            print("Plan et conversation réinitialisés.\n")
            continue

        n_before = len(deps.llm.usage_log)
        trace: list = []
        try:
            out = handle_inbound(graph, InboundMessage(athlete_id=AID, channel=Channel.TELEGRAM,
                                                       text=text), trace)
        except Exception as e:                      # ex. erreur API : on reste dans la boucle
            print(f"⚠ erreur : {type(e).__name__}: {e}\n")
            continue
        if not a.quiet:
            print("   · " + format_trace(trace, deps.llm.usage_log[n_before:], estimated=not a.claude))
        print("coach > " + (out.get("ask") or out.get("reply") or "(pas de réponse)"))
        if out.get("calendar_ops"):
            print("   · agenda mis à jour :\n" + format_agenda(deps.repo.plan(AID)))
        print()


if __name__ == "__main__":
    sys.exit(main())
