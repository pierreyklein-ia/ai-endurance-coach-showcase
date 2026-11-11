"""Front-line frugal : cascade règles -> (option) petit classifieur -> LLM.

Choix d'architecture vs spec :
- On ne REJETTE jamais un message ; on le route. Le "filtre anti-spam" n'a pas
  de sens sur un canal 1:1 avec un abonné payant : le vrai sujet est le
  routage d'intention au coût minimal.
- Le coût d'un faux négatif (fatigue ignorée, cf. "ma fille a été malade")
  >> le coût d'un appel LLM (~0,1 centime). Les seuils sont donc asymétriques :
  on n'envoie en NOISE que ce qui est quasi certain.
- Pas de modèle ML custom au MVP : pas de dataset étiqueté. Les étiquettes
  produites par le petit LLM en prod constituent ce dataset ; on distille
  ensuite (TF-IDF+LogReg, SetFit...) quand le volume le justifie.
"""
from __future__ import annotations

import re
import unicodedata

from .schemas import Route

_NOISE_EXACT = {
    "ok", "okay", "oki", "merci", "merci !", "super", "top", "parfait", "cool",
    "ca marche", "vu", "bien recu", "d'accord", "dac", "yes", "oui", "👍", "🙏", "💪", "👌",
}

# Lexique à RAPPEL MAXIMAL : mieux vaut 10 faux positifs qu'un faux négatif.
_RED_FLAG = re.compile(
    r"\b(douleur|mal (au|a la|aux|de)|bless|tendin|entorse|fracture|"
    r"poitrine|thorac|palpitation|vertige|malaise|syncope|essouffl|"
    r"fievre|grippe|covid|malade|urine (fonce|marron|brun)|crampe)\w*",
    re.IGNORECASE,
)


# Sous-ensemble "urgence" : court-circuite TOUT le raisonnement LLM.
_EMERGENCY = re.compile(
    r"\b(douleur (a la |dans la )?poitrine|douleur thorac|palpitation|syncope|"
    r"perte de connaissance|urine (fonce|marron|brun|noir))\w*",
    re.IGNORECASE,
)


# Détresse psychologique / risque pour autrui : formulations EXPLICITES seulement.
# « j'ai passé le seuil de patience » ne déclenche pas ce chemin (géré en coaching).
_DISTRESS = re.compile(
    r"(envie d['’ ]?en finir|veux en finir|en finir avec (la vie|tout)|me suicider|suicid|"
    r"(envie de|je veux|je vais) me tuer|plus envie de vivre|envie de mourir|"
    r"(la|le|les) frapper|lever la main sur|je vais (la|le) (tuer|cogner|taper)|devenir violent)",
    re.IGNORECASE,
)
# Volontairement NON détectés (hyperboles de coureur) : « cette côte va me tuer »,
# « hâte d'en finir avec ce bloc », « j'ai frappé le mur au 30e ».


def is_distress(text: str) -> bool:
    return bool(_DISTRESS.search(_normalize(text)))


def is_emergency(text: str) -> bool:
    return bool(_EMERGENCY.search(_normalize(text)))


def _normalize(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


_NOISE_TOKENS = {w for phrase in _NOISE_EXACT for w in phrase.split()} | {"!", "a", "toi", "bien"}


def _is_noise(norm: str) -> bool:
    if not norm or norm in _NOISE_EXACT or re.fullmatch(r"[\W_]+", norm):
        return True
    words = re.findall(r"[a-z']+", norm)       # emojis / ponctuation ignorés
    # Court ET exclusivement composé d'acquiescements ("ok merci 👍", "super merci !")
    return 0 < len(words) <= 4 and all(w in _NOISE_TOKENS for w in words)


def triage(text: str) -> Route:
    norm = _normalize(text)
    if _is_noise(norm):
        return Route.NOISE
    if _RED_FLAG.search(norm):
        # "Ma fille a été malade" déclenche aussi ce chemin : c'est voulu.
        # Le nœud red_flag (LLM léger) désambiguïse qui est malade.
        return Route.RED_FLAG
    return Route.COACHING
