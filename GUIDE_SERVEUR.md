# Mettre l'application en ligne — Oracle Cloud « Always Free » (gratuit)

Résultat : l'application tourne en permanence sur un serveur à Paris, avec une adresse HTTPS
(par exemple `https://141-145-12-34.sslip.io`), sans ton Mac ni Tailscale. Coût : 0 €, hors API Claude.

Compte 45 minutes la première fois.

## 1. Créer le compte Oracle Cloud (10 min)

1. Va sur **oracle.com/cloud/free** → **Start for free**.
2. Pays : France. Adresse e-mail, puis validation par le lien reçu.
3. **Home Region : France Central (Paris)**. ⚠ Ce choix est définitif : les ressources gratuites n'existent
   que dans cette région.
4. Carte bancaire : elle sert à vérifier ton identité. Le compte « Always Free » n'est pas débité tant que tu
   ne passes pas manuellement en compte payant.
5. Attends le mail « Your account is ready » (quelques minutes, parfois plus).

## 2. Créer le serveur (10 min)

Console Oracle → menu ☰ → **Compute → Instances → Create instance**.

1. **Name** : `coach`.
2. **Image and shape → Edit** :
   - Image : **Canonical Ubuntu 24.04** ;
   - Shape : **Ampere → VM.Standard.A1.Flex**, 2 OCPU, 12 Go (étiquette « Always Free-eligible »).
   - Si Oracle répond « Out of capacity » : réessaie plus tard, ou prends 1 OCPU / 6 Go, ou la forme
     **VM.Standard.E2.1.Micro** (AMD, 1 Go). La version Micro suffit pour un seul athlète.
3. **Networking** : laisse les valeurs par défaut (réseau créé automatiquement, adresse IP publique **oui**).
4. **Add SSH keys → Generate a key pair for me → Save private key**. Range le fichier :
   ```bash
   mv ~/Downloads/ssh-key-*.key ~/.ssh/coach.key && chmod 600 ~/.ssh/coach.key
   ```
5. **Create**. Une fois l'instance « Running », note son **Public IP address**.

## 3. Ouvrir le web (3 min)

Sur la page de l'instance → **Primary VNIC → Subnet** → **Security Lists → Default Security List →
Add Ingress Rules**, deux règles :

| Source CIDR | IP Protocol | Destination Port Range |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

