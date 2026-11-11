#!/usr/bin/env bash
# Mise à jour depuis GitHub : installe la dernière version VALIDÉE (branche « deploy », avancée par la CI
# quand les tests sont verts) et reconstruit l'application.
# À la main :   bash ~/ai_endurance_coach/deploy/deploy.sh        (automatique : deploy/autodeploy.sh, v0.35)
# Les réglages (deploy/app.env, deploy/.env) et les données (volumes Docker) ne sont jamais touchés :
# ils ne sont pas dans git. Si la construction échoue, l'ancienne version continue de tourner.
set -euo pipefail
# tout est dans une fonction : bash la lit en entier avant de l'exécuter, ce qui permet au script
# d'être remplacé par git pendant qu'il tourne
main() {
  cd "$(dirname "$0")/.."
  DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"
  BRANCH="${COACH_DEPLOY_BRANCH:-deploy}"

  before="$(cat VERSION 2>/dev/null || echo aucune)"
  git fetch --quiet origin "$BRANCH"
  git reset --quiet --hard "origin/$BRANCH"
  after="$(cat VERSION)"
  echo "== $(date -Is) version $before → $after ($(git log -1 --format='%h %s'))"

  cd deploy
  FILES=(-f docker-compose.yml)
  [ -f invite.env ] && FILES+=(-f docker-compose.invite.yml)   # v0.46 : espace invité (add-invite.sh)
  $DOCKER compose "${FILES[@]}" up -d --build
  sleep 5
  $DOCKER compose "${FILES[@]}" ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
  echo "== ouvre l'onglet Détails de l'app : il doit afficher v$after"
}
main "$@"
