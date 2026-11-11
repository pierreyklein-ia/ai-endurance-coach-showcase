"""Base de connaissances de l'entraînement (v0.13) : fiches de synthèse sourcées.

Ce sont des SYNTHÈSES rédigées pour le projet (pas des citations) : chaque fiche donne la référence
complète de l'article pour vérification, et reste prudente sur le niveau de preuve. À relire par un
expert avant tout usage hors pilote (statut `a_relire`).

Usage : le nœud RAG du graphe et l'accueil d'un nouvel athlète injectent les fiches pertinentes
dans le contexte du coach, qui doit appuyer ses explications dessus (et citer l'auteur).
"""
from __future__ import annotations

import re

from .rag import Evidence

K = "synthese"

KNOWLEDGE = [
    Evidence("kb_polarise_seiler2010",
             "Seiler S. What is best practice for training intensity and duration distribution in endurance "
             "athletes? Int J Sports Physiol Perform. 2010;5(3):276-291.",
             "Synthèse : chez les athlètes d'endurance entraînés, la majorité du volume (de l'ordre de 75 à 80 % "
             "des séances) se fait à basse intensité, sous le premier seuil, et le reste à haute intensité ; "
             "environ deux séances intenses par semaine est une organisation fréquente. Trop de temps dans la "
             "zone intermédiaire « ni facile ni dur » accumule de la fatigue sans bénéfice proportionnel.",
             (), ("intensity_distribution", "speed", "volume", "planning")),
    Evidence("kb_polarise_stoggl2014",
             "Stöggl T, Sperlich B. Polarized training has greater impact on key endurance variables than "
             "threshold, high intensity, or high volume training. Front Physiol. 2014;5:33.",
             "Synthèse : sur 9 semaines, chez des athlètes d'endurance entraînés, une distribution polarisée "
             "(beaucoup de basse intensité + des séances très intenses, peu de seuil) a davantage amélioré "
             "VO2max et performance que des programmes centrés sur le seuil, le volume seul ou la haute intensité "
             "seule. Étude courte, petits effectifs : tendance, pas une loi.",
             (), ("intensity_distribution", "speed", "threshold", "volume")),
    Evidence("kb_double_seuil_casado2023",
             "Casado A, Foster C, Bakken M, Tjelta LI. Does lactate-guided threshold interval training within a "
             "high-volume low-intensity approach represent the 'next step' in the evolution of distance running "
             "training? Int J Environ Res Public Health. 2023;20(5):3782.",
             "Synthèse : le « modèle norvégien » associe un gros volume à basse intensité et du travail au seuil "
             "fractionné, souvent en double seuil (deux séances le même jour, matin et soir), à intensité "
             "contrôlée (lactate modéré, effort qui reste confortable) avec des récupérations courtes. L'idée : "
             "accumuler beaucoup de temps au seuil sans la fatigue d'une séance très dure. Preuves surtout "
             "descriptives (pratiques d'élites) ; à doser prudemment chez l'amateur et à ne pas faire après "
             "une période de récupération sans transition.",
             (), ("double_threshold", "threshold", "volume", "doubles")),
    Evidence("kb_30_30_billat2001",
             "Billat LV. Interval training for performance: a scientific and empirical practice. Special "
             "recommendations for middle- and long-distance running. Part I: aerobic interval training. "
             "Sports Med. 2001;31(1):13-31.",
             "Synthèse : les intervalles courts du type 30 s à vitesse maximale aérobie / 30 s de récupération "
             "active (« 30/30 ») permettent de passer un temps important près de VO2max avec une contrainte "
             "musculaire modérée ; c'est une porte d'entrée classique pour travailler la VMA.",
             (), ("speed", "vma", "intervals")),
    Evidence("kb_hiit_buchheit2013",
             "Buchheit M, Laursen PB. High-intensity interval training, solutions to the programming puzzle. "
             "Part I: cardiopulmonary emphasis. Sports Med. 2013;43(5):313-338.",
             "Synthèse : le format des intervalles (durée, intensité, récupération) détermine le temps passé près "
             "de VO2max et la charge neuromusculaire ; les intervalles courts et les séries permettent de "
             "progresser par le nombre de répétitions, ce qui rend la progression d'une semaine à l'autre "
             "facile à doser.",
             (), ("speed", "vma", "intervals", "planning")),
    Evidence("kb_ultra_millet2011",
             "Millet GY, Tomazin K, Verges S, et al. Neuromuscular consequences of an extreme mountain "
             "ultra-marathon. PLoS One. 2011;6(2):e17059.",
             "Synthèse : après un ultra-trail de montagne de plus de 160 km, la force maximale des extenseurs du "
             "genou et des fléchisseurs plantaires chute fortement (de l'ordre d'un tiers), avec une composante "
             "centrale (nerveuse) importante, et les marqueurs de dommages musculaires et d'inflammation sont "
             "très élevés. La récupération complète demande du temps, même quand les sensations reviennent vite.",
             (), ("recovery_ultra", "ultra", "recovery")),
    Evidence("kb_ultra_saugy2013",
             "Saugy J, Place N, Millet GY, Degache F, Schena F, Millet GP. Alterations of neuromuscular function "
             "after the world's most challenging mountain ultra-marathon. PLoS One. 2013;8(6):e65596.",
             "Synthèse : sur un ultra de plus de 300 km, la fatigue neuromusculaire mesurée à l'arrivée était "
             "moindre que sur des ultras plus courts et plus rapides : l'intensité de course pèse autant que la "
             "distance. La fatigue d'un ultra dépend donc du format et de l'allure, d'où une reprise adaptée "
             "au cas par cas.",
             (), ("recovery_ultra", "ultra", "recovery")),
    Evidence("kb_lignes_droites_paavolainen1999",
             "Paavolainen L, Häkkinen K, Hämäläinen I, Nummela A, Rusko H. Explosive-strength training improves "
             "5-km running time by improving running economy and muscle power. J Appl Physiol. "
             "1999;86(5):1527-1533.",
             "Synthèse : chez des coureurs entraînés, remplacer une partie du volume par du travail explosif "
             "(sprints courts, sauts) a amélioré l'économie de course et le temps sur 5 km sans changer VO2max. "
             "Les lignes droites et sprints en côte courts entretiennent cette qualité à faible coût de fatigue.",
             (), ("speed", "strides", "economy")),
    Evidence("kb_detraining_mujika2000",
             "Mujika I, Padilla S. Detraining: loss of training-induced physiological and performance "
             "adaptations. Part I: short term insufficient training stimulus. Sports Med. 2000;30(2):79-87.",
             "Synthèse : quelques jours à deux semaines de réduction ou d'arrêt n'effacent qu'une petite partie "
             "des adaptations ; la VO2max baisse progressivement au-delà. Rater quelques séances ne justifie donc "
             "pas de « rattraper » : on reprend le plan.",
             ("R05_catch_up",), ("missed_sessions", "detraining", "recovery")),
    Evidence("kb_affutage_bosquet2007",
             "Bosquet L, Montpetit J, Arvisais D, Mujika I. Effects of tapering on performance: a meta-analysis. "
             "Med Sci Sports Exerc. 2007;39(8):1358-1365.",
             "Synthèse : l'affûtage le plus efficace dure environ deux semaines, réduit le volume d'environ "
             "40 à 60 % en gardant l'intensité et la fréquence des séances. Décharger trop longtemps ou trop "
             "fort fait perdre une partie du bénéfice.",
             (), ("taper", "planning", "race")),
    Evidence("kb_acwr_impellizzeri2020",
             "Impellizzeri FM, Tenan MS, Kempton T, Novak A, Coutts AJ. Acute:chronic workload ratio: conceptual "
             "issues and fundamental pitfalls. Int J Sports Physiol Perform. 2020;15(6):907-913.",
             "Synthèse : le ratio charge aiguë / charge chronique (ACWR) a des limites conceptuelles et "
             "statistiques ; il ne prédit pas les blessures de façon fiable. Dans l'application il sert de "
             "garde-fou grossier contre les hausses brutales de charge, pas de vérité.",
             ("R03_acwr",), ("load", "acwr", "volume")),
    Evidence("kb_riegel1981",
             "Riegel PS. Athletic records and human endurance. American Scientist. 1981;69(3):285-290.",
             "Synthèse : le temps sur une distance plus longue se prédit approximativement par T2 = T1 × "
             "(D2/D1)^1,06 ; chez l'amateur l'exposant est souvent un peu plus élevé, surtout au marathon. Un "
             "écart important avec la prédiction signale un manque d'endurance de vitesse.",
             (), ("marathon", "prediction", "speed")),
    # --- v0.51 : pente, vent et lecture de la FC pendant les séances d'intensité
    Evidence("kb_pente_minetti2002",
             "Minetti AE, Moia C, Roi GS, Susta D, Ferretti G. Energy cost of walking and running at extreme "
             "uphill and downhill slopes. J Appl Physiol. 2002;93(3):1039-1046.",
             "Synthèse : le coût énergétique de la course augmente fortement avec la pente : en montée, à effort "
             "égal, l'allure baisse nettement (quelques secondes par kilomètre par pour cent de pente sur les "
             "pentes douces, bien plus au-delà). En descente douce le coût diminue, jusqu'à un minimum vers -10 à "
             "-20 %, puis remonte car il faut freiner. C'est la base des allures « ajustées à la pente » (GAP). "
             "Conséquence pratique : sur un parcours vallonné, une répétition de seuil se pilote à la FC et à "
             "l'effort, pas à l'allure ; comparer les répétitions avec le GAP plutôt qu'avec l'allure brute.",
             (), ("terrain", "threshold", "trail")),
    Evidence("kb_vent_pugh1971",
             "Pugh LGCE. The influence of wind resistance in running and walking and the mechanical efficiency "
             "of work against horizontal or vertical forces. J Physiol. 1971;213(2):255-276. "
             "Davies CTM. Effects of wind assistance and resistance on the forward motion of a runner. "
             "J Appl Physiol. 1980;48(4):702-709.",
             "Synthèse : la résistance de l'air pèse quelques pour cent de la dépense à allure de seuil, et elle "
             "croît avec le carré de la vitesse relative de l'air : un vent de face soutenu peut coûter de "
             "l'ordre de 10 à 20 s/km à effort égal. Le vent dans le dos rend moins que ce que le vent de face "
             "coûte (environ la moitié) : sur un aller-retour venté, le temps total est plus lent qu'à l'abri. "
             "Conséquence pratique : par vent fort, garder la FC et l'effort cibles, accepter une allure plus "
             "lente face au vent, ne pas compenser dos au vent ; s'abriter ou choisir un parcours protégé pour "
             "les séances à allure précise. Ordres de grandeur, variables selon le gabarit et la vitesse.",
             (), ("terrain", "threshold")),
    Evidence("kb_fc_retard_derive",
             "Coyle EF, González-Alonso J. Cardiovascular drift during prolonged exercise: new perspectives. "
             "Exerc Sport Sci Rev. 2001;29(2):88-92.",
             "Synthèse : en début d'effort la FC met 1 à 3 minutes à se stabiliser (la moyenne d'une répétition "
             "de 8 min sous-estime donc la FC atteinte en fin de bloc) ; sur les efforts longs, surtout à la "
             "chaleur et avec la déshydratation, elle dérive vers le haut à allure constante (dérive cardiaque). "
             "Conséquence pratique : juger une répétition de seuil sur la FC des dernières minutes, pas sur la "
             "moyenne ; une FC qui monte à allure stable en fin de séance signale fatigue, chaleur ou "
             "déshydratation plutôt qu'un manque de forme.",
             (), ("terrain", "hr", "threshold", "heat")),
    # --- v0.15 : renforcement, prévention, autres sports, récupération, mesure de la FC
    Evidence("kb_blessures_lauersen2014",
             "Lauersen JB, Bertelsen DM, Andersen LB. The effectiveness of exercise interventions to prevent "
             "sports injuries: a systematic review and meta-analysis of randomised controlled trials. "
             "Br J Sports Med. 2014;48(11):871-877.",
             "Synthèse : sur 25 essais randomisés (plus de 26 000 participants, tous sports), le renforcement "
             "musculaire est l'intervention la plus protectrice : il réduit le risque de blessure d'environ deux "
             "tiers, et le travail proprioceptif aide aussi ; les étirements, eux, n'ont pas montré d'effet. "
             "Ce sont des données tous sports confondus, pas spécifiques à la course.",
             (), ("strength", "injury")),
    Evidence("kb_force_blagrove2018",
             "Blagrove RC, Howatson G, Hayes PR. Effects of strength training on the physiologic determinants and "
             "performance of middle- and long-distance running: a systematic review. Sports Med. "
             "2018;48(5):1117-1149.",
             "Synthèse : ajouter 2 à 3 séances de force par semaine (charges lourdes, pliométrie, ou les deux) "
             "pendant 6 à 14 semaines améliore l'économie de course de l'ordre de 2 à 8 % et souvent la "
             "performance, sans faire perdre de VO2max ni prendre de masse gênante ; une séance par semaine suffit "
             "ensuite à entretenir.",
             (), ("strength", "economy", "speed")),
    Evidence("kb_force_ronnestad2014",
             "Rønnestad BR, Mujika I. Optimizing strength training for running and cycling endurance performance: "
             "a review. Scand J Med Sci Sports. 2014;24(4):603-612.",
             "Synthèse : la force la plus utile au coureur est lourde (charges proches de 4 à 10 répétitions "
             "maximales), avec une intention de vitesse en phase concentrique, sur des exercices des membres "
             "inférieurs ; 2 séances par semaine pour progresser, 1 pour entretenir. Pour limiter l'interférence, "
             "on évite la force juste avant une séance clé et on la place après l'endurance ou un autre jour.",
             (), ("strength", "planning")),
    Evidence("kb_trail_descente_giandolini2016",
             "Giandolini M, Vernillo G, Samozino P, et al. Fatigue associated with prolonged graded running. "
             "Eur J Appl Physiol. 2016;116(10):1859-1873.",
             "Synthèse : la course en descente impose un travail excentrique important qui génère plus de dommages "
             "musculaires et de fatigue que le plat ou la montée ; préparer les quadriceps et les mollets à "
             "l'excentrique (renforcement, descentes progressives) fait partie de la préparation au trail.",
             (), ("strength", "trail", "injury")),
    Evidence("kb_charge_foster2001",
             "Foster C, Florhaug JA, Franklin J, et al. A new approach to monitoring exercise training. "
             "J Strength Cond Res. 2001;15(1):109-115.",
             "Synthèse : la charge d'une séance peut s'estimer par la durée multipliée par l'effort ressenti "
             "(RPE de 0 à 10, noté environ 30 min après) ; la méthode est cohérente avec les mesures fondées sur "
             "la fréquence cardiaque et s'applique à toutes les activités, ce qui permet de compter les autres "
             "sports et les séances sans cardio dans la charge totale.",
             (), ("load", "cross_training", "hr")),
    Evidence("kb_cardio_gillinov2017",
             "Gillinov S, Etiwy M, Wang R, et al. Variable accuracy of wearable heart rate monitors during aerobic "
             "exercise. Med Sci Sports Exerc. 2017;49(8):1697-1703.",
             "Synthèse : comparée à l'électrocardiogramme, la ceinture thoracique est la plus précise ; les "
             "capteurs au poignet sont corrects au repos et à allure stable mais leur précision varie et se "
             "dégrade quand l'intensité change vite (fractionné). Avec une montre au poignet, on se cale sur "
             "l'allure et l'effort ressenti pour les séances courtes et intenses.",
             (), ("hr",)),
    Evidence("kb_recuperation_kellmann2018",
             "Kellmann M, Bertollo M, Bosquet L, et al. Recovery and performance in sport: consensus statement. "
             "Int J Sports Physiol Perform. 2018;13(2):240-245.",
             "Synthèse : la récupération fait partie de l'entraînement ; le sommeil, les jours de repos et les "
             "semaines plus légères sont à planifier et à individualiser, et le stress hors sport (travail, "
             "famille, voyages) pèse sur la récupération. On adapte la charge aux semaines chargées plutôt que de "
             "les subir.",
             (), ("recovery", "rest_days", "busy_weeks", "cross_training")),
    # ---------------------------------------------------------------- v0.22 : force (suite), sommeil, stress, nutrition
    Evidence("kb_force_storen2008",
             "Støren Ø, Helgerud J, Støa EM, Hoff J. Maximal strength training improves running economy in distance "
             "runners. Med Sci Sports Exerc. 2008;40(6):1087-1092.",
             "Synthèse : 8 semaines de demi-squat lourd (4 séries de 4 répétitions, ~85-90 % du maximum, montée "
             "aussi rapide que possible), 3 fois par semaine, ont amélioré l'économie de course d'environ 5 % et "
             "le temps d'épuisement à la vitesse aérobie maximale, sans prise de poids ni baisse de VO2max. "
             "Petit échantillon (17 coureurs bien entraînés).",
             (), ("strength", "economy", "squat")),
    Evidence("kb_force_llanoslagos2024",
             "Llanos-Lagos C, Ramirez-Campillo R, Moran J, Sáez de Villarreal E. Effect of strength training "
             "programs in middle- and long-distance runners' economy at different running speeds: a systematic "
             "review with meta-analysis. Sports Med. 2024.",
             "Synthèse : la force améliore l'économie de course des coureurs de demi-fond et de fond, avec des "
             "effets plus nets pour la force lourde (charges élevées, peu de répétitions) et pour les programmes "
             "combinés (lourd + pliométrie) ; le gain se voit surtout aux allures rapides.",
             (), ("strength", "economy", "contrast")),
    Evidence("kb_contraste_seitz2016",
             "Seitz LB, Haff GG. Factors modulating post-activation potentiation of jump, sprint, throw, and "
             "upper-body ballistic performances: a systematic review with meta-analysis. Sports Med. "
             "2016;46(2):231-240.",
             "Synthèse : une série lourde peut rendre les sauts et les sprints qui suivent un peu plus puissants "
             "(potentialisation post-activation) : effet petit, plus net chez les athlètes forts, avec 5 à 7 min "
             "de récupération après la série lourde, et après plusieurs séries ou de la pliométrie. En pratique "
             "(méthode par contraste) : 3 à 5 sauts ou sprints courts de qualité après une série de squat lourd, "
             "pas 10 répétitions fatigantes, et récupération complète avant la série suivante. Sprints à placer "
             "juste après la force principale, pas en fin de séance sur des ischios fatigués.",
             (), ("strength", "contrast", "speed", "strides")),
    Evidence("kb_squat_amplitude_rhea2016",
             "Rhea MR, Kenn JG, Peterson MD, et al. Joint-angle specific strength adaptations influence "
             "improvements in power in highly trained athletes. Hum Mov. 2016;17(1):43-49.",
             "Synthèse : chez des athlètes entraînés, le quart de squat a davantage amélioré le sprint et la "
             "détente que le demi-squat ou le squat complet : la force progresse surtout dans l'amplitude "
             "travaillée. Pour un coureur, un squat au-dessus de la parallèle (box haute, genou ouvert à plus de "
             "90°) est donc acceptable et proche du geste ; garder la même hauteur de box pour comparer les "
             "charges, et compléter par un exercice en grande amplitude (fente bulgare) pour la hanche.",
             (), ("strength", "squat", "speed")),
    Evidence("kb_trapbar_swinton2011",
             "Swinton PA, Stewart A, Agouris I, Keogh JWL, Lloyd R. A biomechanical analysis of straight and "
             "hexagonal barbell deadlifts using submaximal loads. J Strength Cond Res. 2011;25(7):2000-2009.",
             "Synthèse : le soulevé de terre à la barre hexagonale (trap bar) place la charge dans l'axe du corps : "
             "moins de contrainte sur le bas du dos, plus de travail des genoux, et des pics de force, de vitesse "
             "et de puissance plus élevés qu'à la barre droite. Bon outil pour la force et pour les sauts chargés "
             "légers (environ 20 à 30 % de la charge maximale). Le grip limite souvent avant les jambes : sangles "
             "de tirage pour les séries lourdes.",
             (), ("strength", "squat", "contrast")),
    Evidence("kb_ischios_vanderhorst2015",
             "van der Horst N, Smits DW, Petersen J, Goedhart EA, Backx FJ. The preventive effect of the Nordic "
             "hamstring exercise on hamstring injuries in amateur soccer players: a randomized controlled trial. "
             "Am J Sports Med. 2015;43(6):1316-1323.",
             "Synthèse : un programme progressif de Nordic curl (excentrique des ischio-jambiers) a réduit "
             "d'environ deux tiers les blessures aux ischios chez des footballeurs amateurs. Données de "
             "football, pas de course à pied, mais l'exercice est le plus étudié pour protéger les ischios. "
             "Commencer bas (2 × 4) et monter à 3 × 6-8 en quelques semaines : grosses courbatures au début, à "
             "éloigner de 48 h des séances de vitesse. Le soulevé de terre roumain (bilatéral, B-stance ou sur "
             "une jambe) travaille les ischios côté hanche ; le leg curl, côté genou. Une douleur derrière le "
             "genou peut venir des ischios, du mollet ou du poplité : à faire examiner si elle persiste.",
             (), ("strength", "injury", "hamstring")),
    Evidence("kb_sommeil_walsh2021",
             "Walsh NP, Halson SL, Sargent C, et al. Sleep and the athlete: narrative review and 2021 expert "
             "consensus recommendations. Br J Sports Med. 2021;55(7):356-368.",
             "Synthèse : les athlètes dorment souvent moins que recommandé (7 à 9 h) ; le manque de sommeil dégrade "
             "surtout l'endurance, les efforts répétés, la perception de l'effort (tout paraît plus dur), l'humeur "
             "et l'immunité, et il est associé à plus de blessures et de maladies. Recommandations : horaires "
             "réguliers, siestes courtes (20-30 min) si besoin, attention aux séances très tardives et aux "
             "voyages. Après une ou plusieurs nuits courtes, on garde l'endurance facile et on décale l'intensité.",
             (), ("sleep", "recovery", "injury")),
    Evidence("kb_sommeil_fullagar2015",
             "Fullagar HH, Skorski S, Duffield R, Hammes D, Coutts AJ, Meyer T. Sleep and athletic performance: "
             "the effects of sleep loss on exercise performance, and physiological and cognitive responses to "
             "exercise. Sports Med. 2015;45(2):161-186.",
             "Synthèse : la privation de sommeil (nuit blanche ou nuits raccourcies) altère davantage les efforts "
             "longs et sous-maximaux et les fonctions cognitives que la force maximale ou les efforts très courts ; "
             "l'effort ressenti augmente à allure égale. Les effets s'additionnent sur plusieurs nuits.",
             (), ("sleep", "recovery")),
    Evidence("kb_stress_stultskolehmainen2012",
             "Stults-Kolehmainen MA, Bartholomew JB. Psychological stress impairs short-term muscular recovery "
             "from resistance exercise. Med Sci Sports Exerc. 2012;44(11):2220-2227.",
             "Synthèse : les personnes les plus stressées dans leur vie récupéraient moins vite (force, énergie, "
             "fatigue, courbatures) dans les jours suivant une séance de musculation intense. Le stress hors sport "
             "s'ajoute à la charge d'entraînement : une semaine de stress élevé justifie d'alléger l'intensité.",
             (), ("stress", "recovery", "busy_weeks")),
    Evidence("kb_stress_ivarsson2017",
             "Ivarsson A, Johnson U, Andersen MB, Tranaeus U, Stenling A, Lindwall M. Psychosocial factors and sport "
             "injuries: meta-analyses for prediction and prevention. Sports Med. 2017;47(2):353-365.",
             "Synthèse : le stress (événements de vie négatifs, réponse au stress) est associé à un risque de "
             "blessure plus élevé, et les interventions psychologiques (gestion du stress, relaxation) réduisent "
             "le nombre de blessures dans les essais disponibles.",
             (), ("stress", "injury")),
    Evidence("kb_surentrainement_meeusen2013",
             "Meeusen R, Duclos M, Foster C, et al. Prevention, diagnosis, and treatment of the overtraining "
             "syndrome: joint consensus statement of the ECSS and the ACSM. Med Sci Sports Exerc. "
             "2013;45(1):186-205.",
             "Synthèse : la fatigue qui ne passe pas avec quelques jours de repos, la baisse de performance, les "
             "troubles du sommeil et de l'humeur sont des signaux d'alerte ; les causes sont la charge "
             "d'entraînement mais aussi le stress de vie, le manque de sommeil et une alimentation insuffisante. "
             "Prévention : charge progressive, jours faciles vraiment faciles, suivi des sensations.",
             (), ("recovery", "stress", "sleep", "load")),
    Evidence("kb_nutrition_thomas2016",
             "Thomas DT, Erdman KA, Burke LM. American College of Sports Medicine joint position statement: "
             "nutrition and athletic performance. Med Sci Sports Exerc. 2016;48(3):543-568.",
             "Synthèse : les besoins en glucides suivent la charge (environ 5 à 7 g/kg/j pour un entraînement "
             "modéré d'1 h par jour, 6 à 10 g/kg/j pour 1 à 3 h par jour) ; protéines 1,2 à 2,0 g/kg/j réparties "
             "sur la journée ; pendant l'effort de plus d'1 h, 30 à 60 g de glucides par heure, jusqu'à 90 g/h "
             "au-delà de 2 h 30 avec un mélange glucose-fructose ; boire selon un plan qui limite la perte de poids "
             "à moins de 2 %, sans boire plus que ce qu'on perd. Après l'effort : glucides et 20-30 g de protéines.",
             (), ("nutrition",)),
    Evidence("kb_nutrition_ultra_tiller2019",
             "Tiller NB, Roberts JD, Beasley L, et al. International Society of Sports Nutrition position stand: "
             "nutritional considerations for single-stage ultra-marathon training and racing. J Int Soc Sports "
             "Nutr. 2019;16(1):50.",
             "Synthèse : en ultra, viser environ 150 à 400 kcal/h, dont 30 à 50 g de glucides par heure (davantage "
             "si l'estomac est entraîné), 450 à 750 mL d'eau par heure selon la chaleur, et du sodium ; les "
             "troubles digestifs sont une cause majeure d'abandon. Tester en entraînement ce qu'on prendra en "
             "course ; éviter un déficit énergétique chronique pendant la préparation.",
             (), ("nutrition", "trail", "recovery_ultra", "carbs", "real_food")),
    Evidence("kb_nutrition_estomac_jeukendrup2017",
             "Jeukendrup AE. Training the gut for athletes. Sports Med. 2017;47(Suppl 1):101-110.",
             "Synthèse : l'intestin s'adapte : manger et boire régulièrement pendant les sorties longues (en "
             "augmentant progressivement les quantités sur plusieurs semaines) améliore l'absorption des glucides "
             "et réduit les troubles digestifs en course. Faire des sorties longues « répétition générale » avec "
             "les produits et les quantités prévus le jour J.",
             (), ("nutrition", "trail")),
    Evidence("kb_nutrition_proteines_jager2017",
             "Jäger R, Kerksick CM, Campbell BI, et al. International Society of Sports Nutrition position stand: "
             "protein and exercise. J Int Soc Sports Nutr. 2017;14:20.",
             "Synthèse : 1,4 à 2,0 g de protéines par kg et par jour suffisent à la plupart des sportifs, en "
             "prises de 20 à 40 g toutes les 3 à 4 h ; utile pour récupérer de la musculation et des gros volumes "
             "de course.",
             (), ("nutrition", "strength", "recovery")),
    Evidence("kb_reds_mountjoy2018",
             "Mountjoy M, Sundgot-Borgen JK, Burke LM, et al. IOC consensus statement on relative energy deficiency "
             "in sport (RED-S): 2018 update. Br J Sports Med. 2018;52(11):687-697.",
             "Synthèse : manger trop peu par rapport à l'entraînement (énergie disponible basse), chez les hommes "
             "comme chez les femmes, dégrade la santé osseuse, hormonale et immunitaire, la récupération et la "
             "performance, et augmente le risque de fractures de fatigue. Perdre du poids en période de grosse "
             "charge est risqué : à faire lentement, hors des blocs clés, et avec un professionnel.",
             (), ("nutrition", "injury", "recovery")),
    Evidence("kb_charge_frandsen2025",
             "Frandsen JSB, Hulme A, Parner ET, et al. How much running is too much? Identifying high-risk running "
             "sessions in a 5200-person cohort study. Br J Sports Med. 2025;59(17):1203-1210. "
             "doi:10.1136/bjsports-2024-109380",
             "Synthèse : cohorte de 5 205 coureurs suivis 18 mois. Le risque de blessure de surcharge augmente dès "
             "qu'une sortie dépasse de plus de 10 % la plus longue sortie des 30 jours précédents (risque ×1,5 à "
             "×2,3). Ni le ratio d'une semaine à l'autre ni l'ACWR ne montraient cette relation. Surtout des "
             "amateurs sur route : en trail, le dénivelé compte autant que la distance. On fait monter les sorties "
             "longues par petites marches.",
             ("session.peak_warn_ratio",), ("load", "injury", "volume", "acwr", "marathon", "trail")),
    Evidence("kb_nutrition_morton2026",
             "Morton JP, Fell JM, Gonzalez JT, et al. From Metabolism to Medals: Contemporary Perspectives and "
             "Revisiting Carbohydrate Guidelines for Fuelling Endurance Athletes During Exercise. J Nutr. "
             "2026;156(5):101442. doi:10.1016/j.tjnut.2026.101442",
             "Synthèse : revue d'experts. Le plafond historique de 90 g/h de glucides peut monter à 120 g/h chez "
             "l'athlète entraîné, après un entraînement progressif de l'estomac. Au-delà de 120 g/h, rien n'est "
             "démontré (les 120 à 200 g/h vus sur le terrain ne sont pas soutenus). Repères : 30-60 g/h pour un "
             "effort de 1 h à 2 h 30, 60-90 g/h au-delà, 90-120 g/h seulement si l'athlète est entraîné et a "
             "entraîné son estomac ; jamais plus de 120 g/h.",
             (), ("nutrition", "trail", "recovery_ultra", "carbs")),
    Evidence("kb_nutrition_chaleur_salame2026",
             "Salame A, Brown D, Oueijan K, McCullough D. Carbohydrate supplementation for endurance exercise in "
             "the heat: a systematic review with practical recommendations. J Int Soc Sports Nutr. 2026;23(1). "
             "doi:10.1080/15502783.2026.2669307 (chiffres à recontrôler dans l'article original)",
             "Synthèse : revue de 9 essais en cyclisme à la chaleur, aux résultats mitigés (5 essais avec un "
             "bénéfice, 4 sans). À la chaleur, on n'augmente pas les glucides au-delà de ce qui a été testé à "
             "l'entraînement ; on privilégie l'hydratation et le sodium, et on évite les boissons très concentrées. "
             "Il n'y a pas de données en course à pied. Preuve faible.",
             (), ("nutrition", "heat", "trail")),
    Evidence("kb_trail_dommages_garciavaliente2026",
             "Garcia-Valiente I, Pradas F, Ortega-Zayas MÁ, et al. Muscle, Neuromuscular, and Cardiac Damage in "
             "Trail Running: A Systematic Review. Muscles. 2026;5(1):9. doi:10.3390/muscles5010009",
             "Synthèse : revue de 15 études. Un trail provoque des dommages musculaires importants (CK ×15 à 20 "
             "environ), une perte de force et une élévation cardiaque transitoire, d'autant plus fortes que la "
             "descente est importante. Après un trail à fort D−, la récupération est plus longue qu'après une "
             "course sur route de même durée. Échantillons petits, synthèse narrative.",
             (), ("trail", "recovery_ultra", "injury", "strength")),
    Evidence("kb_durabilite_malinen2026",
             "Malinen T, Matomäki P, Uusitalo A, Kyröläinen H, Nuuttila O. Determinants of Physiological Resilience "
             "Among Recreational-To-Competitive Endurance Runners. Scand J Med Sci Sports. 2026;36(7):e70349. "
             "doi:10.1111/sms.70349",
             "Synthèse : étude de 33 coureurs. Une meilleure durabilité (moindre dégradation après 2 h 30 d'effort) "
             "va avec une VO2max plus haute et une force maximale des jambes plus élevée. C'est une corrélation "
             "modeste, pas une preuve de cause, mais c'est un argument pour garder la force lourde pendant toute la "
             "préparation ultra.",
             (), ("durability", "strength", "trail", "volume")),
    Evidence("kb_vfc_schaffarczyk2026",
             "Schaffarczyk M, Sperlich B. Heart rate variability-guided endurance training: evaluating strengths, "
             "weaknesses, opportunities, and threats for load prescription and adjustment. Front Sports Act Living. "
             "2026;8:1858271. doi:10.3389/fspor.2026.1858271",
             "Synthèse : article de perspective. Guider l'entraînement par la VFC n'améliore pas nettement la "
             "performance, mais régularise un peu les adaptations. On suit la tendance sur 7 jours par rapport à la "
             "référence individuelle, on la croise avec le sommeil, le stress et la RPE, et on ne change jamais une "
             "séance sur une seule valeur basse.",
             (), ("hrv", "recovery", "load", "sleep", "stress")),
    # --- v0.38 : nutrition à l'effort (statut « à relire », voir NUTRITION_V038) ---------------------------
    Evidence("kb_glucides_jeukendrup2014",
             "Jeukendrup A. A step towards personalized sports nutrition: carbohydrate intake during exercise. "
             "Sports Med. 2014;44(Suppl 1):S25-S33.",
             "Synthèse : les apports conseillés pendant l'effort dépendent surtout de la durée. Moins de 30 min : "
             "rien n'est nécessaire ; jusqu'à 1 h 15 : de petites quantités suffisent ; 1 à 2 h : jusqu'à environ "
             "30 g/h ; 2 à 3 h : jusqu'à environ 60 g/h ; au-delà de 2 h 30 : jusqu'à environ 90 g/h, mais "
             "seulement avec des glucides « multiples transportables » (glucose ou maltodextrine + fructose, souvent "
             "autour de 2 pour 1), car une seule source de glucose plafonne autour de 60 g/h. Ce sont des repères "
             "à ajuster à la tolérance de chacun, testés à l'entraînement.",
             (), ("nutrition", "carbs", "trail")),
    Evidence("kb_estomac_costa2017",
             "Costa RJS, Miall A, Khoo A, et al. Gut-training: the impact of two weeks repetitive gut-challenge "
             "during exercise on gastrointestinal status, glucose availability, fuel kinetics, and running "
             "performance. Appl Physiol Nutr Metab. 2017;42(5):547-557.",
             "Synthèse : chez des coureurs d'endurance, deux semaines de prises répétées de glucides pendant "
             "l'effort ont réduit les symptômes digestifs et amélioré l'absorption des glucides par rapport à un "
             "groupe contrôle. Petit essai, mais cohérent avec l'idée d'« entraîner l'intestin » : augmenter les "
             "apports par paliers (par exemple +10 g/h d'une sortie longue à l'autre) plutôt que d'un coup, avec les "
             "produits prévus pour la course.",
             (), ("nutrition", "gut", "carbs", "gi")),
    Evidence("kb_digestif_costa2017",
             "Costa RJS, Snipe RMJ, Kitic CM, Gibson PR. Systematic review: exercise-induced gastrointestinal "
             "syndrome-implications for health and intestinal disease. Aliment Pharmacol Ther. "
             "2017;46(3):246-265.",
             "Synthèse : l'effort prolongé détourne le sang de l'intestin et perturbe sa paroi ; nausées, "
             "ballonnements, crampes d'estomac et envies pressantes sont fréquents en ultra, surtout à la chaleur, "
             "à haute intensité, avec la déshydratation et des prises trop concentrées. Pistes prudentes : "
             "fractionner les prises, diluer les boissons, limiter fibres, gras et fructose en excès avant et "
             "pendant l'effort, éviter les anti-inflammatoires, et tester sa stratégie à l'entraînement. Des "
             "symptômes sévères (sang, douleurs fortes, vomissements répétés) imposent d'arrêter et de consulter.",
             (), ("nutrition", "gi", "gut", "heat")),
    Evidence("kb_hyponatremie_hewbutler2015",
             "Hew-Butler T, Rosner MH, Fowkes-Godek S, et al. Statement of the Third International "
             "Exercise-Associated Hyponatremia Consensus Development Conference, Carlsbad, California, 2015. "
             "Clin J Sport Med. 2015;25(4):303-320.",
             "Synthèse : l'hyponatrémie d'effort (sodium sanguin trop bas) vient d'abord du fait de boire plus que "
             "ce qu'on perd, surtout lors des efforts longs et lents ; prendre du poids pendant une course est un "
             "signal d'alerte. Le consensus recommande de boire selon la soif plutôt que selon un volume imposé. "
             "Le sel seul ne protège pas d'un excès d'eau. Maux de tête, nausées, confusion pendant ou après un "
             "ultra imposent un avis médical.",
             (), ("nutrition", "hydration", "heat")),
    Evidence("kb_hydratation_ultra_hoffman2019",
             "Hoffman MD, Stellingwerff T, Costa RJS. Considerations for ultra-endurance activities: part 2 - "
             "hydration. Res Sports Med. 2019;27(2):182-194.",
             "Synthèse : en ultra, les besoins en eau varient beaucoup selon la chaleur, l'altitude et l'allure ; "
             "boire selon la soif convient à la plupart des coureurs, avec des apports souvent compris entre "
             "400 et 800 ml/h. Le sodium (boisson iso, aliments salés, soupe) aide surtout à maintenir l'envie de "
             "boire et de manger ; les capsules de sel ne compensent pas un excès d'eau et n'empêchent pas à coup "
             "sûr les crampes, qui sont souvent liées à la fatigue musculaire.",
             (), ("nutrition", "hydration", "heat", "trail")),
    Evidence("kb_cafeine_guest2021",
             "Guest NS, VanDusseldorp TA, Nelson MT, et al. International society of sports nutrition position "
             "stand: caffeine and exercise performance. J Int Soc Sports Nutr. 2021;18(1):1.",
             "Synthèse : la caféine améliore la performance d'endurance à des doses d'environ 3 à 6 mg/kg, "
             "souvent prises environ 60 min avant ; des doses plus faibles (autour de 2-3 mg/kg) prises en cours "
             "d'effort prolongé sont aussi efficaces. La réponse est très individuelle ; effets indésirables "
             "possibles : troubles digestifs, nervosité, sommeil perturbé. En ultra, la garder pour la nuit ou la "
             "fin de course, ne pas cumuler gels caféinés, cola et comprimés sans compter, et la tester avant.",
             (), ("nutrition", "caffeine", "trail")),
    Evidence("kb_aliments_ultra_costa2019",
             "Costa RJS, Hoffman MD, Stellingwerff T. Considerations for ultra-endurance activities: part 1 - "
             "nutrition. Res Sports Med. 2019;27(2):166-181.",
             "Synthèse : sur les ultras longs, l'allure plus lente permet de manger du solide et l'écœurement du "
             "sucré est fréquent ; mélanger gels et boissons (rapides) avec de la vraie nourriture (pomme de terre, "
             "riz, soupe, sandwich) aide à tenir l'apport d'énergie sur la durée. Les aliments gras, protéinés ou "
             "riches en fibres se digèrent plus lentement : plutôt aux ravitos et aux allures faciles. Les besoins "
             "et la tolérance sont très individuels : tester les aliments des ravitos à l'entraînement.",
             (), ("nutrition", "real_food", "trail", "gi")),
    Evidence("kb_chaleur_racinais2015",
             "Racinais S, Alonso JM, Coutts AJ, et al. Consensus recommendations on training and competing in the "
             "heat. Scand J Med Sci Sports. 2015;25(Suppl 1):6-19.",
             "Synthèse : la chaleur dégrade la performance d'endurance et augmente les risques. S'acclimater "
             "(environ 1 à 2 semaines d'entraînement répété au chaud) est la mesure la plus efficace ; boire de "
             "façon adaptée, avec du sodium, partir moins vite et se refroidir (eau, glace, ombre) complètent. "
             "Signes d'alerte (confusion, arrêt de la transpiration, malaise) : arrêter et se faire aider.",
             (), ("heat", "hydration", "nutrition", "trail")),
    Evidence("kb_palette_aliments_projet",
             "Synthèse de l'équipe projet à partir de Jeukendrup 2014, Costa et al. 2019 et Tiller et al. 2019 "
             "(fiches ci-dessus) ; palette détaillée dans coach/nutrition_foods.py (valeurs typiques à vérifier "
             "sur l'étiquette).",
             "Synthèse : quoi manger à l'effort, par famille. Gels et gommes : rapides et dosés (20-40 g), à "
             "prendre avec de l'eau, risque d'écœurement s'ils sont seuls. Boisson iso : glucides, eau et sodium "
             "à la fois, la base quand il fait chaud. Compote, pâte de fruits, bonbons, pain d'épices : sucres "
             "simples agréables, riches en fructose, utiles en alternance. Barres : un peu plus lentes. Salé "
             "(soupe, bouillon, biscuits salés, pomme de terre) : quand le sucré ne passe plus, la nuit et pour le "
             "sodium. Cola dégazé : glucides et un peu de caféine, apprécié en fin d'ultra. Fromage, charcuterie, "
             "sandwich : lents, aux ravitos ou à allure facile. Règle d'or : rien de nouveau le jour de la course.",
             (), ("nutrition", "real_food", "carbs", "caffeine")),
]
# v0.38 : fiches nutrition à faire relire par un expert avant tout usage hors pilote
NUTRITION_V038 = {"kb_glucides_jeukendrup2014": "a_relire", "kb_estomac_costa2017": "a_relire",
                  "kb_digestif_costa2017": "a_relire", "kb_hyponatremie_hewbutler2015": "a_relire",
                  "kb_hydratation_ultra_hoffman2019": "a_relire", "kb_cafeine_guest2021": "a_relire",
                  "kb_aliments_ultra_costa2019": "a_relire", "kb_chaleur_racinais2015": "a_relire",
                  "kb_palette_aliments_projet": "a_relire"}

