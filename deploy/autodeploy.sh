#!/usr/bin/env bash
# Déploiement automatique (v0.35), lancé par cron toutes les 5 minutes (voir deploy/install-autodeploy.sh).
# Ne fait rien tant que la branche « deploy » de GitHub n'a pas bougé ; sinon lance deploy.sh.
# Journal : deploy/autodeploy.log (gardé court).
set -euo pipefail
# tout est dans une fonction : bash la lit en entier avant de l'exécuter, ce qui permet au script
# d'être remplacé par git pendant qu'il tourne
main() {
  DIR="$(cd "$(dirname "$0")" && pwd)"
  cd "$DIR/.."
  exec 9>"$DIR/.autodeploy.lock"
  flock -n 9 || exit 0                              # un déploiement est déjà en cours
  BRANCH="${COACH_DEPLOY_BRANCH:-deploy}"
  git fetch --quiet origin "$BRANCH" 2>/dev/null || { echo "$(date -Is) GitHub injoignable"; exit 0; }
  [ "$(git rev-parse HEAD)" = "$(git rev-parse "origin/$BRANCH")" ] && exit 0
  echo "$(date -Is) nouvelle version validée : $(git log -1 --format='%h %s' "origin/$BRANCH")"
  if bash "$DIR/deploy.sh"; then
    echo "$(date -Is) déployé : v$(cat VERSION)"
  else
    echo "$(date -Is) ÉCHEC du déploiement (l'ancienne version tourne toujours)"
  fi
  LOG="$DIR/autodeploy.log"
  [ -f "$LOG" ] && tail -n 400 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG" || true
}
main "$@"