(Le pare-feu interne du serveur est ouvert par le script de l'étape 4.)

## 4. Installer l'application (15 min)

Depuis ton Mac, envoie le zip sur le serveur puis connecte-toi (remplace l'IP) :

```bash
scp -i ~/.ssh/coach.key ~/Downloads/ai_endurance_coach_v0.17.zip ubuntu@IP_DU_SERVEUR:~
ssh -i ~/.ssh/coach.key ubuntu@IP_DU_SERVEUR
```

Puis, **sur le serveur** :

```bash
sudo apt-get update && sudo apt-get install -y unzip
unzip -o ai_endurance_coach_v0.17.zip && cd ai_endurance_coach
bash deploy/setup.sh
```

Le script installe Docker, ouvre les ports, propose une adresse gratuite (`IP-avec-tirets.sslip.io`, sans
inscription), pose les questions de réglage (code d'accès, clé Claude, Strava, e-mail), puis construit et
démarre l'application. À la fin : `✅ L'application est en ligne : https://….sslip.io`.

## 5. Strava (1 min)

strava.com/settings/api → **Authorization Callback Domain** = l'adresse affichée, **sans** `https://`
(par exemple `141-145-12-34.sslip.io`).

## 6. Utiliser

Ouvre l'adresse sur l'iPhone dans Safari → code d'accès → **Partager → Sur l'écran d'accueil**.
Puis le parcours habituel : Connecter Strava → bilan → échéances → plan.

## Au quotidien

| Besoin | Commande (sur le serveur, dans `~/ai_endurance_coach/deploy`) |
|---|---|
| Voir les journaux | `docker compose logs -f app` |
| Redémarrer | `docker compose restart app` |
| Mettre à jour | envoyer le nouveau zip, le décompresser au même endroit, puis `bash setup.sh` |
| Changer un réglage | `nano app.env` puis `docker compose up -d` |
| Sauvegarder maintenant | `docker compose exec -T app python -m coach.backup` |
| Voir les sauvegardes | `bash restore.sh` (liste) · journal : `cat /var/log/coach-backup.log` |
| Restaurer | `bash restore.sh coach-AAAAMMJJ-HHMM.tar.gz` (une copie de l'état actuel est faite avant) |

## Sauvegardes (v0.16)

Chaque nuit à 3 h 17, `setup.sh` programme une copie de toutes les données (bases SQLite copiées de façon cohérente,
jetons Strava, clés des notifications) :
- sur la VM, dans le volume, les 14 dernières ;
- et, si `COACH_BACKUP_URL` est renseigné dans `deploy/app.env`, dans un **bucket privé Object Storage** Oracle
  (inclus dans l'offre gratuite). L'URL est une « pre-authenticated request » en **écriture seule** : le serveur
  peut déposer des archives, pas les lire ni les lister. Pour récupérer une archive : console Oracle → Storage →
  Buckets → coach-backups → l'objet → Download, puis `scp` vers le serveur et `bash restore.sh fichier.tar.gz`.

Créer le bucket (une fois) : Storage → Buckets → Create bucket `coach-backups` (Standard, privé) → onglet
Pre-authenticated requests → Create : cible Bucket, accès « Permit object writes », expiration lointaine → copier
l'URL (affichée une seule fois) → la coller quand `setup.sh` la demande.

## Mises à jour automatiques (v0.17)

Après une installation avec `COACH_UPDATE_URL` renseigné, le serveur regarde toutes les 15 minutes le bucket privé
`coach-releases` (lien de lecture seule sur `releases/`). Quand une nouvelle version y est publiée : vérification de la
somme SHA-256, sauvegarde des données, copie du code (les réglages `deploy/app.env` ne sont jamais touchés),
reconstruction ; en cas d'échec, retour automatique à la version précédente. Journal : `/var/log/coach-update.log`.
Seul le propriétaire du compte Oracle peut publier dans ce bucket.

## Dictée (v0.21)

Le bouton micro du chat utilise la reconnaissance vocale du navigateur quand elle marche, sinon le serveur
transcrit la voix avec Whisper (modèle libre, sur le CPU, l'audio n'est jamais gardé ni envoyé ailleurs).
Au 1er démarrage, le serveur télécharge le modèle (~500 Mo, dans le volume `coach_models`, hors sauvegardes) :
la dictée serveur est prête quelques minutes après. Réglages facultatifs dans `deploy/app.env` :

| Réglage | Effet |
|---|---|
| `COACH_STT_MODEL=base` | plus rapide, un peu moins juste (défaut : `small`) |
| `COACH_STT=off` | pas de transcription sur le serveur (le bouton n'utilise que le navigateur) |

Voir si le modèle est prêt : `docker compose logs app | grep dictée`.

## Image de l'avatar par Gemini (v0.26, facultatif)

Sans réglage, l'avatar est un dessin simple. Pour une belle image générée par Gemini (« Nano Banana ») :
1. Crée une clé sur aistudio.google.com → Get API key (facturation Google à activer : ~0,04 $ par image).
2. Sur le serveur : `cd ~/ai_endurance_coach/deploy && nano app.env`, ajoute la ligne `GEMINI_API_KEY=ta_clé`,
   puis `docker compose up -d`.
Une image est créée quand l'avatar change (niveau, équipement, personnalisation), 4 par jour au plus
(`COACH_IMAGE_PER_DAY`), en gardant le même personnage. Seule la description de l'avatar est envoyée à Google.

## À savoir

- **Gratuit et permanent** tant que tu restes dans les ressources « Always Free ». Oracle peut récupérer
  une instance gratuite restée inactive plusieurs jours (CPU très bas) ; une app utilisée chaque jour
  n'est pas concernée.
- **Données** : le serveur est à Paris, mais Oracle est une société américaine. Ça convient pour un pilote sur
  tes propres données. Un produit qui héberge des données de santé d'autres personnes devra passer chez
  un hébergeur certifié HDS (OVHcloud, par exemple).
- **Sécurité** : seuls les ports 80/443 (via Caddy) et SSH (par clé) sont ouverts ; l'application n'est
  pas exposée directement ; les secrets sont dans `deploy/app.env` (droits 600).
