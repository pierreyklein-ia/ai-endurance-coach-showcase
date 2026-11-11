# Guide de test — AI Endurance Coach v0.8 (Mac)

> Tes vraies données, ton téléphone et ton Google Agenda : voir **GUIDE_PILOTE.md**.
> Ce guide-ci reste sur l'athlète fictif (0 €, rien ne sort de ton Mac).

Parcours conseillé : étapes 0 → 1 → 2 → 3. Les étapes 4 à 7 sont optionnelles.
Tout est gratuit sauf l'étape 6 (vrai Claude, ~2 centimes par échange).

## 0. Installation ou mise à jour (~3 min)

⚠ Le `python3` livré avec macOS est en **3.9**, trop ancien. Installe d'abord
Python 3.12 : `brew install python@3.12`, ou l'installeur macOS de python.org.

```bash
cd ~/Downloads
unzip -o ai_endurance_coach_v0.8.zip        # -o : remplace l'ancienne version
cd ai_endurance_coach
bash install.sh                             # trouve le bon Python, installe, lance les tests
source .venv/bin/activate                   # à refaire dans chaque nouveau Terminal
```

Attendu à la fin : `77 passed`.

## 1. Les tests automatiques (20 s)

```bash
python -m pytest -q
```

Ils couvrent le graphe de conversation, les garde-fous (dont ACWR 1,5 / 1,7), le
tri des messages, le Race Pacer, le parcours, le calibrage et
l'appel Claude (avec un faux serveur). Pour voir le détail d'un bloc :
`python -m pytest -v tests/test_guardrails.py`.

## 2. Discuter avec le coach (athlète fictif, 0 €)

```bash
python -m coach.demo
```

Tape `/aide` pour les 8 scénarios (bruit, affûtage, nuit blanche, genou avec
confirmation, rattrapage refusé, urgence, **soirée arrosée, stress conjugal**).
`/reset` entre deux scénarios.

Nouveau en v0.7 — tes deux messages du 30/09 donnent maintenant :

| Message | Réponse attendue |
|---|---|
| « soirée jusque tard avec beaucoup d'alcool… » | pas d'intensité pendant 36 h : les 2 prochaines séances passent en Z1 ≤ 40 min |
| « seuil de patience avec ma femme… » | une phrase d'empathie, « je ne suis pas le mieux placé » pour le couple, proposition de remplacer le Seuil par un footing relâché (confirmation oui/non) |

Les garde-fous R07 (récupération) et R08 (stress de vie) affichent ⚠ si le coach
garde une séance intense malgré tout ; ils deviendront bloquants quand tu auras
validé leurs seuils.

## 3. Race Pacer : TA prévision Diag 2026 (sur ton profil réel)

**a. La reproduire** (≈ 10 s) :

```bash
python -m coach.pacer.predict
```

Le programme ne te demande rien : il prend le parcours et les 35 points de
passage dans `data/races/`, contrôle distance et D+, estime ta FC tenable (point
fixe FC ↔ durée) et applique ton arrêt moyen mesuré. Attendu :
`FC tenable … 140.4 bpm` et `Arrivée Saint-Denis … 30.6h 36.2h 43.7h … Sat 10:13`.

**b. Vérifier que la prévision gelée du 30/09 n'a pas été retouchée** :

```bash
python -m coach.pacer.freeze experiments/strava_perso/prediction_diag2026.json
```

Attendu : `✅ intacte — sha256 recalculée : 7c901e90ddbff9e6…`.
Pour voir la protection en action, change un chiffre dans une **copie** du
fichier et relance la commande sur la copie : `❌ MODIFIÉE`.

**c. Jouer les scénarios** :

```bash
python -m coach.pacer.predict --target-hr 130 --out scenario_130.json   # ton estimation
python -m coach.pacer.predict --stop-min 8 --out scenario_arrets.json    # arrêts plus longs
```

Attendu (arrivée médiane) : ≈ 38,4 h à 130 bpm ; ≈ 37,4 h avec 8 min par ravito.

## 4. Recalibrer ton profil avec tes propres fichiers (optionnel)

1. App Suunto → une activité → ⋯ → export **FIT**. Mets dans un dossier
   `mes_sorties_reunion/` des sorties **sur terrain réunionnais** (c'est ce qui
   compte pour la Diag).
2. Puis :

```bash
python -m coach.pacer.calibrate --activities mes_sorties_reunion/ --out data/athletes/moi.json \
  --hr-rest 40 --hr-max 199 --race-effort 3.3:176.8 --race-effort 13.1:155.8 --backtest
python -m coach.pacer.predict --athlete data/athletes/moi.json --out ma_prevision.json
```

`--race-effort durée_h:FC` = tes courses menées à fond (ici : marathon, UTOI).

## 5. Sans rien installer : via Claude + ton connecteur Strava

Dans une conversation de ce projet (connecteur Strava activé), demande par exemple :

- « Recalibre mon profil Race Pacer avec mes sorties Réunion et compare à la prévision gelée »
- « Montre-moi mon ACWR et ma forme des 4 dernières semaines »
- Après la course : « Récupère ma Diag sur Strava et fais le backtest de la prévision gelée »

Usage personnel uniquement (règles Strava) : c'est le mode POC, pas le produit.

## 6. Le coach avec le vrai Claude (~2 centimes par échange)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."     # clé sur la console Anthropic, avec une limite de dépense
python -m coach.demo --claude
```

Testé avec un serveur simulé seulement : en cas d'erreur, copie-la-moi.

## 7. Sur ton téléphone

Utilise l'application : [GUIDE_APP.md](GUIDE_APP.md).

## Ce qui est encore simulé

- Conversation (étapes 2 et 6) : athlète et plan **fictifs** ; agenda en mémoire
  (Google Calendar pas encore branché) ; pas de vocaux ; pas de persistance.
- Race Pacer (étape 3) : **tes vraies données**, mais nuit (−8 %), sommeil, chaleur
  et météo sont des hypothèses ou non modélisés.
- Recherche automatique du parcours : catalogue local. La source Trace de Trail
  exige un accord pour un usage commercial (voir la revue d'architecture).

## Dépannage

| Symptôme | Solution |
|---|---|
| `No matching distribution found for langgraph…` | Python 3.9 de macOS → installer Python 3.12, puis `bash install.sh` |
| `command not found: python` / `ModuleNotFoundError` | `source .venv/bin/activate` dans ce Terminal |
| `diagonale des fous 2026 absente du catalogue` | lancer la commande depuis le dossier `ai_endurance_coach` |
| `ANTHROPIC_API_KEY absente` | `export ANTHROPIC_API_KEY=...` dans le même Terminal |
