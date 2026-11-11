"""Mode pilote : TES vraies données, TON agenda Google, TON téléphone.

Un seul athlète (toi), tout tourne sur ton Mac. Données dans ~/.ai_coach/
(jamais dans le dossier du projet, jamais dans le zip).

Sources d'activités conformes à la politique API Strava (juin 2026) :
- connecteur MCP officiel de Strava : un abonné peut y brancher « sa propre
  application d'IA » pour SES données, en usage personnel (§3.5, exception au §5.3) ;
- archive de ton compte Strava (export RGPD : tes fichiers) ;
- fichiers FIT envoyés au bot Telegram (app Suunto -> Partager).
L'API REST Strava classique est volontairement absente (§5.3 : pas d'usage IA),
et rien de tout ça ne vaut pour d'autres athlètes : le produit passera par Suunto.
"""
