#!/usr/bin/env bash
# Installation automatique (macOS / Linux) :
# trouve un Python >= 3.11, crée .venv, installe les dépendances, lance les tests.
# Usage :  bash install.sh
set -euo pipefail
cd "$(dirname "$0")"

ok_version='import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
candidates=(
  python3.13 python3.12 python3.11
  /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11
  /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11
  /Library/Frameworks/Python.framework/Versions/3.13/bin/python3
  /Library/Frameworks/Python.framework/Versions/3.12/bin/python3
  python3
)

# Un .venv créé avec le python3 de macOS (3.9) est inutilisable : on le recrée.
if [ -x .venv/bin/python ] && ! .venv/bin/python -c "$ok_version" 2>/dev/null; then
  echo "↻ L'environnement .venv existant utilise un Python trop ancien : je le recrée."
  rm -rf .venv
fi

PY=""
tried=""
for c in "${candidates[@]}"; do
  command -v "$c" >/dev/null 2>&1 || continue
  "$c" -c "$ok_version" 2>/dev/null || continue
  if [ -x .venv/bin/python ]; then PY="$(command -v "$c")"; break; fi
  # Certains Python (paquets système incomplets...) échouent à créer un venv : on passe au suivant.
  if "$c" -m venv .venv >/dev/null 2>&1; then PY="$(command -v "$c")"; break; fi
  rm -rf .venv
  tried="$tried $c"
done

if [ -z "$PY" ]; then
  found="$(python3 --version 2>&1 || echo 'aucun')"
  echo "✗ Il faut Python 3.11 ou plus récent (trouvé : ${found}${tried:+ ; échec venv avec :$tried})."
  echo "  Le python3 livré avec macOS (3.9) est trop ancien pour LangGraph 1.x."
  echo
  echo "  Installe Python 3.12, au choix :"
  echo "    • avec Homebrew :  brew install python@3.12"
  echo "    • sinon         :  installeur macOS sur https://www.python.org/downloads/"
  echo
  echo "  Puis relance :  bash install.sh"
  exit 1
fi
echo "✓ Python retenu : $(.venv/bin/python --version 2>&1)"

echo "… installation des dépendances (1 à 2 min)"
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
echo "✓ Dépendances installées"

echo "… lancement des tests"
.venv/bin/python -m pytest -q -p no:cacheprovider | tail -1


# ---------------------------------------------------------------- réglages de l'application (une fois)
CONF="$HOME/.ai_coach/app.env"
if [ ! -f "$CONF" ]; then
  echo
  echo "Réglages de l'application (une seule fois, modifiables dans $CONF)"
  mkdir -p "$HOME/.ai_coach" && chmod 700 "$HOME/.ai_coach"
  while true; do
    read -r -s -p "  Code d'accès à l'application (8 caractères minimum) : " CODE; echo
    [ ${#CODE} -ge 8 ] && break
    echo "  ✗ trop court"
  done
  read -r -s -p "  Clé Claude (sk-ant-…, Entrée pour passer : le coach répondra en mode simplifié) : " KEY; echo
  read -r -p "  Ton e-mail (exigé par Apple pour les notifications ; reste sur ton Mac) : " MAIL
  umask 077
  {
    echo "export COACH_WEB_PASSCODE='${CODE//\'/}'"
    [ -n "$KEY" ] && echo "export ANTHROPIC_API_KEY='${KEY//\'/}'"
    [ -n "$MAIL" ] && echo "export COACH_PUSH_CONTACT='mailto:${MAIL//\'/}'"
    echo "export COACH_DAILY_BUDGET_EUR=1"
  } > "$CONF"
  chmod 600 "$CONF"
  echo "✓ Réglages enregistrés dans $CONF"
fi

# Strava (v0.12) : application créée sur strava.com/settings/api (voir GUIDE_APP.md §1)
if ! grep -q COACH_STRAVA_CLIENT_ID "$CONF" 2>/dev/null; then
  echo
  echo "Connexion Strava : Client ID et Client Secret de ton application (strava.com/settings/api)."
  read -r -p "  Client ID (Entrée pour passer) : " SID
  if [ -n "$SID" ]; then
    read -r -s -p "  Client Secret : " SSEC; echo
    umask 077
    {
      echo "export COACH_STRAVA_CLIENT_ID='${SID//\'/}'"
      echo "export COACH_STRAVA_CLIENT_SECRET='${SSEC//\'/}'"
    } >> "$CONF"
    echo "✓ Strava configuré"
  else
    echo "  (relance bash install.sh plus tard pour l'ajouter)"
  fi
fi

echo
echo "Prêt. Pour lancer l'application :  bash start.sh"
