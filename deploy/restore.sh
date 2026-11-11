#!/usr/bin/env bash
# Restaure une sauvegarde de l'application.
#   bash deploy/restore.sh                       # liste les sauvegardes locales
#   bash deploy/restore.sh coach-20261029-0317.tar.gz   # restaure celle-ci (locale ou fichier téléchargé)
# L'application est arrêtée pendant la restauration ; les données actuelles sont d'abord sauvegardées.
set -euo pipefail
cd "$(dirname "$0")"
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"
VOL="$($DOCKER volume ls -q | grep -E '(^|_)coach_data$' | head -1)"
[ -n "$VOL" ] || { echo "Volume coach_data introuvable"; exit 1; }

if [ $# -eq 0 ]; then
  echo "Sauvegardes locales :"
  $DOCKER run --rm -v "$VOL":/d alpine sh -c 'ls -1 /d/backups 2>/dev/null || echo "(aucune)"'
  exit 0
fi

ARCH="$1"
echo "== Sauvegarde de l'état actuel avant restauration"
$DOCKER compose exec -T app python -m coach.backup || true

echo "== Arrêt de l'application"
$DOCKER compose stop app

if [ -f "$ARCH" ]; then                       # fichier téléchargé (Object Storage)
  SRC_DIR="$(cd "$(dirname "$ARCH")" && pwd)"; SRC="$(basename "$ARCH")"
  $DOCKER run --rm -v "$VOL":/d -v "$SRC_DIR":/in:ro alpine sh -c \
    "find /d -mindepth 1 -maxdepth 1 ! -name backups -exec rm -rf {} + && tar xzf /in/$SRC -C /d && rm -f /d/BACKUP_INFO.txt && chown -R 1000:1000 /d"
else                                          # sauvegarde locale
  $DOCKER run --rm -v "$VOL":/d alpine sh -c \
    "test -f /d/backups/$ARCH && find /d -mindepth 1 -maxdepth 1 ! -name backups -exec rm -rf {} + && tar xzf /d/backups/$ARCH -C /d && rm -f /d/BACKUP_INFO.txt && chown -R 1000:1000 /d"
fi

echo "== Redémarrage"
$DOCKER compose start app
echo "✅ Restauré depuis $ARCH"
