# Installer et tester l'application (v0.15)

L'application tourne sur ton Mac. Ton téléphone y accède en HTTPS via Tailscale, sans ouvrir ton Mac à Internet.
Au premier lancement, elle te guide comme un nouvel utilisateur :

1. **Connecter Strava** : un bouton, tu autorises chez Strava, tu reviens dans l'app.
2. **Ton bilan** : le coach lit jusqu'à 9 mois de sorties et t'écrit ce qu'il en retient (volume, terrain,
   séances de qualité, meilleure référence, récupération), puis te demande ta prochaine échéance.
3. **Ton plan, en discutant** : il propose un premier bloc. Tu réponds librement (« je me sens prêt à
   reprendre lundi », « plutôt 6 jours », « sortie longue le samedi », « moins de volume »…) : il recalcule
   et te repropose, jusqu'à ce que tu répondes « ça me va ». Rien n'est écrit avant.
4. **Au quotidien** : Aujourd'hui, Semaine, Coach (le chat), État.

Compte 25 minutes la première fois (5 pour Strava, 10 pour Tailscale), puis une commande à chaque lancement.

## 1. Créer ton application Strava (5 min, une fois)

C'est le rôle de l'éditeur de l'application (dans la vraie version, c'est nous) : déclarer l'application chez
Strava pour obtenir ses identifiants. L'athlète, lui, n'aura qu'un bouton « Connecter Strava ».

1. Va sur **strava.com/settings/api** (connecté à ton compte).
2. Remplis le formulaire :
   - **Application Name** : `AI Endurance Coach` ;
   - **Category** : `Training` ;
   - **Website** : `http://localhost` ;
   - **Authorization Callback Domain** : l'adresse Tailscale de ton Mac **sans** `https://`, par exemple
     `ton-mac.tailnet-xxxx.ts.net` (console Tailscale → Machines). Si tu n'as pas encore Tailscale, mets
     `localhost` : tu feras la connexion Strava depuis le Mac, puis tu changeras ce champ plus tard.
   - une image pour l'icône (n'importe laquelle).
3. Valide. Note le **Client ID** (un nombre) et le **Client Secret** (clic sur « afficher »).

Ton application est en « mode développeur » : elle ne peut être connectée qu'à **ton** compte, ce qui suffit.

## 2. Installer (5 min, une fois)

```bash
cd ~/Downloads && unzip -o ai_endurance_coach_v0.15.zip && cd ai_endurance_coach
bash install.sh
```

L'installation pose ses questions à la fin :

- un **code d'accès** de ton choix (8 caractères minimum) : il protège l'application ;
- ta **clé Claude** (console.anthropic.com → API Keys ; facturée à part de l'abonnement : 5 $ de crédit et
  une limite mensuelle suffisent). Sans clé, le coach marche en mode simplifié : bilan et plan identiques,
  mais il ne comprend que des phrases simples dans le chat ;
- ton **e-mail**, exigé par Apple pour les notifications (il reste sur ton Mac) ;
- le **Client ID** et le **Client Secret** Strava (§1).

Si tu avais installé la v0.11, seules les questions Strava sont posées. Les réponses sont rangées dans
`~/.ai_coach/app.env`, lisible par toi seul. Résultat attendu : `103 passed`.

## 3. Tailscale, pour le téléphone (10 min, une fois)

L'installation sur l'écran d'accueil et les notifications exigent HTTPS. Tailscale fournit un vrai certificat
et ne rend l'app visible qu'à **tes** appareils.

1. Installe Tailscale sur le Mac (tailscale.com/download) et sur l'iPhone (App Store), même compte.
2. Console Tailscale (login.tailscale.com) → **DNS** : active **MagicDNS** puis **HTTPS Certificates**.

`start.sh` publie ensuite l'application tout seul.

## 4. Lancer (à chaque fois)

```bash
cd ~/Downloads/ai_endurance_coach && bash start.sh
```

Le terminal affiche les deux adresses, par exemple :

```
  Sur ce Mac  : http://127.0.0.1:8000
  Téléphone   : https://ton-mac.tailnet-xxxx.ts.net
```

Le navigateur du Mac s'ouvre. Garde le terminal ouvert (le Mac ne se met pas en veille) ; Ctrl+C pour arrêter.

L'application démarre **neuve** : ses données sont dans `~/.ai_coach_app`, à part de tout ce qu'on a fait
avant. Aucun réglage de course n'est repris.

