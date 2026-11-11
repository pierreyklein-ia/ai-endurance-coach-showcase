#!/usr/bin/env bash
# Lance l'application (Mac) : serveur local + accès HTTPS depuis le téléphone via Tailscale.
# Usage :  bash start.sh            (les données de l'application sont dans ~/.ai_coach_app)
set -euo pipefail
cd "$(dirname "$0")"
CONF="$HOME/.ai_coach/app.env"
if [ ! -x .venv/bin/python ] || [ ! -f "$CONF" ]; then
  echo "✗ Lance d'abord :  bash install.sh"; exit 1
fi
# shellcheck disable=SC1090
source "$CONF"
# application « neuve » : aucun réglage de course hérité, données à part
unset COACH_RACE_NAME COACH_RACE_START COACH_RACE_TZ COACH_RACE_DURATION_MIN COACH_GOAL
export COACH_HOME="${COACH_HOME:-$HOME/.ai_coach_app}"
PORT="${COACH_PORT:-8000}"

MODE=""
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then MODE="--claude"; else
  echo "ℹ Pas de clé Claude : le coach répond en mode simplifié (ajoute ANTHROPIC_API_KEY dans $CONF)."
fi

TS=""
for c in tailscale /Applications/Tailscale.app/Contents/MacOS/Tailscale; do
  command -v "$c" >/dev/null 2>&1 && { TS="$c"; break; }
done
URL=""
if [ -n "$TS" ]; then
  if "$TS" serve --bg "$PORT" >/dev/null 2>&1; then
    URL="$("$TS" serve status 2>/dev/null | grep -Eo 'https://[^ ]+' | head -1 || true)"
  else
    echo "⚠ Tailscale n'a pas pu publier l'application (HTTPS activé dans la console ? voir GUIDE_APP.md §2)."
  fi
else
  echo "ℹ Tailscale n'est pas installé : l'application ne sera accessible que depuis ce Mac."
fi

echo
echo "  Sur ce Mac  : http://127.0.0.1:$PORT"
[ -n "$URL" ] && echo "  Téléphone   : $URL   (Safari → Partager → Sur l'écran d'accueil)"
echo "  Arrêter     : Ctrl+C"
echo
( sleep 3; command -v open >/dev/null 2>&1 && open "http://127.0.0.1:$PORT" ) >/dev/null 2>&1 &
RUN=(.venv/bin/python -m coach.web serve --port "$PORT" $MODE)
if command -v caffeinate >/dev/null 2>&1; then exec caffeinate -i "${RUN[@]}"; else exec "${RUN[@]}"; fi
