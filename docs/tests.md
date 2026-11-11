# Tests : unitaires, composants, interface (v0.50)

Trois étages, tous lancés par la CI (`.github/workflows/ci.yml`) avant tout déploiement :

| Étage | Outil | Où | Ce qui est vérifié |
|---|---|---|---|
| Backend | pytest + pytest-cov | `tests/` | logique métier (garde-fous, charge, plan, simulateur, carrière…), API web, synchro Strava, bots, sauvegarde |
| Frontend | Vitest + Testing Library (jsdom) | `frontend/src/**/*.test.ts(x)` | aides pures de `src/lib`, composants (cartes, feuilles, glisser-déposer, dictée) |
| Interface | Playwright (Chromium) | `e2e/` | parcours complets sur téléphone (Pixel 7) et ordinateur, contre un serveur de test déterministe |

## Lancer les tests

```bash
# backend (à la racine du dépôt)
pip install -r requirements-dev.txt
python -m pytest -q                                  # tous les tests
python -m pytest -q --cov                            # + couverture (lignes et branches), config dans pyproject.toml
python -m pytest -q --cov --cov-report=html          # rapport navigable dans htmlcov/index.html
ruff check .

# frontend
cd frontend
npm ci
npx tsc --noEmit
npx vitest run                                       # tests
npx vitest run --coverage                            # + couverture de src/lib et src/components (rapport : frontend/coverage/)
npm run build

# interface (l'interface doit être construite : npm run build dans frontend/)
cd e2e
npm ci
npx playwright install --with-deps chromium          # une fois (inutile si Chromium est déjà installé)
PYTHON=../.venv/bin/python npx playwright test       # lance lui-même e2e/server.py
npx playwright test --project telephone --headed     # voir le navigateur, un seul appareil
npx playwright show-report                           # rapport HTML après un échec (captures, traces)
```

Seuils de couverture (la CI échoue en dessous) : backend 93 % (lignes + branches, `--cov-fail-under`),
frontend 94 % des lignes (`frontend/vite.config.ts`, `test.coverage.thresholds`). Ils sont réglés un peu sous le
niveau atteint : on peut les remonter, jamais les baisser sans raison.

Ne comptent pas dans la couverture backend : les points d'entrée en ligne de commande (`coach/*/__main__.py`,
`coach/demo.py`, `coach/pacer/cli.py`) et l'OAuth de bureau Google (`coach/adapters/google_auth.py`, ouvre un
navigateur). Côté frontend, les vues (`src/views`) sont couvertes par les tests d'interface plutôt que par Vitest.

## Le serveur de test de l'interface (`e2e/server.py`)

- coach hors ligne (`HeuristicLLM` : 0 €, réponses reproductibles), date du jour figée (`TODAY`), faux Google
  Agenda, faux Strava ; aucune clé, aucun réseau ;
