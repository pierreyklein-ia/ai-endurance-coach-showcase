# Guide pilote — options avancées (v0.12)

> **Pour installer et utiliser l'application, suis [GUIDE_APP.md](GUIDE_APP.md).**
> Strava se connecte depuis l'application. Ce guide ne sert que pour les options ci-dessous,
> toutes facultatives.

Toutes les commandes se lancent depuis le dossier du projet, avec l'environnement de l'application :

```bash
cd ~/Downloads/ai_endurance_coach && source .venv/bin/activate
source ~/.ai_coach/app.env && export COACH_HOME=~/.ai_coach_app
```

## 1. Écrire le plan dans Google Agenda (15 min, une fois)

Sans cette étape, le plan reste dans l'application, ce qui suffit pour tester. Avec, chaque séance validée
dans le chat apparaît aussi dans ton agenda.

Tu crées ta propre « appli » Google (gratuit), que tu es seul à utiliser.

1. Va sur console.cloud.google.com → **Nouveau projet** (par exemple « AI Coach »).
2. **API et services → Bibliothèque** → « Google Calendar API » → **Activer**.
3. **Google Auth Platform → Branding** → Commencer. Renseigne un nom d'appli et ton Gmail.
   Pour l'audience, choisis **Externe**.
4. **Google Auth Platform → Audience → Utilisateurs test** → ajoute ton adresse Gmail.
5. **Google Auth Platform → Clients → Créer un client** → type **Application de bureau** →
   Créer → **Télécharger le JSON**.
6. Range le fichier au bon endroit :
   ```bash
   mkdir -p ~/.ai_coach_app && mv ~/Downloads/client_secret_*.json ~/.ai_coach_app/google_client.json
   ```
7. Lance la connexion :
   ```bash
   python -m coach.pilot google
   ```
   Le navigateur s'ouvre. Choisis ton Gmail. Google affiche « Google n'a pas validé cette
   application » : c'est normal, c'est la tienne. Clique sur **Continuer**, puis **Autoriser**.
   Résultat attendu : `✅ Google Agenda connecté`.

Relance ensuite `bash start.sh` : le terminal affiche « agenda Google ».

Ce que l'appli peut faire dans ton agenda : le code ne crée, ne modifie et ne supprime **que** ses propres
événements, reconnaissables à leur étiquette. Ils sont marqués « disponible » (pas de créneau bloqué) et
silencieux. Les jours de repos ne créent pas d'événement.

⚠ Tant que l'appli Google reste en mode Test, l'accès expire au bout de 7 jours : relance
`python -m coach.pilot google`.

## 2. Sans Strava : ton archive

Si la connexion Strava ne marche pas, l'écran d'accueil propose d'importer des fichiers FIT. Pour tout ton
historique d'un coup, demande ton archive : strava.com → Paramètres → Mon compte → Télécharger ou supprimer
votre compte → Demander une archive (mail au bout de quelques heures). Puis :

```bash
python -m coach.pilot import ~/Downloads/export_XXXXXXXX.zip
```

Relance l'application : l'écran d'accueil propose « Faire mon bilan ».

## 3. Vérifier en ligne de commande

```bash
python -m coach.pilot status     # forme, fatigue, ACWR, dernières sorties
```

## 4. Réglages (dans `~/.ai_coach/app.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `COACH_SESSION_HOUR` | 18 | heure des séances dans l'agenda |
| `COACH_HR_REST` / `COACH_HR_MAX` | 40 / 199 | fréquences cardiaques, pour le calcul de la charge |
| `COACH_LTHR` | 93 % de FCmax | FC au seuil |
| `COACH_TZ` | fuseau du Mac | fuseau des séances |
| `COACH_DAILY_BUDGET_EUR` | 1 | plafond de dépense Claude par jour |
| `COACH_PORT` | 8000 | port local de l'application |

## 5. Tout retirer

```bash
python -m coach.pilot clear-calendar    # supprime TOUS les événements du coach, et seulement eux
rm -rf ~/.ai_coach_app                  # efface les données de l'application, jetons Strava compris
rm ~/.ai_coach/app.env                  # efface tes réglages (code, clé Claude)
```


## Règles Strava

La connexion passe par l'API officielle de Strava, avec ton application en mode développeur (ton compte
seulement). Conditions de l'accord API : les données ne sont affichées qu'à toi et ne servent à entraîner
aucun modèle d'IA ; le coach les analyse uniquement pour te conseiller. Côté Strava, tu peux révoquer l'accès
à tout moment (Paramètres → Mes applications). Pour d'autres athlètes, le produit passera par Suunto.