## 5. Installer l'app sur l'iPhone (2 min)

1. Tailscale activé sur l'iPhone, ouvre l'adresse « Téléphone » dans **Safari** et entre ton code.
2. **Partager → Sur l'écran d'accueil**. L'icône « Coach » apparaît. Ouvre l'app depuis l'icône.

## 6. Le parcours d'accueil

**Connecter Strava.** Strava s'ouvre : connecte-toi et **laisse cochées** les deux cases (voir tes activités,
y compris privées), puis Autoriser. Strava te renvoie vers une page « Strava connecté ✅ » ; reviens dans l'app.
Elle lit ton historique (une à deux minutes), puis bascule sur le chat.
Si Strava affiche « redirect_uri invalid » depuis l'iPhone, le champ « Authorization Callback Domain » (§1) ne
correspond pas à l'adresse Tailscale : corrige-le, ou fais cette étape depuis le Mac (http://127.0.0.1:8000).

**Le bilan** arrive dans le chat. Réponds à sa question avec tes échéances, comme tu le dirais à un coach :
*« la VVX 85 km, et le Triple des Vallées pour le plaisir ; plus tard un marathon sous 3 h »*.
Le coach cherche lui-même les dates, distances et D+ (recherche web par Claude), puis te les fait **confirmer**.
Si la recherche web n'est pas activée sur ton compte Anthropic (console → Settings), il utilise son catalogue de
courses ou te demande la date.

**Tes habitudes** (v0.15) : avant le plan, le coach pose six questions d'un coup, en s'appuyant sur ce que
Strava montre déjà (jours où tu ne cours jamais, séances de renfo, autres sports, part des sorties avec FC).
Réponds en un seul message, dans le désordre, par exemple :
*« 5 jours, repos le lundi, muscu en salle, vélo le mercredi et foot le vendredi, tout est sur Strava avec une
ceinture cardio, vacances du 21/12 au 27/12 sans courir »*. Ou « passe » pour les valeurs par défaut.