KEYWORDS = {
    "double_threshold": r"double[- ]?seuil|norv[eé]g",
    "doubles": r"\bdoubles?\b|bi-?quotidien|deux sorties",
    "threshold": r"seuil|tempo",
    "vma": r"\bvma\b|30 ?/ ?30|fractionn|intervalle",
    "speed": r"vitesse|rapide|vma",
    "volume": r"volume|kilom[eé]trage|heures? par semaine",
    "recovery_ultra": r"ultra|diag|r[eé]cup",
    "recovery": r"r[eé]cup|fatigu|repos",
    "taper": r"aff[uû]t|d[eé]charg",
    "strides": r"lignes? droites?|c[oô]te|sprint",
    "marathon": r"marathon|sub ?3|3 ?h",
    "missed_sessions": r"rat[eé]|manqu[eé]|rattrap",
    "strength": r"renfo|muscu|force|gainage|squat|salle|poids du corps|pliom|trap ?bar|soulev[eé]|deadlift|fente|nordic|leg curl",
    "squat": r"squat|trap ?bar|hex ?bar|soulev[eé] de terre|deadlift|box",
    "contrast": r"contraste|potentialis|box jump|sauts?\b|(sprint|vitesse).{0,40}(apr[eè]s|suite).{0,30}(force|muscu|squat|renfo)",
    "hamstring": r"ischio|arri[eè]re (de la )?cuisse|derri[eè]re (le|du) genou|nordic|hamstring|leg curl|roumain",
    "sleep": r"sommeil|dormi|dors\b|dormir|nuit|insomni|sieste|r[eé]veil",
    "stress": r"stress|anxi|angoiss|charge mentale|pression|tendu|[ée]nerv|d[ée]bord|pr[ée]occup",
    "nutrition": r"aliment|nutrition|manger|mange\b|mang[eé]|glucide|compote|p[aâ]te de fruits|naus[eé]|g ?/ ?h\b|prot[eé]ine|\bgels?\b|boisson|ravito|hydrat|sodium|\bsel\b|digest|estomac|ventre|r[eé]gime|calori|perdre du poids|maigrir|petit[- ]d[eé]j|repas",
    "injury": r"bless|tendin|douleur|pr[eé]vention|fragile",
    "cross_training": r"v[eé]lo|natation|nage|foot|tennis|escalade|crossfit|ski|sport",
    "rest_days": r"repos|off\b|jour sans",
    "busy_weeks": r"vacances|cong[eé]s|d[eé]placement|charg[eé]e|boulot|travail",
    "hr": r"ceinture|cardio|poignet|fr[eé]quence cardiaque|\bfc\b",
    "durability": r"durabilit|tenir (l'|mon |son )?allure|fin de course|derni[eè]res? heures?|d[eé]rive|s'effondr|effondr|coup de bambou|exploser",
    "hrv": r"\bvfc\b|\bhrv\b|variabilit[eé] (cardiaque|de la fr)|rmssd|readiness|body ?battery",
    "heat": r"chaleur|\bchaud|canicule|temp[eé]rature|d[eé]shydrat|\bsodium\b|\bsel\b",
    "trail": r"trail|descente|d\+|d[eé]nivel",
    "terrain": r"pente|mont[ée]e|descente|d[eé]nivel|\bvent\b|vent de (face|dos)|\bgap\b|c[oô]te|vallonn|bosse",
    # v0.38 : nutrition à l'effort
    "carbs": r"glucide|g ?/ ?h\b|\bgels?\b|gommes?|maltodextrin|fructose|sucres?\b|combien.{0,20}(manger|prendre)",
    "gut": r"entra[iî]ner (l'|mon |son |ton )?(estomac|intestin|ventre)|gut[- ]training|tol[eé]r",
    "gi": r"ventre|estomac|naus[eé]|vomi|ballonn|diarrh|envie pressante|digest|[eé]c(oe|œ)ur|intestin|gerb",
    "hydration": r"\bboire\b|\bbu\b|bois\b|hydrat|flasque|\bsoif|hyponatr|\bsodium|\bsel\b|ml ?/ ?h\b|\beau\b",
    "caffeine": r"caf[eé]ine|caf[eé]\b|\bcola\b|coca",
    "real_food": r"ravito|sandwich|vraie nourriture|solide|sal[eé]\b|soupe|bouillon|compote|p[aâ]tes? de fruits|"
                 r"barres?\b|mange quoi|manger quoi|quoi manger|aliments?",
}


def tags_for(text: str) -> set[str]:
    t = text.lower()
    return {tag for tag, rx in KEYWORDS.items() if re.search(rx, t)}


def cards(tags: set[str], k: int = 4) -> list[dict]:
    from .rag import rank
    return [{"id": e.id, "source": e.source, "texte": e.text} for e in rank(KNOWLEDGE, tags, k)]
