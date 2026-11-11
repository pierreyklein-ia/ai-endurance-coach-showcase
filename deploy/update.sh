#!/usr/bin/env bash
# Mise à jour automatique (v0.17). Lancé par cron toutes les 15 min (voir setup.sh), en root.
#
# Canal : un bucket privé Object Storage. COACH_UPDATE_URL (dans deploy/app.env) est une « pre-authenticated
# request » en LECTURE sur le préfixe releases/ : le serveur peut lire les versions publiées, rien d'autre.
#   releases/latest.txt                         « 0.17.1 <sha256 du zip> »
#   releases/ai_endurance_coach-0.17.1.zip
# Si la version publiée diffère de ../VERSION : vérification de la somme SHA-256, sauvegarde des données,
# copie du code (deploy/app.env et deploy/.env, les réglages du serveur, ne sont jamais touchés), reconstruction.
# En cas d'échec de la reconstruction : retour automatique à la version précédente.
set -euo pipefail

# le script peut se remplacer lui-même pendant la mise à jour : on s'exécute depuis une copie
if [ -z "${COACH_UPDATE_SELF:-}" ]; then
  DIR="$(cd "$(dirname "$0")" && pwd)"
  cp "$0" /tmp/coach-update.sh
  COACH_UPDATE_SELF=1 exec bash /tmp/coach-update.sh "$DIR"
fi
DEPLOY="$1"
cd "$DEPLOY"
ROOT="$(cd .. && pwd)"
log() { echo "$(date -Is) $*"; }

URL="$(grep -E '^COACH_UPDATE_URL=' app.env 2>/dev/null | head -1 | cut -d= -f2- || true)"
[ -n "$URL" ] || exit 0
case "$URL" in
  */o/) URL="${URL}releases/" ;;
  */) ;;
  *) URL="$URL/" ;;
esac

REMOTE="$(curl -fsS -m 30 "${URL}latest.txt")" || { log "lecture du canal impossible"; exit 1; }
VER="$(echo "$REMOTE" | awk '{print $1}')"
SUM="$(echo "$REMOTE" | awk '{print $2}')"
CUR="$(cat "$ROOT/VERSION" 2>/dev/null || echo aucune)"
[ -n "$VER" ] && [ -n "$SUM" ] || { log "latest.txt invalide"; exit 1; }
[ "$VER" = "$CUR" ] && exit 0

log "nouvelle version $VER (actuelle : $CUR)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsS -m 300 -o "$TMP/app.zip" "${URL}ai_endurance_coach-${VER}.zip"
echo "$SUM  $TMP/app.zip" | sha256sum -c - >/dev/null || { log "somme de contrôle fausse : mise à jour annulée"; exit 1; }
unzip -q "$TMP/app.zip" -d "$TMP"
[ -f "$TMP/ai_endurance_coach/VERSION" ] || { log "archive invalide"; exit 1; }

docker compose exec -T app python -m coach.backup || log "sauvegarde avant mise à jour en échec (on continue)"
OWNER="$(stat -c '%u:%g' "$DEPLOY/app.env")"
rm -rf "$ROOT.prev" && cp -a "$ROOT" "$ROOT.prev"
rsync -a --checksum --delete --exclude 'deploy/app.env' --exclude 'deploy/.env' "$TMP/ai_endurance_coach/" "$ROOT/"
chown -R "$OWNER" "$ROOT"
chmod 600 "$DEPLOY/app.env"

if docker compose up -d --build; then
  log "mise à jour $CUR -> $VER : OK"
else
  log "ÉCHEC de la reconstruction : retour à $CUR"
  rsync -a --checksum --delete "$ROOT.prev/" "$ROOT/"
  docker compose up -d --build || true
  exit 1
fi