- deux états préparés une fois au démarrage : `fresh` (nouvel athlète) et `onboarded` (accueil terminé, plan en
  place, objectif VVX 85 km, une sortie longue aujourd'hui, un footing hier à lier) ;
- `POST /__e2e/reset?state=fresh|onboarded` remet l'application dans cet état : chaque test part du même point
  (la fixture `app` de `e2e/tests/fixtures.ts` le fait, fige l'horloge du navigateur et se connecte) ;
- chaque test échoue s'il y a une erreur dans la console (exception, violation de la CSP…).

Parcours couverts : connexion, accueil complet jusqu'au premier plan, validation d'une séance avec le bilan
nutrition, glisser-déposer et bouton « Déplacer » dans Semaine, lier / délier une sortie, chat (indicateur « … écrit »),
Progrès et Œil du coach (analyse offerte), État, simulateur (survol du profil, fiche d'un ravito, dépôt d'un GPX
synthétique), Carrière (éditeur d'avatar : lieu et ambiance), Réglages (thème, volume maximum, « Tester Claude »),
bouton « retour » d'Android qui ferme la fiche ouverte, pas de défilement horizontal.

## Inversion de contrôle : rendre le code testable

### L'idée

Un module qui va chercher lui-même ses dépendances (l'heure avec `datetime.now()`, une variable avec
`os.environ`, le réseau avec `httpx`, un fil d'exécution avec `threading.Thread`) est difficile à tester : le test
ne contrôle ni l'heure, ni l'environnement, ni la réponse du serveur. **L'inversion de contrôle** (IoC) renverse
le sens : le module *reçoit* ses dépendances de l'extérieur. En production, on lui passe les vraies ; en test, des
remplaçants simples et déterministes.

### Comment on le fait en Python

Python n'a pas besoin de « conteneur » d'injection : trois techniques suffisent, de la plus simple à la plus formelle.

1. **Paramètre avec valeur par défaut** (le plus courant ici) : la valeur par défaut est la vraie dépendance, donc
   les appels existants ne changent pas.

   ```python
   def load_settings(env: Mapping[str, str] | None = None, repo_root: Path | None = None) -> PilotSettings:
       env = os.environ if env is None else env          # production : os.environ ; test : un simple dict
   ```

   ```python
   s = load_settings({"COACH_TZ": "UTC", "COACH_LTHR": "170"}, repo_root=tmp_path)    # dans le test
   ```

2. **Injection par le constructeur** : l'objet garde la dépendance (`self.clock`, `self.http`…).

   ```python
   class Throttle:
       def __init__(self, max_fail=5, lock_s=60, clock: Callable[[], float] = time.time):
           self.clock = clock
   ```

   Le test passe une horloge qu'il fait avancer à la main : pas de `sleep(60)` pour tester un blocage d'une minute.

3. **`typing.Protocol`** pour décrire ce qu'on attend d'une dépendance sans imposer de classe mère (typage
   « structurel », comme une interface) : tout objet qui a les bonnes méthodes convient, y compris un faux. Exemple
   existant : `CourseSource` et `WebSearchPort` dans `coach/pacer/course.py`.

### Faux (fakes) ou simulacres (mocks) ?

- **Faux** : une petite implémentation qui marche vraiment, en mémoire (`FakeStravaHTTP`, `Svc` pour Google Agenda,
  `FakeWhisper`, `FakeHttp` pour Gemini, le faux `fetch` de `frontend/src/test/fakes.ts`). C'est le choix par
  défaut : le test vérifie un *résultat* (ce qui est enregistré, affiché, renvoyé), pas une suite d'appels.
- **Mocks** (`unittest.mock`, `monkeypatch`, `vi.fn`) : pour vérifier qu'un appel a eu lieu, ou remplacer
  ponctuellement une méthode qui doit échouer (ex. les erreurs de service → codes HTTP dans
  `tests/test_unit_api_errors.py`). À garder rares : ils lient le test aux détails de l'implémentation.

### Ce qui a été injecté en v0.50 (comportement de production inchangé)

| Module | Dépendance injectée | Valeur par défaut |
|---|---|---|
| `coach/pilot/config.py` | `env` (variables), `localtime` (chemin), `repo_root` | `os.environ`, `/etc/localtime`, racine du dépôt |
| `coach/web/auth.py` | `Throttle(clock=…)`, `load_secret(env=…)` | `time.time`, `os.environ` |
| `coach/web/push.py` | `pywebpush_sender(env=…, webpush=…, error=…)` | `os.environ`, `pywebpush.webpush`, `WebPushException` |
| `coach/backup.py` | `main(env=…, http=…)` | `os.environ`, `httpx.put` |
| `coach/stt.py` | `Transcriber(env=…, model_factory=…, spawn=…)` | `os.environ`, faster-whisper, fil de fond |
| `coach/adapters/telegram_bot.py` | `CoachBot(sleep=…)` | `time.sleep` |
| `coach/pilot/bot.py` | `PilotBot(clock=…, sleep=…)` | `time.time`, `time.sleep` |
| `coach/pilot/strava_mcp.py` | `_wait_for_callback(port=…, clock=…)`, `StravaMCP(open_browser=…)`, `sync_strava(now=…)` | port 8765, `time.time`, `webbrowser.open`, `datetime.now` |
| `coach/pilot/strava_api.py` | `StravaAPI(clock=…)`, `StravaAPIConnector(clock=…)`, `sync_strava_api(now=…)` | `time.time`, `datetime.now` |
| `coach/pacer/predict.py` | `main(root=…)` (catalogue et profil) | racine du dépôt |

Déjà injectables avant : `today_fn` (`build_pilot`), `http` (`TelegramAPI`, `StravaAPI`), `client` (`avatar_image`,
`race_lookup.web_search`), `strava_factory` et `connector` (`CoachService`), `calendar_service` (Google Agenda).

Côté frontend, même principe : les composants appellent `api()` qui appelle `fetch` ; les tests remplacent `fetch`
(`stubFetch` dans `frontend/src/test/fakes.ts`), `SpeechRecognition`, `MediaRecorder` ou `PointerEvent` sur
`window` avant de rendre le composant, sans modifier son code.

### Une règle simple pour la suite

Quand un test est difficile à écrire parce qu'une fonction lit l'heure, l'environnement, un fichier ou le réseau :
ajouter un paramètre (avec la vraie dépendance par défaut) plutôt que de « patcher » le module dans le test.
