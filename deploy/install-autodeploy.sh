#!/usr/bin/env bash
# À lancer UNE fois sur le serveur (v0.35) : installe la tâche cron du déploiement automatique.
#   bash ~/ai_endurance_coach/deploy/install-autodeploy.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
LINE="*/5 * * * * bash $DIR/autodeploy.sh >> $DIR/autodeploy.log 2>&1"
# sans crontab existante, « crontab -l » échoue : on part d'une liste vide (sinon set -e arrête tout sans rien dire)
CUR="$(crontab -l 2>/dev/null || true)"
{ printf '%s\n' "$CUR" | grep -v -e 'autodeploy.sh' -e '^$' || true; echo "$LINE"; } | crontab -
echo "✓ déploiement automatique installé (toutes les 5 min) :"
crontab -l | grep autodeploy
echo "Journal : tail -f $DIR/autodeploy.log"
