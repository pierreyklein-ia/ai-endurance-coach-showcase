#!/usr/bin/env bash
# Installation de l'application sur un serveur Ubuntu (Oracle Cloud Always Free, ARM ou x86).
# À lancer UNE fois, depuis le dossier du projet sur le serveur :   bash deploy/setup.sh
# Relancer le script met à jour l'application (les données et réglages sont gardés).
set -euo pipefail
cd "$(dirname "$0")"

echo "== 1/5 Docker et outils"
command -v rsync >/dev/null && command -v unzip >/dev/null || \
  { sudo apt-get update -qq && sudo apt-get install -y -qq rsync unzip; }
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"

echo "== 2/5 Pare-feu du serveur (ports 80 et 443)"
# Les images Ubuntu d'Oracle bloquent tout sauf SSH dans iptables : on ouvre le web.
for p in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || \
    sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport "$p" -j ACCEPT
done
if command -v netfilter-persistent >/dev/null 2>&1; then sudo netfilter-persistent save >/dev/null; fi

echo "== 3/5 Adresse publique"
IP="$(curl -fsS https://api.ipify.org || curl -fsS https://ifconfig.me)"
DEFAULT_DOMAIN="${IP//./-}.sslip.io"            # sslip.io : nom gratuit qui pointe vers l'IP, sans inscription
if [ -f .env ]; then
  # shellcheck disable=SC1091
  source .env
fi
if [ -z "${DOMAIN:-}" ]; then
  read -r -p "  Nom de domaine [$DEFAULT_DOMAIN] : " DOMAIN
  DOMAIN="${DOMAIN:-$DEFAULT_DOMAIN}"
  echo "DOMAIN=$DOMAIN" > .env
fi
echo "  → https://$DOMAIN"

echo "== 4/5 Réglages de l'application"
if [ ! -f app.env ]; then
  umask 077
  while true; do
    read -r -s -p "  Code d'accès à l'application (8 caractères minimum) : " CODE; echo
    [ ${#CODE} -ge 8 ] && break; echo "  ✗ trop court"
  done
  read -r -s -p "  Clé Claude (sk-ant-…, Entrée pour passer) : " KEY; echo
  read -r -p "  Strava Client ID : " SID
  read -r -s -p "  Strava Client Secret : " SSEC; echo
  read -r -p "  Ton e-mail (contact exigé par Apple pour les notifications) : " MAIL
  {
    echo "COACH_WEB_PASSCODE=$CODE"
    [ -n "$KEY" ] && echo "ANTHROPIC_API_KEY=$KEY"
    [ -n "$SID" ] && echo "COACH_STRAVA_CLIENT_ID=$SID"
    [ -n "$SSEC" ] && echo "COACH_STRAVA_CLIENT_SECRET=$SSEC"
    [ -n "$MAIL" ] && echo "COACH_PUSH_CONTACT=mailto:$MAIL"
    echo "COACH_DAILY_BUDGET_EUR=1"
  } > app.env
  chmod 600 app.env
  echo "  ✓ réglages dans deploy/app.env (lisible par toi seul)"
else
  echo "  ✓ deploy/app.env existe déjà (modifie-le puis relance ce script pour changer un réglage)"
fi

if ! grep -q '^COACH_UPDATE_URL=' app.env; then
  echo "  Mises à jour automatiques (v0.17) : colle l'URL « pre-authenticated request » de lecture des versions"
  read -r -p "  URL des mises à jour (Entrée pour passer) : " UURL
  if [ -n "$UURL" ]; then echo "COACH_UPDATE_URL=$UURL" >> app.env; fi
fi
if ! grep -q '^COACH_BACKUP_URL=' app.env; then
  echo "  Sauvegarde distante (v0.16) : colle l'URL « pre-authenticated request » du bucket Object Storage"
  read -r -p "  URL de sauvegarde (Entrée pour garder seulement la copie locale) : " BURL
  if [ -n "$BURL" ]; then echo "COACH_BACKUP_URL=$BURL" >> app.env; fi
fi

echo "== 5/5 Construction et démarrage (2 à 5 min la première fois)"
$DOCKER compose up -d --build
sleep 5
$DOCKER compose ps

echo "== Sauvegarde quotidienne (3 h 17) et premier essai"
sudo tee /etc/cron.d/coach-backup >/dev/null <<CRON
# Sauvegarde quotidienne de l'application (coach/backup.py) — journal : /var/log/coach-backup.log
17 3 * * * root cd $(pwd) && docker compose exec -T app python -m coach.backup >> /var/log/coach-backup.log 2>&1
CRON
sudo chmod 644 /etc/cron.d/coach-backup
sudo tee /etc/cron.d/coach-update >/dev/null <<CRON
# Mises à jour automatiques depuis le canal Object Storage (deploy/update.sh) — journal : /var/log/coach-update.log
*/15 * * * * root bash $(pwd)/update.sh >> /var/log/coach-update.log 2>&1
CRON
sudo chmod 644 /etc/cron.d/coach-update
sleep 5
$DOCKER compose exec -T app python -m coach.backup || echo "  ⚠ premier essai de sauvegarde en échec : voir le message ci-dessus"
echo
echo "✅ L'application est en ligne : https://$DOMAIN"
echo "   (le certificat HTTPS peut prendre une minute à arriver)"
echo
echo "Dernière étape côté Strava : strava.com/settings/api → « Authorization Callback Domain » = $DOMAIN"
echo "Journaux :  cd $(pwd) && $DOCKER compose logs -f app"
echo "Sauvegardes : /var/log/coach-backup.log ; restaurer : bash deploy/restore.sh"
echo "Mises à jour automatiques : /var/log/coach-update.log (version : $(cat ../VERSION 2>/dev/null))"