| Tu réponds | Le plan |
|---|---|
| jours de repos | aucune course ni renfo ces jours-là |
| renfo : salle / sans matériel / pas de renfo | 2 séances par semaine (force lourde + prévention), version salle ou maison ; 1 séance douce en récupération, 1 courte en semaine allégée ; rien si tu refuses (il te dit une fois pourquoi c'est utile) |
| autres sports et jours | affichés dans le plan ; sport intense (foot, tennis, crossfit…) : pas de séance dure ce jour-là ni le lendemain |
| Strava complet ou non | s'il manque des séances, il reste prudent sur le volume |
| ceinture ou poignet | au poignet : allure et effort ressenti pour le fractionné |
| vacances, semaines chargées | pas de course (« pas du tout ») ou semaine allégée (« un peu ») |

Le renfo se place après une séance de qualité (les jours durs restent durs), jamais la veille d'une séance dure,
jamais les 2 jours avant une course.

**La proposition** : un premier bloc, semaine par semaine, avec les séances clés et le renfo. Discute :

| Tu écris | Le coach |
|---|---|
| « Mes sorties longues sont en semaine, peu de temps le week-end (famille) » | sortie longue en semaine, week-end limité à 1 h sans séance dure |
| « J'aime le double seuil, je peux faire des doubles lundi, mardi, jeudi, vendredi, j'aime le gros volume » | double seuil (matin + soir) à partir de la 2e semaine d'intensité, footings du matin ces jours-là, volume en hausse par paliers |
| « Je me sens prêt à reprendre sérieusement dès lundi » | avance la reprise ; l'intensité reste au plus tôt 3 semaines après un effort de plus de 24 h, et il t'explique pourquoi |
| « 6 jours par semaine, sortie longue le samedi » | réorganise la semaine |
| « Moins de volume » / « environ 8 h par semaine » | ajuste le volume (plafonné selon ton historique) |
| « Un bloc de vitesse de 3 semaines » | change la durée du bloc (2 à 6 semaines) |
| « Je ne peux pas courir le 14/11 » | retire la séance de ce jour-là |
| « Je fais du foot le jeudi », « repos le lundi », « pas de renfo », « vacances du 21 au 28 décembre » | les mêmes réglages que les questions de l'accueil, à tout moment |
| une question (« pourquoi du 30/30 ? ») | répond, sans toucher au plan |
| « Ça me va » | écrit le plan : il apparaît dans Aujourd'hui et Semaine |

Les boutons sous le chat proposent les réponses courantes. « détails » sous un message montre ce que le code
a calculé (séances, date de reprise de l'intensité, contrôle de charge).

Pour refaire le test depuis le début : **État → Recommencer à zéro** (la connexion Strava est gardée).

## 7. Au quotidien

| Écran | Ce que tu fais |
|---|---|
| Aujourd'hui | lire la séance, **Valider la séance** : statut, effort ressenti (1-10), note. Coche « Prévenir le coach » si la note compte (douleur, fatigue). |
| Semaine | le plan, fait / prévu, le focus de la semaine ; toucher une séance pour le détail. |
| Coach | écrire comme à un coach. Un changement important du plan attend ton « oui ». **＋** importe un fichier FIT. (v0.21) **Micro** : touche-le et parle, touche ■ pour finir ; le texte arrive dans le champ, tu le corriges puis tu envoies avec ➤. La 1re fois, le téléphone demande l'accès au micro. |
| Progrès | (v0.19) estimation de ton temps sur tes objectifs et son évolution, forme / fatigue / fraîcheur (et ce que donnera le plan jusqu'au jour J), cumul sur 7 jours glissants, chronos estimés 5 km à marathon, efficacité en endurance, tes semaines, tout l'historique des activités. Période : 3 mois à 5 ans. Touche une courbe pour lire une valeur. (v0.20) Ton profil de coureur : un radar avec une note sur 10 par qualité, celle d'il y a 3 mois en pointillés, et pour chaque note sa source, sa fiabilité et quoi faire pour progresser. |
| État | forme, fatigue, ACWR, dernières sorties ; **Synchroniser** (Strava est aussi lu toutes les 2 h) ; **Activer les notifications**. |

Notifications (depuis l'icône de l'écran d'accueil, iOS 16.4 ou plus) : séance du jour à 7 h, relance à 21 h
si la séance n'est pas validée, nouvelle sortie Strava.

**Coût** : quelques centimes pour l'accueil, 1 à 2 centimes par message ensuite, plafond 1 € par jour
(`COACH_DAILY_BUDGET_EUR` dans `~/.ai_coach/app.env`).

## Dépannage

| Symptôme | Solution |
|---|---|
| `Lance d'abord : bash install.sh` | l'installation n'a pas été faite dans ce dossier |
| « trop d'essais » à la connexion | 5 codes faux : attendre une minute |
| « Strava n'est pas configuré sur ce serveur » | Client ID / Secret absents : `bash install.sh` puis relancer `bash start.sh` |
| Strava : « redirect_uri invalid » | §1 : « Authorization Callback Domain » = adresse Tailscale sans `https://` (ou passer par le Mac) |
| Strava : « Connexion Strava interrompue » | revenir dans l'app et réappuyer sur « Connecter Strava » ; laisser les deux cases cochées |
| « quota Strava atteint » | Strava limite à 100 lectures par 15 min : la synchro reprend toute seule plus tard |
| Strava : « connexion expirée » plus tard | État → **Reconnecter Strava** |
| Pas d'adresse « Téléphone » au lancement | Tailscale pas installé ou HTTPS non activé (§2) |
| Pas d'option « Sur l'écran d'accueil » | ouvrir dans Safari, pas dans une autre app |
| « Activer les notifications » ne fait rien | ouvrir l'app depuis l'icône ; Réglages → Notifications → Coach |
| Le coach ne comprend pas une phrase | sans clé Claude, écrire simplement (« reprendre lundi », « 6 jours », « ça me va ») |

## Ce que la v0.15 ne fait pas encore

- Après le premier bloc, la suite n'est pas encore proposée automatiquement : demande-la au coach dans le chat.
- Le bloc proposé cible la vitesse ; les autres points faibles (volume, endurance) ne sont pas encore différenciés.
- Les allures viennent d'une VMA estimée à partir de ta meilleure course : à confirmer par un test.
- Strava : l'application Strava est en mode développeur (un seul athlète, toi). Le produit passera par Suunto
  ou les fichiers de la montre.
- Google Agenda reste optionnel et se branche en ligne de commande ([GUIDE_PILOTE.md](GUIDE_PILOTE.md)).
- Pas de notes vocales (la dictée du téléphone marche dans le chat).
