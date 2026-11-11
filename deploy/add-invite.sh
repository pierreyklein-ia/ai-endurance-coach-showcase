#!/usr/bin/env bash
# v0.46 : crée l'espace d'une personne invitée (ex. ta conjointe) sur ce serveur, à une adresse à elle :
#   https://invite.<ton domaine>   (sslip.io accepte n'importe quel préfixe : rien à acheter ni à configurer)
# Elle a son code d'accès, son plan, son coach, ses trophées et sa connexion Strava ; tes données ne sont
# jamais visibles depuis son espace (volume Docker à part).
# À lancer UNE fois sur le serveur :   bash ~/ai_endurance_coach/deploy/add-invite.sh
# Pour supprimer l'espace : bash deploy/add-invite.sh --supprimer   (ses données restent dans le volume
# invite_data tant que tu ne fais pas « docker volume rm deploy_invite_data »).
set -euo pipefail
KEYS='ANTHROPIC_API_KEY|COACH_STRAVA_CLIENT_ID|COACH_STRAVA_CLIENT_SECRET|GEMINI_API_KEY|GOOGLE_API_KEY|COACH_PUSH_CONTACT|COACH_MODEL_[A-Z]+|COACH_TRUSTED_PROXIES|COACH_TZ|COACH_AGENTS|COACH_IMAGE_[A-Z_]+|COACH_STT[A-Z_]*|COACH_MAX_TOKENS_[A-Z]+'

# v0.48 : clés de app.env recopiées telles que Docker les lira : « export CLE=… », espaces, guillemets ("…" ou '…')
# et fins de ligne Windows acceptés (avant, « export ANTHROPIC_API_KEY=… » n'était pas recopié : l'invitée tournait
# sans Claude).
shared_keys() {
  sed -e 's/\r$//' app.env | sed -nE "s/^[[:space:]]*(export[[:space:]]+)?(($KEYS)=)(.*)$/\2\4/p" |
    while IFS= read -r line; do
      k=${line%%=*}; v=${line#*=}
      v=$(printf '%s' "$v" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
      case "$v" in
        \"*\") v=${v#\"}; v=${v%\"} ;;
        \'*\') v=${v#\'}; v=${v%\'} ;;
      esac
      printf '%s=%s\n' "$k" "$v"
    done
}

# Remet les clés partagées dans invite.env (son code d'accès et son budget sont gardés).
sync_keys() {
  local tmp
  tmp=$(mktemp)
  grep -vE "^[[:space:]]*(export[[:space:]]+)?($KEYS)=" invite.env > "$tmp" || true
  shared_keys >> "$tmp"
  cat "$tmp" > invite.env; rm -f "$tmp"
  chmod 600 invite.env
  if grep -qE '^ANTHROPIC_API_KEY=.+' invite.env; then
    echo "✓ clé Claude recopiée dans deploy/invite.env"
  else
    echo "⚠ pas de ANTHROPIC_API_KEY dans deploy/app.env : l'espace invité répondra en mode simplifié (sans Claude)"
  fi
}

main() {
  cd "$(dirname "$0")"
  DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"
  # shellcheck disable=SC1091
  [ -f .env ] && source .env
  if [ "${1:-}" = "--cles" ]; then                    # v0.48 : recopier les clés sans rien reconstruire
    [ -f invite.env ] || { echo "✗ pas encore d'espace invité (deploy/invite.env)"; return 1; }
    sync_keys
    echo "  Pour l'appliquer : $DOCKER compose -f docker-compose.yml -f docker-compose.invite.yml up -d invite"
    return
  fi
  if [ "${1:-}" = "--supprimer" ]; then
    rm -f invite.env
    $DOCKER compose -f docker-compose.yml -f docker-compose.invite.yml rm -sf invite || true
    $DOCKER compose up -d
    echo "✓ espace invité arrêté (données gardées dans le volume invite_data)"; return
  fi
  HOST="invite.$DOMAIN"
  if [ ! -f invite.env ]; then
    umask 077
    while true; do
      read -r -s -p "Code d'accès de la personne invitée (8 caractères minimum, différent du tien) : " CODE; echo
      [ ${#CODE} -ge 8 ] && break; echo "  ✗ trop court"
    done
    {
      echo "COACH_WEB_PASSCODE=$CODE"
      echo "COACH_DAILY_BUDGET_EUR=0.5"
    } > invite.env
    chmod 600 invite.env
    echo "✓ réglages dans deploy/invite.env"
  else
    echo "✓ deploy/invite.env existe déjà (son code d'accès est dedans)"
  fi
  # mêmes clés que ton application (Claude, Strava, Gemini, notifications), SANS tes sauvegardes distantes ;
  # recopiées à chaque lancement (une clé changée dans app.env passe aussi chez l'invitée)
  sync_keys
  echo "== Construction et démarrage (2 à 5 min)"
  $DOCKER compose -f docker-compose.yml -f docker-compose.invite.yml up -d --build
  $DOCKER compose -f docker-compose.yml -f docker-compose.invite.yml restart caddy >/dev/null
  sleep 5
  $DOCKER compose -f docker-compose.yml -f docker-compose.invite.yml ps --format 'table {{.Service}}\t{{.State}}'
  echo
  echo "✓ Espace invité prêt : https://$HOST"
  echo "  Sur son téléphone : ouvrir l'adresse dans Safari (iPhone) ou Chrome (Android), code d'accès,"
  echo "  puis « Ajouter à l'écran d'accueil » pour l'avoir comme une app."
}
main "$@"
