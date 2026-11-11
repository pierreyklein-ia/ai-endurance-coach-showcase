"""Accueil d'un nouvel athlète : premier bloc d'entraînement construit à partir de son bilan,
puis ajusté en conversation.

Déterministe : la structure (récupération, reprise, bloc ciblé sur le point faible) et les volumes
viennent des données et des préférences exprimées dans le chat. Le LLM extrait ces préférences et
rédige ; il ne fixe jamais un chiffre du plan.

Règles :
- après un effort de plus de 24 h : pas d'intensité avant J+28 par défaut, J+21 au plus tôt si
  l'athlète se sent prêt (6 à 24 h : J+21, J+14 au plus tôt) ; avant, récupération puis reprise ;
- bloc de vitesse de 4 semaines par défaut (2 à 6), la dernière allégée (-30 %) à partir de 4 ;
- volume = 80 % de la semaine type récente, plafonné à 10 h (12 h si l'athlète aime le volume),
  modulable de -40 % à +30 % : on n'augmente jamais volume ET intensité en même temps ;
- focus « vitesse » : VMA courte (30/30) et seuil (ou double seuil, à partir de la 2e semaine),
  lignes droites et sprints en côte dans les footings, sortie longue le jour choisi ; si le marathon
  décroche, allure marathon dans le dernier tiers de la sortie longue ;
- jamais deux jours durs d'affilée (séance de qualité ou sortie longue) ; doubles (footing du matin)
  seulement les jours autorisés, en semaine de vitesse non allégée ;
- v0.36 : la 1re semaine part de la semaine réelle (7 jours avant le début du plan, lissée avec celle d'avant),
  voir `last_week_anchor` : suite (au plus +10 %), reprise progressive après une semaine très légère,
  assimilation après une semaine très chargée ou une course ; aucune sortie au-delà de 110 % de la plus longue
  des 30 derniers jours (Frandsen 2025).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from datetime import date as Date  # champ « date » des échéances
from typing import Literal

from pydantic import BaseModel, Field

from .diagnosis import Diagnosis, RecentRun
from .pilot.planner import estimate_load
from .rules_registry import R
from .schemas import PlannedSession
from .session_content import harmonize, resize_session

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
JOURS_ABR = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MOIS_NOMS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre"]


class Event(BaseModel):
    """Une échéance : A = objectif principal, B = course secondaire, C = projet plus lointain."""
    name: str
    date: Date | None = None
    year: int | None = None                    # v0.25 : édition visée quand seule l'année est connue
    role: Literal["A", "B", "C"] = "A"
    kind: str = "trail"
    distance_km: float | None = None
    dplus_m: int | None = None
    note: str = ""                             # « pour le plaisir », « objectif < 3 h »...
    source: str = ""                           # où la date a été trouvée (URL, catalogue)
    start: str = ""                            # v0.48 : heure (et lieu) de départ du format


class Profile(BaseModel):
    first_name: str = ""
    goal_name: str = Field(min_length=2, max_length=120)
    goal_date: date
    goal_kind: str = "trail"                  # "trail" | "route"
    goal_distance_km: float | None = None
    goal_dplus_m: int | None = None
    days_per_week: int = Field(default=5, ge=2, le=7)       # v0.48 : 2 jours possibles (+ bonus facultatif)
    long_day: int = Field(default=6, ge=0, le=6)
    events: list[Event] = Field(default_factory=list)      # autres échéances (B, C)


class OtherSport(BaseModel):
    """Un autre sport pratiqué régulièrement (vélo, foot, escalade...)."""
    name: str
    days: list[int] = Field(default_factory=list)   # jours habituels (0 = lundi)
    hard: bool = False                               # sport intense / à impacts : pas de séance dure le lendemain
    minutes_per_week: int | None = None


class Busy(BaseModel):
    """Vacances ou semaine chargée : `off` = pas de course, `light` = charge réduite."""
    start: date
    end: date
    kind: Literal["off", "light"] = "light"
    label: str = ""
    min_runs: int | None = None                # v0.48 : « garder au moins 2 sorties » pendant cette période


HARD_SPORTS = {"foot", "football", "futsal", "tennis", "squash", "padel", "badminton", "basket", "handball",
               "rugby", "crossfit", "boxe", "hyrox", "volley"}


class Prefs(BaseModel):
    """Ce que l'athlète a demandé dans la conversation (bornes appliquées par le code)."""
    # v0.15 : questions de l'accueil (renfo, autres sports, Strava, cardio, repos, vacances)
    strength: Literal["gym", "bodyweight", "none"] | None = None   # None = pas répondu -> sans matériel
    strength_per_week: int = 2
    strength_days: list[int] = Field(default_factory=list)          # s'il a déjà ses créneaux
    other_sports: list[OtherSport] = Field(default_factory=list)
    strava_complete: bool | None = None
    hr_source: Literal["ceinture", "poignet", "aucune"] | None = None
    rest_days: list[int] = Field(default_factory=list)
    busy: list[Busy] = Field(default_factory=list)
    resume_from: date | None = None           # reprise « sérieuse » souhaitée
    volume_factor: float = 1.0                # 0,6 à 1,3
    weekly_hours: float | None = None         # cible pour les semaines de vitesse
    speed_weeks: int = 4                      # 2 à 6
    unavailable: list[date] = Field(default_factory=list)
    weekend_max_min: int | None = None        # temps max par jour le week-end (famille...)
    double_days: list[int] = Field(default_factory=list)   # jours où un 2e footing est possible
    double_threshold: bool = False            # double seuil (matin + soir), méthode « norvégienne »
    likes_volume: bool = False
    bonus: bool = True                        # v0.25 : une séance bonus facultative par semaine
    discipline: Literal["route", "trail", "mixte"] | None = None   # v0.25 : demandé à l'accueil
    # v0.48 : jours imposés par la vie de l'athlète (séance du club le mardi, bonus le week-end...)
    quality_days: list[int] = Field(default_factory=list)  # jour(s) de la séance d'intensité, gardés tels quels
    club_quality: bool = False                # la séance d'intensité se fait au club (contenu donné par le club)
    quality_minutes: int | None = None        # durée habituelle de cette séance (club : 1 h 15...)
    bonus_days: list[int] = Field(default_factory=list)    # où placer la séance bonus facultative
    strength_with_quality: bool | None = None  # renfo le jour de la qualité ; False = jamais ce jour-là
    # v0.49 : volume maximum que l'athlète PEUT faire par semaine (None = pas de plafond, comptes existants)
    max_week_min: int | None = None
    max_week_km: float | None = None
    max_week_dplus: int | None = None
    max_week_inferred: bool = False           # estimé d'après l'historique (« je ne sais pas »)
    # v0.50 : premier jour de SA semaine (0 = lundi … 6 = dimanche) : vue Semaine, plafonds, récap, semaines du plan
    week_start: int = 0


class Block(BaseModel):
    sessions: list[PlannedSession]
    weeks: list[dict]                          # [{start, kind, minutes}]
    intensity_from: date
    resume_from: date
    summary: str
    notes: list[str] = Field(default_factory=list)   # ajustements imposés par la sécurité
    layout: dict = Field(default_factory=dict)
    last_week: str = ""                        # v0.36 : la phrase « semaine dernière -> 1re semaine »


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_start_of(d: date, ws: int = 0) -> date:
    """v0.50 : premier jour de la semaine de l'athlète (0 = lundi … 6 = dimanche) contenant `d`."""
    return d - timedelta(days=(d.weekday() - ws) % 7)


def day_in_week(week0: date, dow: int, ws: int = 0) -> date:
    """Le jour `dow` (0 = lundi) de la semaine de l'athlète qui commence le `week0`."""
    return week0 + timedelta(days=(dow - ws) % 7)


def _fmt_day(d: date) -> str:
    return f"{JOURS[d.weekday()]} {d:%d/%m}"


def _h(minutes: float) -> str:
    m = int(round(minutes / 5) * 5)
    return f"{m // 60} h {m % 60:02d}" if m >= 60 else f"{m} min"


# --------------------------------------------------------------------------- #
# Organisation de la semaine
# --------------------------------------------------------------------------- #
def week_layout(p: Profile, prefs: Prefs) -> dict:
    """Jours : sortie longue, deux séances de qualité non adjacentes (ni entre elles ni à la longue),
    footings jusqu'au nombre de jours voulu. Le week-end est évité pour la qualité s'il est limité.
    Jours de repos : jamais de course. Sport intense (foot, tennis...) : pas de qualité ce jour-là ni le
    lendemain ; on évite aussi d'y ajouter un footing si le nombre de jours est atteint ailleurs."""
    long_ = p.long_day
    rest = set(prefs.rest_days) - {long_}
    near = {(long_ - 1) % 7, long_, (long_ + 1) % 7}
    weekend = {5, 6} if prefs.weekend_max_min else set()
    hard_sport = {d for s in prefs.other_sports if s.hard for d in s.days}
    no_quality = hard_sport | {(d + 1) % 7 for d in hard_sport}
    sport_days = {d for s in prefs.other_sports for d in s.days}

    def pick(order, taken):
        for relax in (0, 1, 2, 3):   # 0 : tout respecter ; 1 : week-end ; 2 : lendemain du sport ; 3 : jour du sport
            for x in order:
                if x in taken or x in near or x in rest:
                    continue
                if (x in weekend and relax < 1 or x in no_quality and relax < 2
                        or x in hard_sport and relax < 3):
                    continue
                if any(abs(x - y) in (1, 6) for y in taken if y != long_):
                    continue
                return x
        return None

    n_days = min(p.days_per_week, 7 - len(rest))
    fixed = [x for x in dict.fromkeys(prefs.quality_days) if 0 <= x <= 6 and x != long_ and x not in rest]
    if fixed:                                    # v0.48 : son jour d'intensité (club...) est gardé tel quel
        quality = fixed[:1] if n_days <= 3 else fixed[:2]
        if len(quality) < 2 and n_days > 3:
            q2 = pick([3, 4, 2, 0, 5, 6, 1], {long_, *quality})
            quality += [q2] if q2 is not None else []
    else:
        q1 = pick([1, 2, 0, 3, 4, 5, 6], {long_})
        q2 = pick([3, 4, 2, 0, 5, 6, 1], {long_, q1} if q1 is not None else {long_})
        quality = [x for x in (q1, q2) if x is not None]
        if n_days <= 3:
            quality = quality[:1]
    used = {long_, *quality}
    easy_pref = [2, 4, 0, 5, 6, 1, 3]
    easy_pref = ([x for x in easy_pref if x not in weekend | sport_days] + [x for x in easy_pref if x in sport_days
                                                                             and x not in weekend]
                 + [x for x in easy_pref if x in weekend])
    easy = []
    for x in easy_pref:
        if len(used) + len(easy) >= n_days:
            break
        if x not in used and x not in rest:
            easy.append(x)
    return {"long": long_, "quality": sorted(quality), "easy": sorted(easy),
            "days": sorted(used | set(easy)), "rest": sorted(rest), "hard_sport": sorted(hard_sport),
            "after_sport": sorted(set(quality) & no_quality)}


def _strength_text(phase: str, kind: str, k: int, trail: bool) -> tuple[str, int]:
    """Contenu d'une séance de renfo selon la phase. kind = 'A' (force) ou 'B' (prévention) ;
    phase = recup | reprise | vitesse | leger. Retourne (description, minutes)."""
    gym = phase.endswith("+gym")
    phase = phase.replace("+gym", "")
    if phase == "recup":
        return ("Renfo doux, sans charge : mobilité hanches et chevilles, gainage (planche 3 × 30 s, latérale "
                "3 × 20 s par côté), pont fessier 3 × 12, mollets pieds au sol 2 × 15. Après un ultra, la force "
                "revient plus lentement que les sensations : on ne charge pas encore.", 20)
    if phase in ("reprise", "leger"):
        if gym:
            txt = ("Renfo général (adaptation), charges modérées, 2-3 répétitions en réserve : goblet squat 3 × 10, "
                   "soulevé de terre roumain 3 × 10, fente arrière 3 × 8 par jambe, mollets debout 3 × 12 et assis "
                   "3 × 12, planche Copenhague 3 × 20 s par côté.")
        else:
            txt = ("Renfo général sans matériel : squat 3 × 15, fente arrière 3 × 10 par jambe, pont fessier sur "
                   "une jambe 3 × 10, mollets sur une jambe 3 × 15 (sur une marche), planche Copenhague 3 × 20 s "
                   "par côté, gainage 3 × 40 s.")
        return (txt + (" Version courte : 2 séries de chaque." if phase == "leger" else ""), 25 if phase == "leger"
                else 30)
    # semaines de vitesse
    if kind == "A":
        reps = ["3 × 6", "4 × 5", "4 × 4"][min(k, 2)]
        if gym:
            txt = (f"Force : squat (ou box squat) {reps} lourd, 2 répétitions en réserve, descente contrôlée et "
                   "montée explosive, suivi de 3 squat jumps après chaque série ; soulevé de terre roumain 3 × 6 ; "
                   "fente bulgare 3 × 6 par jambe ; mollets debout sur une jambe 3 × 8 chargés ; planche "
                   "Copenhague 3 × 8 par côté. Au moins 3 h après la séance de course, ou à la suite.")
        else:
            txt = ("Force sans matériel : fente bulgare 4 × 8 par jambe (3 s en descente, sac à dos lesté si "
                   "possible), step-down excentrique 3 × 8 par jambe, mollets sur une jambe 3 × 15 lents, pont "
                   "fessier sur une jambe 3 × 12, planche Copenhague 3 × 8 par côté ; puis pliométrie : pogos "
                   "3 × 15, squat jumps 3 × 6.")
        return txt, 40 if gym else 35
    txt = ("Renfo prévention : mollets (debout et genou fléchi, 3 × 15), pieds (marche sur pointes, 2 × 30 s), "
           "hanches (abduction élastique ou couché 3 × 15), gainage 3 × 40 s, planche Copenhague 2 × 20 s"
           + ("; excentrique pour les descentes : step-down lent 3 × 10 par jambe" if trail else "")
           + ". Pliométrie légère : pogos 2 × 15.")
    return txt, 25


def _speed_keys(k: int, light: bool, pace: dict, durability: bool, double: bool) -> dict:
    """Séances clés d'une semaine de vitesse : liste de (intensité, description, minutes) par rôle."""
    vma = f" à {pace['vma']}/km" if pace.get("vma") else " à allure VMA (effort 9/10)"
    seuil = f" à {pace['seuil']}/km" if pace.get("seuil") else " au seuil (effort 7/10, une répétition en réserve)"
    mp = f" à {pace['marathon']}/km" if pace.get("marathon") else " à allure marathon"
    from .session_content import Workout, build
    kk = min(k, 2)
    # v0.50 : texte ET durée tirés de la même structure (session_content)
    if light:
        vw = Workout(20, 10, 30, 30, cooldown=10, short=True, pace=vma)
    else:
        sets, reps = [(2, 8), (2, 10), (3, 8)][kk]
        vw = Workout(20, reps, 30, 30, sets=sets, set_rest_s=180, cooldown=10, short=True, pace=vma)
    vma_s = ("vo2", *build(vw, 55 if light else 60 + 5 * kk))    # durées historiques : le contenu s'y adapte
    reps, on, rec = [(3, 8, 2), (4, 8, 2), (3, 12, 3)][kk] if not light else (2, 10, 2)
    seuil_s = [("threshold", *build(Workout(20, reps, on * 60, rec * 60, cooldown=10, pace=seuil),
                                   55 if light else 60 + 5 * kk))]
    if double and not light and k >= 1:
        am = [(5, 6), (5, 6), (4, 8)][kk]
        pm = [10, 10, 12][kk]
        seuil_s = [("threshold", *build(Workout(15, am[0], am[1] * 60, 60, cooldown=10, pace=seuil,
                                                prefix="Double seuil, séance du matin : ",
                                                suffix=" Contrôlé : effort 6-7/10, tu dois finir frais."), 60)),
                   ("threshold", *build(Workout(15, pm, 180, 60, cooldown=10, pace=seuil,
                                                prefix="Double seuil, séance du soir : ",
                                                suffix=" Même allure que le matin, pas plus vite."), 55))]
    long_txt = "en endurance" + (f", dont 2 × 10 min{mp} dans le dernier tiers" if durability and k >= 1
                                 and not light else "")
    # avec le double seuil, il passe en premier dans la semaine ; la VMA est la 2e séance.
    # 1re semaine d'intensité : ordre classique (le seuil tombe en fin de semaine = 1re séance dure)
    order = [seuil_s, [vma_s]] if double and k >= 1 else [[vma_s], seuil_s]
    return {"q": order, "long_txt": long_txt}


def recovery_window(d: Diagnosis) -> tuple[date | None, date | None]:
    """(reprise de l'intensité par défaut, au plus tôt) après le dernier gros effort."""
    big = d.recent_big_effort
    if not big:
        return None, None
    long_eff = big.time_s >= 24 * 3600
    return big.day + timedelta(days=28 if long_eff else 21), big.day + timedelta(days=21 if long_eff else 14)


# --------------------------------------------------------------------------- #
# v0.36 : la 1re semaine part de la dernière semaine réelle
# --------------------------------------------------------------------------- #
ANCHOR_LOW = 0.5          # semaine dernière < 50 % de l'habitude : reprise progressive
ANCHOR_HIGH = 1.3         # > 130 % de l'habitude : on assimile d'abord
ANCHOR_STEP = 0.05        # suite : +5 % si l'intensité est déjà là (sinon même volume : elle entre)
ANCHOR_MAX = 0.10         # suite : jamais plus de +10 % sur la semaine dernière
RETURN_SHARE = (0.6, 0.75)  # reprise : 60 % de l'habitude après 2 semaines légères, 75 % après une seule
ASSIM_SHARE = (0.85, 0.75)  # assimilation : au plus 85 % de l'habitude et 75 % de la semaine chargée
VERY_LONG_MIN = 150       # « très longue sortie » : au moins 2 h 30 ...
VERY_LONG_RATIO = 1.3     # ... et 30 % de plus que la plus longue du mois d'avant
LONG_MAX = 150            # plafond de la sortie longue en bloc de vitesse (inchangé)
W1_FACTOR = 0.85          # part de `base` en 1re semaine de vitesse (cf. [0.85, 0.9, 0.95])


class LastWeek(BaseModel):
    """Ce que l'athlète a fait les 7 jours avant le plan, et ce qu'on en déduit pour la 1re semaine."""
    mode: Literal["suite", "reprise", "assimilation", "recup"]
    end: date                                  # dernier jour pris en compte (veille du plan)
    runs: int = 0
    minutes: float = 0.0
    km: float = 0.0
    dplus: int = 0
    long_min: float = 0.0                      # plus longue sortie de la semaine
    quality: int = 0
    prev_minutes: float = 0.0                  # la semaine d'avant (lissage)
    usual_min: float = 0.0                     # semaine type des 12 dernières semaines
    longest_30d_min: float = 0.0               # référence Frandsen
    target_min: float = 0.0                    # volume visé pour la 1re semaine (avant préférences)
    max_min: float = 0.0                       # plafond de la 1re semaine
    hard_from: date | None = None              # pas de séance dure avant ce jour
    big_run: RecentRun | None = None           # course ou très longue sortie dans les 7 jours


def last_week_anchor(d: Diagnosis, start: date) -> LastWeek | None:
    """Ancre de la 1re semaine : les 7 jours avant `start` (lissés avec les 7 d'avant) comparés à la semaine type.

    - suite (50 à 130 % de l'habitude) : 75 % semaine dernière + 25 % celle d'avant, +5 % si l'intensité est déjà
      pratiquée (qualité dans les 14 jours), au plus +10 % sur la semaine dernière ;
    - reprise (< 50 %, ou une seule sortie) : 75 % de l'habitude (60 % si les deux semaines sont légères), footing
      seulement pendant 7 jours ;
    - assimilation (> 130 %, course ou très longue sortie) : min(85 % de l'habitude, 75 % de la semaine), pas de
      séance dure les 3 premiers jours (ni dans les 4 jours qui suivent la course) ;
    - recup : gros effort récent (> 6 h), la récupération existante s'applique telle quelle.
    None si le bilan ne contient pas le détail des sorties, ou s'il est trop ancien (plus de 8 jours)."""
    rr = d.recent_runs
    usual = d.typical_week_min
    if rr is None or not usual or (start - d.today).days > 8:
        return None
    end = min(start - timedelta(days=1), d.today)

    def win(a: int, b: int) -> list[RecentRun]:
        return [r for r in rr if end - timedelta(days=a) <= r.day <= end - timedelta(days=b)]

    last, prev = win(6, 0), win(13, 7)
    before = win(36, 7)
    a = LastWeek(mode="suite", end=end, runs=len(last), minutes=sum(r.minutes for r in last),
                 km=round(sum(r.km for r in last), 1), dplus=sum(r.dplus for r in last),
                 long_min=max((r.minutes for r in last), default=0.0), quality=sum(r.quality for r in last),
                 prev_minutes=sum(r.minutes for r in prev), usual_min=usual,
                 longest_30d_min=max((r.minutes for r in win(29, 0)), default=0.0))
    ref_before = max((r.minutes for r in before), default=0.0)
    big = [r for r in last if r.race or (r.minutes >= VERY_LONG_MIN and r.minutes >= VERY_LONG_RATIO * ref_before)]
    a.big_run = max(big, key=lambda r: r.minutes, default=None)
    if d.recent_big_effort:
        a.mode = "recup"
    elif a.minutes < ANCHOR_LOW * usual or (a.runs <= 1 and d.typical_days_per_week >= 3):
        a.mode = "reprise"
        share = RETURN_SHARE[0] if a.minutes + a.prev_minutes < ANCHOR_LOW * usual else RETURN_SHARE[1]
        a.target_min = a.max_min = max(a.minutes, share * usual)
        a.hard_from = start + timedelta(days=7)
    elif a.minutes > ANCHOR_HIGH * usual or a.big_run:
        a.mode = "assimilation"
        a.target_min = a.max_min = max(RETURN_SHARE[0] * usual,
                                       min(ASSIM_SHARE[0] * usual, ASSIM_SHARE[1] * a.minutes))
        a.hard_from = max(start + timedelta(days=3),
                          a.big_run.day + timedelta(days=4) if a.big_run else start)
    else:
        smooth = 0.75 * a.minutes + 0.25 * a.prev_minutes
        intensity_known = any(r.quality for r in win(13, 0))
        a.max_min = (1 + ANCHOR_MAX) * a.minutes
        a.target_min = min(smooth * (1 + ANCHOR_STEP if intensity_known else 1.0), a.max_min)
    return a


def frandsen_cap(a: LastWeek) -> int:
    """Plus longue sortie permise la 1re semaine : 110 % de la plus longue des 30 derniers jours
    (pas de contrôle sous 50 min, comme les garde-fous)."""
    return int(max(R("session.peak_floor_min"), R("session.peak_warn_ratio") * a.longest_30d_min) // 5 * 5)


def _last_week_what(a: LastWeek, trail: bool) -> str:
    bits = [f"{a.runs} sortie{'s' if a.runs > 1 else ''}", _h(a.minutes)]
    if a.km:
        bits.append(f"{a.km:.0f} km")
    if a.runs > 1 and a.long_min:
        bits.append(f"sortie longue {_h(a.long_min)}")
    if trail and a.dplus >= 100:
        bits.append(f"{_km(a.dplus)} m D+")
    if a.quality:
        bits.append(f"{a.quality} séance{'s' if a.quality > 1 else ''} de qualité")
    return ", ".join(bits)


def last_week_sentence(a: LastWeek, w1: float, intensity_from: date, trail: bool) -> str:
    """La phrase qui explique à l'athlète d'où part la 1re semaine (`w1` = minutes prévues sur les 7 premiers
    jours du plan, hors renfo et bonus)."""
    what = _last_week_what(a, trail)
    pct = round((w1 / a.minutes - 1) * 100) if a.minutes else 0
    if a.mode == "suite":
        if pct >= -10:
            tail = "on repart de là (même volume)" if abs(pct) <= 2 else f"on repart de là ({pct:+d} %)"
        else:
            tail = f"première semaine à {_h(w1)} ({pct:+d} %), le temps que l'intensité s'installe"
        return f"La semaine dernière : {what} → {tail}."
    if a.mode == "reprise":
        head = ("Aucune sortie ces 7 derniers jours" if not a.runs
                else f"Semaine dernière très légère ({what})")
        return (f"{head} : la première semaine est une reprise progressive, en footing facile ({_h(w1)}, environ "
                f"{round(w1 / a.usual_min * 100)} % de ton habitude), l'intensité revient le {_fmt_day(intensity_from)}.")
    if a.mode == "assimilation":
        if a.big_run:
            head = f"Tu sors de {a.big_run.name} ({_fmt_day(a.big_run.day)}, {_h(a.big_run.minutes)})"
        else:
            head = (f"Semaine dernière très chargée ({what}, {round((a.minutes / a.usual_min - 1) * 100):+d} % sur ton "
                    "habitude)")
        return (f"{head} : la première semaine sert d'abord à assimiler ({_h(w1)}), pas de séance dure avant le "
                f"{_fmt_day(intensity_from)}.")
    return f"La semaine dernière : {what}. Vu ton gros effort récent, on commence par récupérer avant de reconstruire."


def merge_club_sport(prefs: Prefs) -> Prefs:
    """v0.52 : « club de trail le mardi (intense) » noté comme autre sport ET séance d'intensité au club le mardi
    sont la même séance : l'autre sport est retiré ces jours-là (sinon séance dure fantôme et doubles absurdes)."""
    if not (prefs.club_quality and prefs.quality_days and prefs.other_sports):
        return prefs
    keep = []
    for o in prefs.other_sports:
        if re.search(r"\bclub\b", o.name, re.I) and (not o.days or set(o.days) & set(prefs.quality_days)):
            rest = [x for x in o.days if x not in prefs.quality_days]
            if rest:
                keep.append(o.model_copy(update={"days": rest}))
            continue
        keep.append(o)
    return prefs if len(keep) == len(prefs.other_sports) and keep == prefs.other_sports else \
        prefs.model_copy(update={"other_sports": keep})


def initial_block(d: Diagnosis, p: Profile, start: date, prefs: Prefs | None = None,
                  done_week: tuple[float, float, float] | None = None) -> Block:
    """`done_week` (v0.49) : minutes, km et D+ déjà courus la semaine de `start`, avant `start` (plafond hebdo)."""
    from .week_max import describe as wm_describe
    from .week_max import fit_plan, from_prefs
    prefs = merge_club_sport(prefs or Prefs())
    wm = from_prefs(prefs, d)
    notes: list[str] = []
    ws = prefs.week_start % 7                    # v0.50 : les semaines du bloc suivent celles de l'athlète
    start_monday = week_start_of(start, ws)
    default_int, floor = recovery_window(d)
    if default_int is None:
        default_int = floor = start
    big = d.recent_big_effort
    if prefs.resume_from:
        resume = max(start, prefs.resume_from)
        intensity_from = max(floor, resume)
        if intensity_from > resume and big:
            gap = (floor - big.day).days
            notes.append(f"Reprise le {_fmt_day(resume)} : d'accord pour le volume. L'intensité attend le "
                         f"{_fmt_day(intensity_from)} ({gap // 7} semaines après {big.name.strip()}, c'est le "
                         "minimum que je m'autorise) : avant, footings et lignes droites.")
    else:
        intensity_from = max(start, default_int)
        resume = max(start, week_start_of(intensity_from, ws) - timedelta(days=7)) if big else start
    int_monday = week_start_of(intensity_from, ws)

    typical = d.typical_week_min or 300
    # v0.18 : l'historique dit jusqu'où il est déjà monté (85 % de sa plus grosse semaine des 12 derniers mois)
    hist_cap = 0.85 * d.history.peak_week_12m_min if d.history.peak_week_12m_min else 0
    cap_abs = max(720, min(900, hist_cap)) if prefs.likes_volume else 600
    base = min(0.8 * typical, cap_abs) * min(1.3, max(0.6, prefs.volume_factor))
    if prefs.likes_volume and hist_cap > 720 and 0.8 * typical > 720:
        notes.append(f"Tu as déjà tenu {_h(d.history.peak_week_12m_min)} sur une semaine cette année : je m'autorise "
                     f"à monter jusqu'à {_h(cap_abs)}, par paliers.")
    if prefs.weekly_hours:
        wanted = prefs.weekly_hours * 60 / 0.9
        cap = max(min(1.2 * typical, 780), min(0.9 * d.history.peak_week_12m_min, 900))
        if wanted > cap:
            notes.append(f"{prefs.weekly_hours:g} h par semaine, c'est plus que ce que ton historique justifie "
                         f"en introduisant de l'intensité : je plafonne à {_h(cap * 0.9)}.")
        base = max(180, min(wanted, cap))
    if wm:                                       # v0.49 : le volume de départ tient déjà dans son maximum
        eq = [x for x in (wm.minutes, wm.km * 60 / wm.kmh if wm.km else None) if x]
        if eq:
            base = min(base, min(eq) / 0.95)
    # v0.36 : la 1re semaine de vitesse part de la semaine réelle (W1_FACTOR = part de `base` en semaine 1)
    anchor = last_week_anchor(d, start)
    vf = min(1.3, max(0.6, prefs.volume_factor))
    if anchor and anchor.mode in ("suite", "assimilation"):
        base = (min(base, anchor.max_min / W1_FACTOR) if prefs.weekly_hours
                else min(anchor.target_min / W1_FACTOR, cap_abs) * vf)
    if anchor and anchor.hard_from and anchor.hard_from > intensity_from:
        intensity_from = anchor.hard_from
        int_monday = week_start_of(intensity_from, ws)
    pace = d.paces or {}
    durability = d.marathon_gap is not None and d.marathon_gap > 0.06
    lay = week_layout(p, prefs)
    days = lay["days"]
    for q in lay["after_sport"]:
        sp = next((o.name for o in prefs.other_sports if o.hard and (q in o.days or (q - 1) % 7 in o.days)), "")
        notes.append(f"Avec tes jours de repos et ton {sp}, la seule place pour la 2e séance dure est le {JOURS[q]}, "
                     f"tout près du {sp} : vas-y tranquille ce jour-là, ou dis-moi si tu préfères une seule séance "
                     "dure par semaine.")
    wk_cap = prefs.weekend_max_min
    recov_order = [x for x in (1, 3, p.long_day, 5, 4, 2, 0, 6) if x in days]
    n_speed = max(2, min(6, prefs.speed_weeks))

    def cap_day(dow: int, minutes: int) -> int:
        return min(minutes, wk_cap) if wk_cap and dow in (5, 6) else minutes

    # vacances / semaines chargées : jours sans course, ou semaine allégée
    busy_off: set[date] = set()
    busy_light: dict[date, str] = {}
    for b in prefs.busy:
        if b.end < start:
            continue
        if b.kind == "off":
            busy_off |= {b.start + timedelta(days=i) for i in range((b.end - b.start).days + 1)}
        else:
            m0 = week_start_of(b.start, ws)
            while m0 <= b.end:
                busy_light[m0] = b.label or "semaine chargée"
                m0 += timedelta(weeks=1)

    pre = []
    m = start_monday
    while m < int_monday:
        pre.append(("Récupération" if m + timedelta(days=6) < resume else "Reprise", m))
        m += timedelta(weeks=1)
    n_recov = sum(1 for k, _ in pre if k == "Récupération")

    sessions: list[PlannedSession] = []
    weeks: list[dict] = []
    r = 0
    for kind, monday in pre:
        i = 0
        if kind == "Récupération":
            n = min(len(days), [3, 4, len(days)][min(r, 2)])
            cap = [45, 60, 75][min(r, 2)]
            share = ([0.45] if n_recov == 1 else [0.35, 0.55, 0.75])[min(r, 2)]
            per = max(30, min(cap, round(base * share / n / 5) * 5))
            for dow in sorted(recov_order[:n]):
                sessions.append(_s(monday, dow, "z1", cap_day(dow, per), "Footing facile, effort 3/10 (vélo ou "
                                   "marche possible si les jambes sont lourdes)", i, ws=ws))
                i += 1
            r += 1
        else:
            active = [x for x in days if day_in_week(monday, x, ws) >= resume]
            per = max(35, min(75, round(base * 0.75 / max(len(active), 1) / 5) * 5))
            if anchor and anchor.mode == "reprise":     # v0.36 : retour après une semaine très légère
                per = max(30, min(60, round(anchor.target_min * vf / max(len(days), 1) / 5) * 5))
            for j, dow in enumerate(active):
                desc = "Footing facile, effort 3/10" + (", + 6 lignes droites de 20 s (relâché, pas un sprint)"
                                                        if j == 1 else "")
                sessions.append(_s(monday, dow, "z2", cap_day(dow, per), desc, i, ws=ws))
                i += 1
        weeks.append({"start": monday.isoformat(), "kind": kind})

    # v0.38 : phase spécifique (8 semaines avant l'affûtage de l'objectif principal), voir race_specific
    from .race_specific import (
        REHEARSAL_MAX,
        SPEC_LONG_MAX,
        in_window,
        rehearsal_week,
        shape_for,
        specific_sessions,
        window,
    )
    shape, spec_a, spec_b = None, None, None
    if p.goal_kind != "route" and p.goal_date:
        spec_a, spec_b = window(p.goal_date)
        if start < spec_b and int_monday + timedelta(weeks=n_speed) > spec_a:
            shape = shape_for(p.goal_name, p.goal_date, p.goal_distance_km, p.goal_dplus_m, p.goal_kind)
    reh_monday = rehearsal_week(p.goal_date, [int_monday + timedelta(weeks=k) for k in range(n_speed)]) if shape else None
    k_spec, n_spec = 0, 0
    prev_long = 0.0                                  # la 1re semaine part de la sortie longue du bloc, puis +10 %/sem.
    spec_cap = min(1.0, vf)                          # « moins de volume » raccourcit aussi les sorties spécifiques

    double_started = None
    # v0.36 : après une reprise, la 1re semaine de vitesse reste dans les +30 % habituels
    prev_min = anchor.target_min * vf if anchor and anchor.mode == "reprise" and pre else None
    capped = False
    for k in range(n_speed):
        monday = int_monday + timedelta(weeks=k)
        busy_lbl = busy_light.get(monday)
        light = n_speed >= 4 and (k == n_speed - 1 or k % 4 == 3) or busy_lbl is not None
        week_min = base * (0.7 if light else [0.85, 0.9, 0.95][min(k, 2)])
        bridge = k == 0 and bool(pre) and pre[-1][0] == "Récupération"
        if bridge:
            week_min *= 0.75
            notes.append("Comme tu enchaînes directement après la récupération, la première semaine reste "
                         "modérée (volume -25 %, sortie longue limitée à 1 h 45).")
        # progression : pas plus de +30 % d'une semaine à l'autre (hors semaine allégée)
        if prev_min and not light and week_min > prev_min * 1.3:
            week_min, capped = prev_min * 1.3, True
        keys = _speed_keys(k, light, pace, durability, prefs.double_threshold)
        if prefs.double_threshold and k >= 1 and not light and double_started is None:
            double_started = monday
        long_min = (round(min(150, max(75, week_min * 0.28)) / 5) * 5 if not light
                    else round(min(100, max(40, week_min * 0.25)) / 5) * 5)       # v0.48 : 40 min au moins
        if bridge:
            long_min = min(long_min, 105)
        if anchor and anchor.mode == "suite" and not light and not bridge:
            # v0.36 : la sortie longue ne repart pas de zéro : proche de celle de la semaine dernière
            long_min = max(long_min, round(min(anchor.long_min, LONG_MAX) / 5) * 5)
        long_min = cap_day(p.long_day, long_min)
        plan: dict[int, list[tuple[str, str, int]]] = {p.long_day: [("long", f"Sortie longue {keys['long_txt']}",
                                                                      long_min)]}
        for qd, sess in zip(lay["quality"], keys["q"]):
            plan[qd] = [(a, b, cap_day(qd, c)) for a, b, c in sess]
            if qd in prefs.quality_days and prefs.club_quality:      # v0.48 : la séance du club, telle quelle
                plan[qd] = [("vo2", CLUB_TEXT, cap_day(qd, prefs.quality_minutes or 75))]
            elif qd in prefs.quality_days and prefs.quality_minutes and len(sess) == 1:
                plan[qd] = [(sess[0][0], sess[0][1], cap_day(qd, prefs.quality_minutes))]
        used = sum(x[2] for v in plan.values() for x in v)
        easy = lay["easy"]
        per = max(35, min(75, round((week_min - used) / max(len(easy), 1) / 5) * 5)) if easy else 0
        extras = ["+ 6 lignes droites de 20 s", "+ 8 sprints de 10 s en côte, récup en marchant"]
        for j, dow in enumerate(easy):
            plan[dow] = [("z2", "Footing en endurance" + (f" {extras[j]}" if j < 2 and not light else ""),
                          cap_day(dow, per))]
        spec_week = shape is not None and monday + timedelta(days=6) >= spec_a and monday < spec_b
        if spec_week:                                  # v0.38 : les séances clés ressemblent à la course
            sk = specific_sessions(shape, k_spec, (p.goal_date - monday).days // 7, light,
                                   prefs.double_threshold and k >= 1, reh_monday == monday, pace)
            k_spec += 1
            n_spec += 1

            def inside(dow: int) -> bool:
                return in_window(day_in_week(monday, dow, ws), p.goal_date)
            for qd, sess in zip(lay["quality"], sk["q"]):
                if inside(qd):
                    plan[qd] = [(a, b, cap_day(qd, c)) for a, b, c in sess]
            if inside(p.long_day):
                if light:
                    lm = min(SPEC_LONG_MAX, max(long_min, prev_long * 0.65)) if prev_long else long_min
                else:
                    cap_l = (REHEARSAL_MAX if sk["rehearsal"] else SPEC_LONG_MAX) * spec_cap
                    lm = min(cap_l, max(long_min, prev_long * 1.10)) if prev_long else long_min
                    lm = max(lm, min(long_min, cap_l))
                lm = cap_day(p.long_day, int(lm // 5 * 5))      # arrondi vers le bas : jamais > +10 %
                plan[p.long_day] = [("long", sk["long"][1], lm)]
                if not light:
                    prev_long = lm
                # enchaînement le week-end : seulement si la sortie longue est le samedi et le week-end non limité
                nxt = (p.long_day + 1) % 7
                if (p.long_day == 5 and not wk_cap and nxt in easy and inside(nxt) and not light
                        and not sk["rehearsal"] and (p.goal_date - monday).days // 7 in (5, 6, 7)):
                    plan[nxt] = [("z2", "Footing dos-à-dos en terrain vallonné, sur les jambes de la veille : facile, "
                                  f"marche active dans les côtes (habituer le corps à la fatigue de {shape.short}).",
                                  cap_day(nxt, 75))]
            # les footings absorbent l'allongement de la sortie longue (le volume de la semaine ne s'emballe pas)
            excess = sum(x[2] for v in plan.values() for x in v) - week_min
            for dow in sorted(easy, key=lambda x: -plan[x][0][2] if x in plan else 0):
                if excess <= 0:
                    break
                if dow in plan and plan[dow][0][0] == "z2" and not plan[dow][0][1].startswith("Footing dos-à-dos"):
                    cut = min(plan[dow][0][2] - 30, int(-(-excess // 5) * 5))
                    if cut > 0:
                        plan[dow] = [(plan[dow][0][0], plan[dow][0][1], plan[dow][0][2] - cut)]
                        excess -= cut
        # doubles : footings du matin, seulement si le volume voulu n'est pas atteint
        if prefs.double_days and not light and not bridge:
            left = week_min - sum(x[2] for v in plan.values() for x in v)
            for dow in sorted(prefs.double_days):
                if left < 30 or dow == p.long_day or dow not in plan or len(plan[dow]) > 1:
                    continue
                if wk_cap and dow in (5, 6):
                    continue
                add = int(min(45, max(30, round(left / 5) * 5)))
                plan[dow].insert(0, ("z1", "Footing du matin (double), très facile, effort 2-3/10", add))
                left -= add
        # avant la date d'intensité : la qualité devient un footing avec lignes droites
        for dow, sess in plan.items():
            if day_in_week(monday, dow, ws) < intensity_from:
                plan[dow] = [("z2", "Footing en endurance + 6 lignes droites de 20 s (l'intensité reprend le "
                              f"{intensity_from:%d/%m})", min(mn, 60)) if inten in ("vo2", "threshold")
                             else (inten, desc, mn) for inten, desc, mn in sess]
                if len(plan[dow]) == 2 and all(x[0] == "z2" for x in plan[dow]):
                    plan[dow] = plan[dow][:1]
        i = 0
        for dow in sorted(plan):
            for inten, desc, mn in plan[dow]:
                sessions.append(_s(monday, dow, inten, mn, desc, i, ws=ws))
                i += 1
        label = f"Spécifique {shape.short} {k_spec}" if spec_week else f"Vitesse {k + 1}"
        weeks.append({"start": monday.isoformat(), "kind": label + (
            f" (allégée : {busy_lbl})" if busy_lbl else " (allégée)" if light else ""), "k": k,
                      **({"specific": True} if spec_week else {})})
        prev_min = sum(x[2] for v in plan.values() for x in v)
    if capped:
        notes.append("Le volume monte par paliers (+30 % maximum d'une semaine à l'autre) : le gros volume que "
                     "tu aimes arrive en 2 à 3 semaines.")
    if prefs.double_threshold and double_started:
        notes.append(f"Double seuil à partir de la semaine du {double_started:%d/%m} : pas la semaine où "
                     "l'intensité revient, et jamais en semaine allégée.")

    sessions = [s for s in sessions if s.day >= start]
    if anchor:                                   # v0.36 : Frandsen sur les 7 premiers jours
        cap = frandsen_cap(anchor)
        trimmed = []
        for idx, s in enumerate(sessions):
            if s.day < start + timedelta(days=7) and s.intensity not in ("rest", "race") and s.duration_min > cap:
                sessions[idx] = resize_session(s, cap)              # v0.50 : le texte suit la durée
                trimmed.append(s)
        if trimmed:
            notes.append(f"Première semaine : aucune sortie au-delà de {_h(cap)}, soit 110 % de ta plus longue "
                         "sortie des 30 derniers jours (au-delà, le risque de blessure augmente nettement).")
        # plafond de la 1re semaine (+10 % en suite) : les séances clés ont une durée fixe, on raccourcit les footings
        if anchor.mode != "recup":
            w1_idx = [i for i, s in enumerate(sessions) if s.day < start + timedelta(days=7)]
            excess = sum(sessions[i].duration_min for i in w1_idx) - anchor.max_min * vf
            long_floor = max(R("session.peak_floor_min"), 0.9 * anchor.long_min)
            for i in sorted((i for i in w1_idx if sessions[i].intensity in ("z1", "z2", "long")),
                            key=lambda i: (sessions[i].intensity == "long", -sessions[i].duration_min)):
                if excess <= 0:
                    break
                s = sessions[i]
                low = 30 if s.intensity != "long" else int(-(-long_floor // 5) * 5)   # footings, puis sortie longue
                cut = min(s.duration_min - low, int(-(-excess // 5) * 5))
                if cut > 0:
                    sessions[i] = s.model_copy(update={"duration_min": s.duration_min - cut,
                                                       "planned_load": estimate_load(s.intensity,
                                                                                     s.duration_min - cut)})
                    excess -= cut
    sessions, capped_note = _cap_to_recent(sessions, weeks, d, start, anchor, prefs)
    if capped_note:
        notes.append(capped_note)
    done = done_week or (0.0, 0.0, 0.0)
    keep = {s.session_id for s in sessions if s.description == CLUB_TEXT}
    if wm:                                       # v0.49 : jamais au-delà de son volume maximum (et du 115 %)
        sessions, touched = fit_plan(sessions, wm, start, done, keep)
        if touched:
            notes.append(f"Ton volume maximum ({wm_describe(wm.minutes, wm.km, wm.dplus)}"
                         + (", estimé d'après ton historique" if wm.inferred else "") + ") est respecté : "
                         "j'ai raccourci d'abord les footings, puis la sortie longue, "
                         + ("la semaine du " if len(touched) == 1 else "les semaines du ")
                         + ", du ".join(f"{m:%d/%m}" for m in touched[:4]) + (" et suivantes" if len(touched) > 4 else "")
                         + ".")
    sessions += _strength_sessions(sessions, weeks, p, prefs, lay, start)
    off = set(prefs.unavailable) | busy_off
    if prefs.bonus:
        sessions += _bonus_sessions(sessions, weeks, p, lay, start, off, prefs)
        if wm:                                   # la séance bonus aussi : retirée là où elle ferait dépasser
            sessions, _ = fit_plan(sessions, wm, start, done, keep, is_bonus)
    dropped = [s for s in sessions if s.day in off and s.intensity != "rest"]
    if dropped:
        notes.append("Jours sans séance comme demandé : "
                     + ", ".join(sorted({_fmt_day(s.day) for s in dropped})) + ".")
    sessions = [s for s in sessions if s.day >= start and s.day not in off]
    end = max((s.day for s in sessions), default=start)
    for ev in [Event(name=p.goal_name, date=p.goal_date, role="A")] + [e for e in p.events if e.role == "B"]:
        if ev.date and start <= ev.date <= end:
            sessions = [s for s in sessions if not (ev.date - timedelta(days=2) <= s.day <= ev.date)
                        or s.intensity in ("z1", "z2")]          # renfo compris : rien de lourd avant
            sessions.append(PlannedSession(session_id=f"start-{ev.date.isoformat()}-race", day=ev.date,
                                           intensity="race", duration_min=0, planned_load=0, is_race=True,
                                           description=ev.name))
            notes.append(f"{ev.name} tombe pendant le bloc : séances dures retirées les 2 jours avant.")
    for w in weeks:
        ws = date.fromisoformat(w["start"])
        wk = [s for s in sessions if ws <= s.day < ws + timedelta(days=7)]
        w["minutes"] = sum(s.duration_min for s in wk if s.intensity != "rest" and not is_bonus(s))
        w["strength"] = [s.duration_min for s in wk if s.intensity == "rest"]

    if busy_off & {start + timedelta(days=i) for i in range((end - start).days + 1)}:
        notes.append("Pendant tes jours sans course (vacances), rien n'est prévu ; la reprise juste après se fait "
                     "en footing.")
    last_week = ""
    if anchor:
        w1 = sum(s.duration_min for s in sessions if start <= s.day < start + timedelta(days=7)
                 and s.intensity not in ("rest", "race") and not is_bonus(s))
        last_week = last_week_sentence(anchor, w1, intensity_from, p.goal_kind == "trail" or d.trail_share >= 0.3)
    n_pre = len(pre)
    if n_spec:
        a_, b_ = spec_a, spec_b - timedelta(days=1)
        notes.append(f"Phase spécifique {shape.short} du {a_:%d/%m} au {b_:%d/%m} (les 8 semaines avant l'affûtage) : "
                     "côtes à la vitesse de course, descentes, allure course sur le roulant, sorties longues "
                     "vallonnées avec la nutrition du jour J"
                     + (", et la répétition générale" if reh_monday else "") + ".")
    summary = ((f"{n_pre} semaine(s) de récupération et de reprise, puis " if n_pre else "")
               + (f"{n_speed} semaines dont {n_spec} spécifiques {shape.short}" if n_spec else f"{n_speed} semaines de vitesse")
               + (", la dernière allégée" if n_speed >= 4 else "")
               + f". Intensité à partir du {_fmt_day(intensity_from)}.")
    sessions = [harmonize(s) for s in sessions]   # v0.50 : texte et durée toujours d'accord (filet de sécurité)
    return Block(sessions=sorted(sessions, key=lambda s: (s.day, s.session_id)), weeks=weeks,
                 intensity_from=intensity_from, resume_from=resume, summary=summary, notes=notes, layout=lay,
                 last_week=last_week)


CLUB_TEXT = ("Séance du club (fractionné) : suis la séance prévue par le club. Échauffe-toi bien (15-20 min "
             "tranquille), donne-toi sur les fractions (effort 8/10 : rapide, mais tu finis la dernière aussi bien que "
             "la première), récupère en trottinant entre deux, et termine par 10 min très faciles.")
FIRST_PLAN_CAP = 1.15     # v0.48 : 1re semaine du 1er plan ≤ 115 % de la moyenne des 4 dernières semaines
FIRST_PLAN_STEP = 0.10    # ensuite +10 % par semaine au plus (+30 % s'il a demandé plus de volume)


def recent_week_avg(d: Diagnosis, start: date) -> float | None:
    """Minutes de course par semaine sur les 28 jours avant `start` (sorties douteuses exclues par le bilan)."""
    if d.recent_runs is None:
        return None
    end = min(start - timedelta(days=1), d.today)
    runs = [r for r in d.recent_runs if end - timedelta(days=27) <= r.day <= end]
    if not runs:
        return None
    return sum(r.minutes for r in runs) / 4


def _cap_to_recent(sessions: list[PlannedSession], weeks: list[dict], d: Diagnosis, start: date,
                   anchor: LastWeek | None, prefs: Prefs) -> tuple[list[PlannedSession], str]:
    """v0.48 : le 1er plan part de ce qu'il fait vraiment. Semaine 1 ≤ 115 % de la moyenne des 4 dernières
    semaines, puis +10 % par semaine au plus (+30 % s'il a demandé plus de volume, règle habituelle). On raccourcit
    d'abord les footings (30 min minimum), puis on les retire, puis la sortie longue (40 min minimum), enfin une
    séance de qualité qui n'est pas celle du club (45 min minimum). Hors récupération après un gros effort."""
    avg = recent_week_avg(d, start)
    if not avg or avg < 30 or (anchor and anchor.mode == "recup") or d.recent_big_effort:
        return sessions, ""
    cap = FIRST_PLAN_CAP * avg
    if anchor and anchor.mode == "reprise":
        cap = max(cap, anchor.target_min * min(1.3, max(0.6, prefs.volume_factor)))
    step = 0.30 if (prefs.likes_volume or prefs.weekly_hours) else FIRST_PLAN_STEP
    out = list(sessions)
    trimmed = False
    allowed = cap
    for w in weeks:
        if w["kind"].startswith("Récup"):
            continue
        ws = date.fromisoformat(w["start"])
        idx = [i for i, s in enumerate(out) if s is not None and ws <= s.day < ws + timedelta(days=7)
               and s.intensity not in ("rest", "race") and not is_bonus(s)]
        if not idx:
            continue
        partial = ws < start                    # 1re semaine entamée : plafond au prorata des jours restants
        limit = allowed * (ws + timedelta(days=7) - start).days / 7 if partial else allowed
        total = sum(out[i].duration_min for i in idx)
        excess = total - limit
        if excess > 0:
            trimmed = True

            def cut(i: int, floor: int) -> None:
                nonlocal excess
                s = out[i]
                c = min(s.duration_min - floor, int(-(-excess // 5) * 5))
                if c > 0:
                    out[i] = resize_session(s, s.duration_min - c)          # v0.50 : le texte suit la durée
                    excess -= c
            easy = sorted((i for i in idx if out[i].intensity in ("z1", "z2")), key=lambda i: -out[i].duration_min)
            for i in easy:
                if excess > 0:
                    cut(i, 30)
            for i in sorted(easy, key=lambda i: out[i].duration_min):
                if excess > 0 and len({out[j].day for j in idx if out[j] is not None}) > 2:
                    excess -= out[i].duration_min
                    out[i] = None                                           # type: ignore[call-overload]
            idx = [i for i in idx if out[i] is not None]
            for i in (i for i in idx if out[i].intensity == "long"):
                if excess > 0:
                    cut(i, 40)
            for i in (i for i in idx if out[i].intensity in ("vo2", "threshold", "tempo", "hills")
                      and out[i].description != CLUB_TEXT):
                if excess > 0:
                    cut(i, 45)
        kept = sum(out[i].duration_min for i in idx if out[i] is not None)
        if not partial and "allégée" not in w["kind"]:
            allowed = max(allowed, kept) * (1 + step)
    out = [s for s in out if s is not None]
    note = (f"Ton plan part de ce que tu fais vraiment : environ {_h(avg)} de course par semaine ces 4 dernières "
            f"semaines. La première semaine reste sous {_h(cap)} (+15 % au plus), puis le volume monte par petits "
            f"paliers (+{round(step * 100)} % par semaine au maximum).") if trimmed else ""
    return out, note


BONUS_TEXT = ("Bonus facultatif : footing très facile (effort 2-3/10), ou vélo, marche, mobilité. Seulement si tu "
              "te sens frais et que tu as le temps : la sauter ne change rien au plan.")


def is_bonus(s) -> bool:
    sid = s.session_id if hasattr(s, "session_id") else str(s)
    return sid.endswith("-bonus")


def _bonus_sessions(sessions: list[PlannedSession], weeks: list[dict], p: Profile, lay: dict, start: date,
                    off: set, prefs: Prefs | None = None) -> list[PlannedSession]:
    """v0.25 : une séance bonus facultative par semaine de vitesse ou de reprise (pas en récupération ni en
    semaine allégée), un jour libre qui n'est ni un jour de repos, ni la veille d'une séance de qualité, ni dans les
    3 jours avant une course (la veille d'une sortie longue est possible : 30 min très faciles).
    Charge prévue 0 : elle ne compte ni dans le volume de la semaine ni dans le garde-fou ACWR."""
    rest = set(lay.get("rest", []))
    busy = {s.day for s in sessions}
    key_days = {s.day for s in sessions if s.intensity in ("threshold", "vo2", "hills", "tempo")}
    races = [d for d in [p.goal_date] + [e.date for e in p.events if e.role == "B" and e.date] if d]
    out = []
    for w in weeks:
        if w["kind"].startswith("Récup") or "allégée" in w["kind"]:
            continue
        ws = date.fromisoformat(w["start"])
        wanted = [x for x in (prefs.bonus_days if prefs else []) if 0 <= x <= 6]
        for i in wanted or (2, 4, 0, 5, 1, 3, 6):             # ses jours (v0.48), sinon mercredi, vendredi d'abord
            d = day_in_week(ws, i, prefs.week_start % 7 if prefs else 0)
            if (d < start or d in busy or d in off or d.weekday() in rest or d + timedelta(days=1) in key_days
                    or any(r - timedelta(days=3) <= d <= r for r in races)):
                continue
            out.append(PlannedSession(session_id=f"start-{d.isoformat()}-bonus", day=d, intensity="z1",
                                      duration_min=30, planned_load=0, description=BONUS_TEXT))
            break
    return out


def project_acwr(history_loads: list[float], block: Block, start: date) -> list[tuple[date, float]]:
    """ACWR projeté jour par jour sur le bloc (historique jusqu'à la veille de `start`)."""
    from .load_engine import pmc
    if not block.sessions:
        return []
    end = max(s.day for s in block.sessions)
    planned = [0.0] * ((end - start).days + 1)
    for s in block.sessions:
        if not s.is_race:
            planned[(s.day - start).days] += s.planned_load
    hist = history_loads or [0.0]
    seed = sum(hist[:14]) / max(1, min(14, len(hist)))
    states = pmc(hist + planned, ctl0=seed, atl0=seed, a7_0=seed, c28_0=seed)[len(hist):]
    return [(start + timedelta(days=i), st.acwr) for i, st in enumerate(states)]


def check_block(history_loads: list[float], block: Block, start: date) -> list[str]:
    """Garde-fou du bloc : l'ACWR ne doit pas dépasser la zone de vigilance une fois l'intensité reprise."""
    from .rules_registry import R
    warn = []
    hi = R("acwr.sweet_hi")
    peaks = [(d, a) for d, a in project_acwr(history_loads, block, start)
             if d >= block.intensity_from and a == a and a > hi]
    if peaks:
        d, a = max(peaks, key=lambda x: x[1])
        warn.append(f"ACWR projeté {a:.2f} le {d:%d/%m} (> {hi})")
    return warn


def _s(monday: date, dow: int, inten: str, minutes: int, desc: str, i: int, ws: int = 0) -> PlannedSession:
    day = day_in_week(monday, dow, ws)
    return PlannedSession(session_id=f"start-{day.isoformat()}-{i + 1}", day=day, intensity=inten,
                          duration_min=int(minutes), planned_load=estimate_load(inten, minutes),
                          description=desc)


def _strength_sessions(sessions: list[PlannedSession], weeks: list[dict], p: Profile, prefs: Prefs,
                       lay: dict, start: date) -> list[PlannedSession]:
    """Renfo (v0.15) : 1 séance douce en récupération, 2 en reprise et en vitesse (A = force, B = prévention),
    1 courte en semaine allégée. Placé le jour d'une séance de qualité (après la course : les jours durs restent
    durs, les jours faciles restent faciles), sinon un jour facile qui ne précède ni qualité ni sortie longue.
    Jamais un jour de repos, jamais les 2 jours avant une course."""
    if prefs.strength == "none" or prefs.strength_per_week <= 0:
        return []
    wsd = prefs.week_start % 7
    gym = "+gym" if prefs.strength == "gym" else ""
    trail = p.goal_kind == "trail"
    rest = set(lay.get("rest", []))
    races = [p.goal_date] + [e.date for e in p.events if e.role == "B" and e.date]
    out: list[PlannedSession] = []
    for w in weeks:
        monday = date.fromisoformat(w["start"])
        wk = [s for s in sessions if monday <= s.day < monday + timedelta(days=7)]
        by_day: dict[int, list[PlannedSession]] = {}
        for s in wk:
            by_day.setdefault(s.day.weekday(), []).append(s)
        hard = {d for d, v in by_day.items() if any(x.intensity in ("vo2", "threshold", "long", "hills", "tempo")
                                                    for x in v)}
        quality = sorted(d for d, v in by_day.items() if any(x.intensity in ("vo2", "threshold", "hills", "tempo")
                                                             for x in v))
        kind = w["kind"]
        if kind.startswith("Récup"):
            plan = [("recup", "B")]
        elif kind == "Reprise":
            plan = [("reprise", "B")] * min(2, prefs.strength_per_week)
        elif "allégée" in kind:
            plan = [("leger", "B")]
        else:
            plan = [("vitesse", "A"), ("vitesse", "B")][:min(2, prefs.strength_per_week)]

        def ok(d: int) -> bool:
            day = day_in_week(monday, d, wsd)
            if day < start or d in rest or any(0 <= (r - day).days <= 2 for r in races):
                return False
            nxt = (d + 1) % 7
            return nxt not in hard or d in quality          # pas la veille d'une séance dure (sauf jour de qualité)

        single_q = [d for d in quality if len(by_day[d]) == 1]          # pas le jour d'un double seuil
        easy = [d for d in sorted(by_day) if d not in hard]
        apart = prefs.strength_with_quality is False                    # v0.48 : jamais avec la séance dure
        if apart:
            order = prefs.strength_days + [d for d in (2, 6, 5, 0, 3, 4, 1) if d not in hard]
        elif plan[0][0] == "vitesse":
            order = prefs.strength_days + single_q + [d for d in easy if d not in single_q]
        else:
            order = prefs.strength_days + [2, 1, 3, 4, 0, 5, 6]
        chosen: list[int] = []
        for d in order:
            if len(chosen) == len(plan):
                break
            if d in chosen or not ok(d) or any(abs(d - c) < 2 for c in chosen):
                continue
            if apart and d in hard and d not in prefs.strength_days:
                continue
            if plan[0][0] != "vitesse" and d not in by_day and d not in prefs.strength_days and not apart:
                continue
            chosen.append(d)
        for (phase, kind_ab), d in zip(plan, chosen):          # 1er choix = la séance A (force)
            desc, mn = _strength_text(phase + gym, kind_ab, w.get("k", 0), trail)
            day = day_in_week(monday, d, wsd)
            out.append(PlannedSession(session_id=f"start-{day.isoformat()}-renfo", day=day, intensity="rest",
                                      duration_min=mn, planned_load=round(35 * mn / 60, 1), description=desc))
    return out


# --------------------------------------------------------------------------- #
# Présentation : titres de séance, semaines, messages du coach
# --------------------------------------------------------------------------- #
TITLES = {"z1": "Footing de récupération", "z2": "Footing", "vo2": "VMA courte (30/30)",
          "threshold": "Seuil", "long": "Sortie longue", "race": "Objectif", "hills": "Côtes", "tempo": "Tempo"}
# v0.38 : séances de la phase spécifique (titre = début de la description)
SPEC_TITLES = {"Côtes à l'allure de course": "Côtes à l'allure de course", "Durabilité en descente": "Descentes",
               "Allure course sur terrain roulant": "Allure course", "Répétition générale": "Répétition générale",
               "Sortie longue vallonnée": "Sortie longue vallonnée", "Footing dos-à-dos": "Footing dos-à-dos"}
FOCUS_TXT = {"vitesse": "la vitesse", "endurance de vitesse": "l'endurance de vitesse",
             "volume": "le volume", "équilibre": "l'équilibre actuel"}


def _renfo_title(desc: str) -> str:
    return re.split(r"[:,(]", desc, maxsplit=1)[0].strip()


def session_title(s: PlannedSession) -> str:
    if is_bonus(s):
        return "⭐ Bonus facultatif"
    if s.is_race:
        return "🏁 " + s.description
    if s.intensity == "rest":
        return "🏋️ " + _renfo_title(s.description)
    for k, t in SPEC_TITLES.items():
        if s.description.startswith(k):
            return t
    if s.description == CLUB_TEXT:
        return "Séance du club (fractionné)"
    if s.description.startswith("Double seuil, séance du matin"):
        return "Double seuil (matin)"
    if s.description.startswith("Double seuil, séance du soir"):
        return "Double seuil (soir)"
    if s.description.startswith("Footing du matin (double)"):
        return "Footing du matin"
    if s.intensity == "z2" and "lignes droites" in s.description:
        return "Footing + lignes droites"
    if s.intensity == "z2" and "côte" in s.description:
        return "Footing + sprints en côte"
    if s.intensity == "long" and "dernier tiers" in s.description:
        return "Sortie longue + allure marathon"
    return TITLES.get(s.intensity, s.intensity)


def session_meta(block: Block) -> dict[str, dict]:
    return {s.session_id: {"title": session_title(s), "detail": "" if s.is_race else s.description,
                           "kind": "renfo" if s.intensity == "rest" else s.intensity,
                           "optional": is_bonus(s),
                           "key": s.intensity in ("vo2", "threshold", "long", "race", "hills", "tempo")}
            for s in block.sessions}


def week_meta(block: Block) -> dict[str, dict]:
    out = {}
    q = [JOURS[x] for x in block.layout.get("quality", [1, 3])]
    lg = JOURS[block.layout.get("long", 6)]
    for w in block.weeks:
        k = w["kind"]
        start = date.fromisoformat(w["start"])
        wk = [s for s in block.sessions if start <= s.day < start + timedelta(days=7)]
        if k.startswith("Récup"):
            focus = "Récupérer : footings courts et faciles, aucune intensité. Le vélo ou la marche comptent aussi."
        elif k == "Reprise":
            focus = f"Reprise : footings et lignes droites. L'intensité revient le {block.intensity_from:%d/%m}."
        elif "allégée" in k:
            focus = "Semaine allégée (-30 %) : on assimile le travail des semaines précédentes."
        elif k.startswith("Spécifique"):
            focus = (f"Spécificité : la course se prépare sur son terrain. Côtes et allure de course le "
                     f"{' et le '.join(q)}, sortie longue vallonnée le {lg}, avec la nutrition du jour J.")
        else:
            focus = f"Développer la vitesse : qualité le {' et le '.join(q)}, sortie longue le {lg}."
        if k.startswith("Vitesse") and not any(s.intensity == "vo2" for s in wk) and "allégée" not in k:
            focus = ("Retour de l'intensité, en douceur : seuil, lignes droites dans les footings. "
                     "La VMA arrive la semaine suivante.")
        keys = [{"day": JOURS[s.day.weekday()].capitalize(), "text": f"{session_title(s)} · {s.duration_min} min"}
                for s in wk if s.intensity in ("vo2", "threshold", "long", "hills", "tempo")]
        out[w["start"]] = {"phase": k, "focus": focus, "keys": keys}
    if block.last_week and block.weeks:                  # v0.36 : d'où part la 1re semaine
        out[block.weeks[0]["start"]]["last_week"] = block.last_week
    return out


def _short(s: PlannedSession) -> str:
    """Libellé d'une séance pour le message du coach."""
    if s.is_race:
        return "🏁 " + s.description
    if is_bonus(s):
        return f"⭐ bonus facultatif {_h(s.duration_min)}"
    d = s.description
    if s.intensity == "rest":
        return f"🏋️ {_renfo_title(d).lower()} {_h(s.duration_min)}"
    if d.startswith("Double seuil, séance du matin"):
        return "double seuil matin " + re.search(r"\d+ × \d+ min", d)[0]
    if d.startswith("Double seuil, séance du soir"):
        return "soir " + re.search(r"\d+ × \d+ min", d)[0]
    if d.startswith("Footing du matin"):
        return f"footing du matin {_h(s.duration_min)}"
    if d == CLUB_TEXT:
        return f"séance du club (fractionné) {_h(s.duration_min)}"
    for k, t in SPEC_TITLES.items():                  # v0.38 : phase spécifique
        if d.startswith(k):
            return f"{t.lower()} {_h(s.duration_min)}"
    if s.intensity == "vo2":
        return "VMA " + re.sub(r"^\d+ min d'échauffement, | à .*$|, \d+ min.*$", "", d)
    if s.intensity == "threshold":
        return "seuil " + re.sub(r"^\d+ min d'échauffement, |, récup.*$|, \d+ min de retour.*$", "", d)
    if s.intensity == "long":
        return ("sortie longue + allure marathon " if "dernier tiers" in d else "sortie longue ") + _h(s.duration_min)
    extra = " + lignes droites" if "lignes droites" in d else " + côtes" if "côte" in d else ""
    return f"footing{extra} {_h(s.duration_min)}"


def recap_message(d: Diagnosis, first_name: str = "") -> str:
    """Premier message : le bilan des données, puis la question de l'échéance."""
    hello = f"Salut {first_name} !" if first_name else "Salut !"
    span = (f"du {d.first_day:%d/%m/%Y} au {d.last_day:%d/%m/%Y}" if d.first_day and d.last_day else "")
    lines = [f"{hello} J'ai lu tes {d.n_activities} activités Strava {span}, dont {d.n_runs} courses. Voici ce que j'en retiens.", ""]
    lines.append(f"📊 Sur les {d.weeks} dernières semaines : {d.run_hours_per_week:.1f} h et "
                 f"{d.run_km_per_week:.0f} km de course par semaine, {d.dplus_per_week:.0f} m D+, "
                 f"{d.trail_share * 100:.0f} % en trail.".replace(".", ",", 1))
    lines.append("")
    order = {"high": 0, "medium": 1, "info": 2}
    for f in sorted(d.findings, key=lambda x: order.get(x.severity, 3)):
        icon = {"high": "🔴", "medium": "🟠", "info": "🔵"}.get(f.severity, "•")
        lines.append(f"{icon} {f.title} — {f.detail}")
    lines.append("")
    if d.focus in ("vitesse", "endurance de vitesse"):
        lines.append(f"🎯 En résumé : ton endurance est solide, ce qui te manque c'est {FOCUS_TXT[d.focus]}. "
                     "C'est là-dessus qu'on va travailler.")
    else:
        lines.append(f"🎯 En résumé : la priorité, c'est {FOCUS_TXT.get(d.focus, d.focus)}.")
    lines += history_lines(d)
    big = d.recent_big_effort
    if big and d.days_since_big_effort is not None:
        lines.append(f"Mais d'abord, tu sors de {big.name.strip()} il y a {d.days_since_big_effort} jours : "
                     "on va laisser ton corps finir de récupérer avant de remettre de l'intensité.")
    lines += ["", "Avant de te proposer un plan : quelles sont tes prochaines échéances ? Dis-moi la course "
                  "principale, et s'il y en a d'autres, celles qui comptent moins."]
    return "\n".join(lines)


def _km(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def history_lines(d: Diagnosis) -> list[str]:
    """Bloc « ton historique » du bilan (v0.18), si l'historique dépasse un an."""
    from .diagnosis import fmt_time
    h = d.history
    if h.years < 1.2 or not h.runs:
        return []
    out = ["", f"📚 Ton historique ({str(h.years).replace('.', ',')} ans, {h.runs} sorties de course) :"]
    out.append("• Par année : " + " · ".join(f"{y.year} {_km(y.km)} km" for y in h.per_year[-6:]))
    if h.records:
        out.append("• Meilleurs chronos sur le plat : " + ", ".join(
            f"{r.label} {fmt_time(r.time_s)} ({r.day:%m/%Y})" for r in h.records))
    if h.ultras:
        out.append("• Ultras : " + " ; ".join(
            f"{u.name.strip()} ({u.distance_km:g} km" + (f", {_km(u.dplus_m)} m D+" if u.dplus_m else "")
            + f", {fmt_time(u.time_s)}, {u.day:%m/%Y})" for u in h.ultras[:3])
            + (f" et {len(h.ultras) - 3} autre(s)" if len(h.ultras) > 3 else ""))
    if h.peak_week_min:
        out.append(f"• Plus grosse semaine d'entraînement (hors courses) : {_h(h.peak_week_min)} (semaine du {h.peak_week_day:%d/%m/%Y})"
                   + (f" ; sur 12 mois, plus gros bloc de 4 semaines : {str(h.peak_4w_hours).replace('.', ',')} h"
                      if h.peak_4w_hours else ""))
    if h.gaps:
        g = max(h.gaps, key=lambda x: x.days)
        out.append(f"• Coupures de 3 semaines ou plus : {len(h.gaps)} (la plus longue : {g.days} jours, "
                   f"{MOIS_NOMS[g.start.month - 1]} {g.start.year})")
    return out


def history_brief(d: Diagnosis) -> str:
    """Résumé d'une ligne pour le contexte du coach."""
    from .diagnosis import fmt_time
    h = d.history
    if not h.runs:
        return ""
    bits = [f"Historique {str(h.years).replace('.', ',')} ans, {h.runs} sorties"]
    if h.per_year:
        bits.append("volume annuel " + ", ".join(f"{y.year} {_km(y.km)} km" for y in h.per_year[-4:]))
    if h.records:
        bits.append("records " + ", ".join(f"{r.label} {fmt_time(r.time_s)} ({r.day.year})" for r in h.records))
    if h.ultras:
        bits.append(f"{len(h.ultras)} ultra(s), le plus long {h.ultras[0].name.strip()} {h.ultras[0].distance_km:g} km")
    if h.peak_week_12m_min:
        bits.append(f"plus grosse semaine sur 12 mois {_h(h.peak_week_12m_min)}")
    if h.gaps:
        bits.append(f"{len(h.gaps)} coupure(s) de 3 semaines ou plus")
    return " ; ".join(bits) + "."


def events_message(main: Event, others: list[Event]) -> str:
    """Les échéances trouvées, à faire confirmer."""
    def line(e: Event) -> str:
        bits = [x for x in [f"{e.distance_km:g} km" if e.distance_km else "",
                            f"{e.dplus_m} m D+" if e.dplus_m else "",
                            f"{JOURS[e.date.weekday()]} {e.date:%d/%m/%Y}" if e.date else "date à préciser",
                            f"départ {e.start}" if e.start else ""] if x]
        src = f" (source : {e.source})" if e.source else ""
        note = f" — {e.note}" if e.note else ""
        return f"• {e.name} : {', '.join(bits)}{note}{src}"
    lines = ["Voici ce que j'ai trouvé :", "", "🎯 Objectif principal", line(main)]
    b = [e for e in others if e.role == "B"]
    c = [e for e in others if e.role == "C"]
    if b:
        lines += ["", "Courses secondaires"] + [line(e) for e in b]
    if c:
        lines += ["", "Plus tard"] + [line(e) for e in c]
    lines += ["", "C'est bien ça ? Vérifie surtout le D+ et la date : il existe souvent plusieurs formats "
                  "sur un même événement."]
    return "\n".join(lines)


def proposal_message(d: Diagnosis, p: Profile, block: Block, warnings: list[str] | None = None,
                     first: bool = True, today: date | None = None, taken: list[str] | None = None,
                     prefs: Prefs | None = None) -> str:
    """Le plan proposé, semaine par semaine, et la question d'ouverture."""
    today = today or date.today()
    lines = []
    if first:
        lines.append(f"Voici ce que je te propose pour démarrer, en route vers {p.goal_name} "
                     f"({JOURS[p.goal_date.weekday()]} {p.goal_date:%d/%m/%Y}, dans "
                     f"{(p.goal_date - today).days // 7} semaines).")
    else:
        lines.append("Voici le plan mis à jour.")
    if block.last_week:
        lines += ["", f"📅 {block.last_week}"]
    if taken:
        lines += ["", "Ce que j'ai pris en compte :"] + [f"✓ {t}" for t in taken]
    if block.notes:
        lines += ["", "Ce que j'ai adapté, et pourquoi :"] + [f"⚠ {n}" for n in block.notes]
    for n, w in enumerate(block.weeks, 1):
        ws = date.fromisoformat(w["start"])
        wk = [s for s in block.sessions if ws <= s.day < ws + timedelta(days=7)]
        st = w.get("strength") or []
        lines += ["", f"Semaine {n} — du {ws:%d/%m} au {ws + timedelta(days=6):%d/%m} · {w['kind']} · "
                      f"{_h(w['minutes'])} de course" + (f" + renfo {' + '.join(str(x) for x in st)} min" if st else "")]
        by_day: dict[date, list[str]] = {}
        for s in wk:
            by_day.setdefault(s.day, []).append(_short(s))
        for i in range(7):                                   # ses autres sports, pour voir l'ensemble
            names = [o.name for o in (prefs.other_sports if prefs else []) if i in o.days]
            if names:
                by_day.setdefault(day_in_week(ws, i, prefs.week_start % 7), []).append("(" + ", ".join(names) + ")")
        for day, items in sorted(by_day.items()):
            lines.append(f"   {JOURS_ABR[day.weekday()]} {' + '.join(items)}")
    lines.append("")
    rest = block.layout.get("rest") or []
    lines.append(f"{block.summary} {len(block.layout.get('days', []))} jours de course par semaine, sortie longue "
                 f"le {JOURS[p.long_day]}" + (f", repos le {' et le '.join(JOURS[x] for x in rest)}" if rest else "")
                 + ".")
    if any(is_bonus(s) for s in block.sessions):
        lines.append("⭐ = séance bonus facultative : à faire seulement si tu es frais, la sauter ne change rien.")
    if first and any(w.get("strength") for w in block.weeks):
        if prefs and prefs.strength_with_quality is False:
            lines.append("Le renfo se fait à la maison, sans matériel, un autre jour que ta séance d'intensité et "
                         "jamais la veille d'une séance dure : c'est ce qui protège le mieux des blessures.")
        else:
            lines.append("Le renfo est placé après les séances de qualité : les jours durs restent durs, les jours "
                         "faciles restent faciles. C'est ce qui protège le mieux des blessures.")
    if d.paces and first:
        lines.append(f"Les allures viennent de ta référence (VMA estimée ~{d.vma_kmh:.1f} km/h, à confirmer "
                     "par un test en fin de bloc).".replace(".", ",", 1))
    others = [e for e in p.events if e.role in ("B", "C")]
    if others and first:
        lines.append("")
        for e in others:
            when = f" ({e.date:%d/%m/%Y})" if e.date else ""
            what = ("course secondaire : on l'intégrera avec une semaine allégée avant" if e.role == "B"
                    else "noté pour après, on en reparlera")
            lines.append(f"📌 {e.name}{when} — {what}.")
    if first:
        lines.append("À la fin du bloc, je te propose la suite en fonction de tes retours.")
    if warnings:
        lines += ["", "⚠ " + " ; ".join(warnings)]
    lines += ["", CLOSING_FIRST if first else CLOSING_NEXT]
    return "\n".join(lines)


CLOSING_FIRST = ("Qu'est-ce que tu en dis ? Tout est ajustable : date de reprise, jours, repos, sortie longue, doubles, "
                 "volume, renfo, vacances…")
CLOSING_NEXT = "Ça te va comme ça ?"


# --------------------------------------------------------------------------- #
# Conversation : extraction (LLM ou heuristique) des réponses de l'athlète
# --------------------------------------------------------------------------- #
class EventX(BaseModel):
    name: str
    date: Date | None = None
    year: int | None = None                   # v0.25 : édition visée (« en 2027 ») quand la date n'est pas donnée
    role: Literal["A", "B", "C"] = "A"
    kind: Literal["trail", "route"] | None = None
    distance_km: float | None = None
    dplus_m: int | None = None
    note: str = ""


class EventEdit(BaseModel):
    """v0.25 : correction d'une échéance après l'accueil (« c'est pour 2027 », « enlève le marathon »)."""
    target: str = ""                          # nom de l'échéance existante visée ("" pour un ajout)
    action: Literal["update", "remove", "add"] = "update"
    name: str = ""
    date: Date | None = None
    year: int | None = None
    role: Literal["A", "B", "C"] | None = None
    kind: Literal["trail", "route"] | None = None
    distance_km: float | None = None
    dplus_m: int | None = None
    note: str = ""


class GoalEdit(BaseModel):
    edits: list[EventEdit] = Field(default_factory=list)
    reply: str = ""


class GoalsExtract(BaseModel):
    events: list[EventX] = Field(default_factory=list)
    confirmed: bool = False                   # l'athlète valide les échéances proposées
    days_per_week: int | None = None
    long_day: int | None = None
    reply: str = ""


class ProfileFields(BaseModel):
    """Réponses aux questions de l'accueil (v0.15), aussi modifiables pendant la discussion du plan."""
    days_per_week: int | None = None
    long_day: int | None = None               # 0 = lundi ... 6 = dimanche
    rest_days: list[int] = Field(default_factory=list)
    strength: Literal["gym", "bodyweight", "none"] | None = None
    strength_days: list[int] = Field(default_factory=list)
    other_sports: list[OtherSport] = Field(default_factory=list)
    strava_complete: bool | None = None
    hr_source: Literal["ceinture", "poignet", "aucune"] | None = None
    busy: list[Busy] = Field(default_factory=list)
    discipline: Literal["route", "trail", "mixte"] | None = None    # v0.25
    bonus: bool | None = None                                        # v0.25 : séances bonus facultatives
    # v0.48 : jours imposés (« le club le mardi, 1 h 15 », « la longue le vendredi », « le bonus le week-end »)
    quality_days: list[int] = Field(default_factory=list)
    club_quality: bool | None = None
    quality_minutes: int | None = None
    bonus_days: list[int] = Field(default_factory=list)
    strength_with_quality: bool | None = None
    no_other_sports: bool | None = None                              # « je ne fais pas d'autres sports »
    # v0.49 : volume maximum par semaine (« 6 h max », « pas plus de 50 km et 1500 m D+ »)
    max_week_hours: float | None = None
    max_week_km: float | None = None
    max_week_dplus: int | None = None
    # v0.50 : jour où commence sa semaine (« ma semaine commence le dimanche ») : 0 = lundi … 6 = dimanche
    week_start: int | None = None


class ProfileExtract(ProfileFields):
    skip: bool = False                        # « passe » : valeurs par défaut
    reply: str = ""


class TurnExtract(ProfileFields):
    intent: Literal["accept", "modify", "question", "reject", "other"]
    resume_from: date | None = None
    volume_factor: float | None = None
    weekly_hours: float | None = None
    speed_weeks: int | None = None
    unavailable: list[date] = Field(default_factory=list)
    weekend_max_min: int | None = None
    double_days: list[int] = Field(default_factory=list)
    double_threshold: bool | None = None
    likes_volume: bool | None = None
    goal_name: str | None = None
    goal_date: date | None = None
    goal_distance_km: float | None = None
    goal_dplus_m: int | None = None
    reply: str = ""


RECAP_SYSTEM = """Tu es un coach de course à pied et de trail. Tu accueilles un nouvel athlète dans l'application.
On te donne son bilan calculé à partir de ses données Strava (chiffres exacts) et un brouillon de message.
Réécris le brouillon pour qu'il sonne comme un coach humain : chaleureux, direct, en français, tutoiement.
Règles strictes :
- n'utilise QUE les chiffres, dates et noms du brouillon ; n'en invente aucun ; la VMA est une estimation ;
- garde l'ordre : ce que tu as lu, les constats, le résumé (le point à travailler), l'historique long s'il y en a un
  (garde ses chiffres, en une ou deux phrases), la récupération s'il y en a une ;
- termine EXACTEMENT par la question sur ses prochaines échéances ;
- pas de diagnostic médical. 280 mots maximum."""

GOAL_SYSTEM = """Tu es un coach de course à pied. L'athlète parle de ses prochaines échéances.
Extrais chaque course citée dans `events` :
- role : A = objectif principal (une seule), B = course secondaire ou « pour le plaisir », C = projet plus lointain
  ou pas encore choisi (ex. « un marathon en 2027 ») ;
- date (AAAA-MM-JJ) SEULEMENT si l'athlète la donne ; sinon vide (l'application la cherchera) ; s'il donne seulement
  l'année (« le marathon de Nice-Cannes 2027 », « l'an prochain »), mets-la dans `year` ;
- distance, D+, type (trail/route) s'il les donne ; `note` : son intention en quelques mots (« pour le plaisir »,
  « passer sous 3 h »).
Si on te fournit des `candidats` (échéances trouvées en ligne) et que l'athlète les valide, renvoie confirmed=true et
les candidats tels quels ; s'il corrige, renvoie les échéances corrigées (confirmed=false).
Il peut aussi dire combien de jours il s'entraîne (days_per_week) et le jour de sa sortie longue (long_day, 0 = lundi).
N'invente rien. `reply` : une phrase de coach qui accuse réception, sans chiffre que l'athlète n'a pas donné."""

GOAL_EDIT_SYSTEM = """Tu es un coach de course à pied. L'athlète a déjà un plan et des échéances (`echeances`, avec leur
nom, date et rôle : A = objectif principal, B = course secondaire, C = projet lointain). Son message corrige-t-il ces
échéances ? Remplis `edits` SEULEMENT s'il demande de changer une échéance :
- update : `target` = nom exact de l'échéance visée ; mets `date` (AAAA-MM-JJ) s'il la donne, sinon `year` s'il donne
  l'année (« c'est pour 2027 », « l'an prochain » = année suivante) ; `role` s'il change son importance ;
- remove : il ne la fait plus ; add : nouvelle course (name, date ou year, distance, D+ s'il les donne).
S'il parle d'autre chose (séance, fatigue, question), renvoie edits = [].
N'invente aucune date. `reply` : une phrase de coach qui accuse réception, sans annoncer de date que l'athlète n'a pas
donnée."""

TURN_SYSTEM = """Tu es un coach de course à pied et de trail. Tu viens de proposer un premier bloc d'entraînement et
vous en discutez. On te donne : la date du jour, le plan proposé, ses préférences actuelles, son bilan, des fiches
scientifiques et son dernier message.
Classe le message (`intent`) : accept (il valide tel quel), modify (il demande un ou plusieurs changements),
question, reject (refus sans précision), other.
Pour modify, remplis TOUS les champs correspondant à ce qu'il demande, et seulement ceux-là :
- resume_from : date de reprise sérieuse (AAAA-MM-JJ ; « lundi » = prochain lundi ; « la semaine prochaine » = lundi prochain) ;
- days_per_week (2-7, sorties fixes) ; long_day (0 = lundi ... 6 = dimanche : « sorties longues en semaine » sans jour
  = 2, mercredi) ; quality_days (jour de sa séance d'intensité à garder, club_quality si c'est au club,
  quality_minutes) ; bonus_days (jours de la sortie bonus facultative, « le week-end » = [5, 6]) ;
  strength_with_quality (false : pas de renfo le jour de l'intensité) ;
- weekend_max_min : s'il veut peu de temps le week-end (famille...), mets 60 sauf durée précise ;
- double_days : jours où il peut faire deux sorties (0 = lundi) ; double_threshold : true s'il aime le double seuil ;
- likes_volume : true s'il aime le gros volume ; volume_factor (plus ≈ 1.15, moins ≈ 0.85) ; weekly_hours ;
- speed_weeks (2-6) ; unavailable (dates) ; goal_* s'il change d'objectif principal ;
- rest_days (jours sans course) ; strength (gym | bodyweight | none) et strength_days ; other_sports (nom, jours,
  hard = sport intense ou à impacts) ; strava_complete ; hr_source (ceinture | poignet | aucune) ; busy (vacances ou
  semaines chargées : start, end, kind off = pas de course / light = un peu, label) ; discipline (route | trail |
  mixte) ; bonus (false s'il ne veut pas de séance bonus facultative) ;
- max_week_hours / max_week_km / max_week_dplus : le volume MAXIMUM qu'il peut faire par semaine (« je peux monter à
  7 h par semaine », « pas plus de 60 km », « 2000 m D+ max ») ; ne pas confondre avec weekly_hours (volume souhaité).
`reply` : 2 à 4 phrases de coach, tutoiement, qui répondent à CHAQUE point de son message (rien d'ignoré) et
expliquent brièvement le pourquoi en t'appuyant sur les fiches (cite l'auteur entre parenthèses). N'annonce pas les
nouvelles dates ni les volumes : le plan recalculé est affiché juste après ta réponse, avec ce qui a été pris en compte.
Ne termine PAS par une question et ne redemande pas ce qu'il veut changer : la question est posée à la fin du plan.
Si l'athlète refuse le renfo, respecte son choix sans insister.
La sécurité est appliquée par le code (après un effort de plus de 24 h, pas d'intensité avant 3 semaines ; double
seuil pas la semaine où l'intensité revient) : dis que tu entends qu'il se sent prêt et que le plan montre ce qui est
possible. Pas de diagnostic médical : en cas de douleur, conseille un professionnel de santé."""


PROFILE_SYSTEM = """Tu es un coach de course à pied et de trail. Tu as posé à un nouvel athlète des questions sur son
quotidien (jours, repos, renfo, autres sports, Strava et cardio, vacances) et il répond. Extrais UNIQUEMENT ce qu'il dit :
- days_per_week (2-7) = ses sorties FIXES de la semaine (une sortie « quand je peux » n'en fait pas partie : c'est
  le bonus) ; long_day (0 = lundi ... 6 = dimanche) = le jour de sa sortie la plus longue (s'il a deux sorties fixes
  dont une séance d'intensité, c'est l'autre jour) ; rest_days (jours sans course) ;
- quality_days : jour(s) de sa séance d'intensité habituelle (fractionné, club, piste...) ; club_quality = true si
  elle se fait au club ; quality_minutes = sa durée s'il la donne (« 1h15 » = 75) ;
- bonus_days : jours où il peut parfois ajouter une sortie (« le week-end » = [5, 6]) ; bonus = true dans ce cas ;
- strength_with_quality : false s'il ne veut pas de renfo le jour de sa séance d'intensité ;
- no_other_sports : true s'il dit ne faire aucun autre sport (une activité « seulement en vacances » n'est PAS un
  autre sport régulier : ne la mets pas dans other_sports) ;
- strength : gym (salle, muscu avec charges), bodyweight (à la maison, sans matériel), none (il n'en veut pas) ;
  strength_days s'il a déjà ses créneaux ;
- other_sports : nom en minuscules, jours, hard = true pour un sport intense ou à impacts (foot, tennis, crossfit,
  squash, sports collectifs...), false pour vélo tranquille, natation, yoga, escalade douce ; minutes_per_week si donné ;
- strava_complete : true s'il enregistre tout, false sinon ; hr_source : ceinture | poignet | aucune ;
- busy : périodes (AAAA-MM-JJ) ; kind = off s'il ne pourra pas courir, light s'il pourra courir un peu ; label court
  (« vacances », « déplacement pro ») ; min_runs s'il dit garder au moins N sorties ; « les vacances de Noël » sans
  dates = du samedi avant le 21 décembre, sur deux semaines ;
- discipline : route (il court surtout sur route), trail, ou mixte (les deux) ;
- bonus : false s'il ne veut pas de séance bonus facultative, true s'il en veut ;
- max_week_hours / max_week_km / max_week_dplus : seulement s'il donne le volume MAXIMUM qu'il peut faire par semaine ;
- week_start (0 = lundi ... 6 = dimanche) : le jour où commence SA semaine, seulement s'il le dit (« ma semaine
  commence le dimanche », « du dimanche au samedi » = 6) ;
- skip = true s'il veut passer les questions.
Date du jour fournie : « semaine du 14 » = lundi de cette semaine-là. N'invente rien : un champ non mentionné reste vide.
`reply` : 1 à 2 phrases de coach qui accusent réception (sans répéter chaque point : la liste est affichée après)."""


MOIS = {"janvier": 1, "janv": 1, "février": 2, "fevrier": 2, "févr": 2, "fev": 2, "mars": 3, "avril": 4, "avr": 4,
        "mai": 5, "juin": 6, "juillet": 7, "juil": 7, "août": 8, "aout": 8, "septembre": 9, "sept": 9,
        "octobre": 10, "oct": 10, "novembre": 11, "nov": 11, "décembre": 12, "decembre": 12, "déc": 12, "dec": 12}
_WD = {j: i for i, j in enumerate(JOURS)}


def _next_occurrence(day: int, month: int, today: date, year: int | None = None) -> date | None:
    try:
        if year:
            return date(year if year > 99 else 2000 + year, month, day)
        d = date(today.year, month, day)
        return d if d > today else date(today.year + 1, month, day)
    except ValueError:
        return None


def parse_date_fr(text: str, today: date, weekdays: bool = True) -> date | None:
    t = text.lower()
    m = re.search(r"\b(\d{1,2})\s*[/.-]\s*(\d{1,2})(?:\s*[/.-]\s*(\d{2,4}))?\b", t)
    if m:
        return _next_occurrence(int(m[1]), int(m[2]), today, int(m[3]) if m[3] else None)
    m = re.search(r"\b(1er|\d{1,2})\s+(" + "|".join(sorted(MOIS, key=len, reverse=True)) + r")\.?(?:\s+(\d{4}))?", t)
    if m:
        return _next_occurrence(1 if m[1] == "1er" else int(m[1]), MOIS[m[2]], today, int(m[3]) if m[3] else None)
    if "après-demain" in t or "apres-demain" in t:
        return today + timedelta(days=2)
    if "demain" in t:
        return today + timedelta(days=1)
    if "semaine prochaine" in t:
        return today + timedelta(days=7 - today.weekday())
    if weekdays:
        m = re.search(r"\b(" + "|".join(JOURS) + r")\b", t)
        if m:
            ahead = (_WD[m[1]] - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead)
    return None


def _num(s: str) -> float:
    return float(s.replace(" ", "").replace(" ", "").replace(",", "."))


def _common(t: str, today: date) -> dict:
    out: dict = {}
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*km\b", t)
    if m:
        out["goal_distance_km"] = _num(m[1])
    m = re.search(r"(\d[\d\s .]*)\s*m?\s*(?:de\s+)?(?:d\+|dénivel|denivel)", t)
    if m:
        out["goal_dplus_m"] = int(_num(m[1].replace(".", "")))
    m = re.search(r"\b([3-7])\s*(?:jours|séances|seances|sorties|fois)\b", t)
    if m:
        out["days_per_week"] = int(m[1])
    m = re.search(r"(?:longue|long)\D{0,12}\b(" + "|".join(JOURS) + r")\b", t)
    if m:
        out["long_day"] = _WD[m[1]]
    return out


def parse_goal(text: str, today: date, candidates: list[dict] | None = None) -> GoalsExtract:
    """Heuristique hors ligne : une échéance principale (la première citée), date si donnée."""
    if candidates and ACCEPT.search(text.lower()):
        return GoalsExtract(events=[EventX.model_validate(c) for c in candidates], confirmed=True,
                            reply="Parfait.")
    t = text.lower()
    c = _common(t, today)
    when = parse_date_fr(t, today, weekdays=False)
    name = re.split(r"\s(?:le|du|ce|en|au|,|:|—|-)\s+(?:\d|1er|" + "|".join(JOURS) + r")|\s\d{1,2}\s*[/.]|,|\(",
                    text.strip(), maxsplit=1)[0]
    name = re.sub(r"^(?:salut coach\s*!?\s*|c'est|ma prochaine échéance(?: c'est| est)?|mon objectif(?: c'est| est)?"
                  r"|je fais|je cours|je vise|ce sera|ça sera)\s+", "", name.strip(), flags=re.I).strip(" .!")
    kind = "trail" if ("trail" in t or (c.get("goal_dplus_m") or 0) > 500) else (
        "route" if re.search(r"marathon|semi|\b10 ?km\b|route", t) else None)
    year = _year_in(t, today)
    name = re.sub(r"\s*\b20\d\d\b\s*", " ", name).strip(" -") or name
    ev = EventX(name=name[:80] if len(name) >= 2 and not name[0].isdigit() else "Mon objectif", date=when, year=year,
                role="A", kind=kind, distance_km=c.get("goal_distance_km"), dplus_m=c.get("goal_dplus_m"))
    return GoalsExtract(events=[ev], days_per_week=c.get("days_per_week"), long_day=c.get("long_day"),
                        reply=f"Noté : {ev.name}." if when else "")


def _year_in(t: str, today: date) -> int | None:
    m = re.search(r"\b(20[2-9]\d)\b", t)
    if m and int(m[1]) >= today.year:
        return int(m[1])
    if re.search(r"l'an(n[ée]e)? prochaine?|l'ann[ée]e prochaine", t):
        return today.year + 1
    return None


_EDIT_VERB = re.compile(r"ajout|rajout|supprim|enl[eè]v|retir|annul|d[ée]cal|report|c'est pour|c'est en|c'est le|"
                        r"n'est pas|pas (cette|cet) ann[ée]e|mauvaise date|tu as mis|tu t'es tromp|erreur|plus la|"
                        r"je ne (la )?fais plus|je fais plus|chang\w* (la )?date|nouvel(le)? (objectif|course)", re.I)
_STOP = {"de", "du", "des", "la", "le", "les", "et", "en", "a", "au", "trail", "course", "the", "km"}


def _words(s: str) -> set[str]:
    from .race_lookup import _norm
    return {w for w in _norm(s).split() if len(w) > 2 and w not in _STOP and not w.isdigit()}


def mentions_event(text: str, events: list[Event]) -> Event | None:
    w = _words(text)
    best = max(events, key=lambda e: len(w & _words(e.name)), default=None)
    return best if best is not None and w & _words(best.name) else None


def looks_like_goal_edit(text: str, events: list[Event], today: date) -> bool:
    """Message qui corrige les échéances (sinon : conversation de coaching habituelle)."""
    t = text.lower()
    ev = mentions_event(t, events)
    if _EDIT_VERB.search(t) and (ev or re.search(r"objectif|[ée]ch[ée]ance|course|marathon|semi|ultra|trail", t)):
        return True
    return bool(ev and _year_in(t, today))


def parse_goal_edit(text: str, events: list[Event], today: date) -> GoalEdit:
    """Heuristique hors ligne : année ou suppression de l'échéance citée."""
    t = text.lower()
    ev = mentions_event(t, events)
    if not ev:
        return GoalEdit()
    if re.search(r"supprim|enl[eè]v|retir|annul|je ne (la )?fais plus|je fais plus", t):
        return GoalEdit(edits=[EventEdit(target=ev.name, action="remove")], reply=f"D'accord, je retire {ev.name}.")
    when, year = parse_date_fr(t, today, weekdays=False), _year_in(t, today)
    if when or year:
        return GoalEdit(edits=[EventEdit(target=ev.name, action="update", date=when, year=year)],
                        reply=f"Merci, je corrige {ev.name}.")
    return GoalEdit()


_PREF_ROLE = re.compile(r"intensit|fractionn|qualit|longue|bonus|renfo|repos|club|s[ée]ance|sortie", re.I)
_PREF_VERB = re.compile(r"\bgard|\bmet(s|tre)\b|plac|d[ée]plac|plut[ôo]t|pr[ée]f[èée]r|je veux|j'aimerais|je voudrais|"
                        r"l'id[ée]e|toujours|tjrs|\bplus (le|de)\b|pas le\b|jamais le|à la place|au lieu", re.I)


# v0.51.1 : une habitude décrite sans verbe de consigne (« tous les mardis soir j'ai ma séance avec le club ») est
# aussi une préférence permanente : avant, elle partait au coach, qui répondait sans rien enregistrer.
_PREF_HABIT = re.compile(r"\btous les\b|\bchaque\b|\b(lun|mar|mercre|jeu|vendre|same)dis\b|\bdimanches\b|"
                         r"d'habitude|habituellement|en g[ée]n[ée]ral|r[ée]guli[èe]rement|\bclub\b", re.I)


def looks_like_prefs_edit(text: str) -> bool:
    """v0.48 : après l'accueil, un message qui replace les séances dans la semaine (« garder l'intensité le mardi,
    la longue le vendredi et le bonus le week-end ») : préférences mises à jour + plan recalculé."""
    t = text.lower()
    from .week_max import looks_like_week_max
    if looks_like_week_max(t):                     # v0.49 : « je peux monter à 7 h par semaine »
        return True
    return bool(_days_we(t) and _PREF_ROLE.search(t) and (_PREF_VERB.search(t) or _PREF_HABIT.search(t)))


ACCEPT = re.compile(r"^\s*(oui|ok|okay|ça me va|ca me va|ça marche|ca marche|parfait|go|d'accord|dac|c'est parti|"
                    r"c'est bon|c'est ça|c'est ca|valide|je valide|top|super|nickel|banco|exact|let'?s go)\b", re.I)


def parse_turn(text: str, today: date) -> TurnExtract:
    t = text.lower().strip()
    out = _common(t, today)
    out.pop("goal_distance_km", None)
    out.pop("goal_dplus_m", None)
    if re.search(r"repren|dès|des lundi|à partir|a partir|commencer|démarr|demarr|prêt|pret\b", t):
        out["resume_from"] = parse_date_fr(t, today)
    if re.search(r"gros volume|beaucoup de volume|aime (bien )?(le|du) volume|aime bien faire du gros", t):
        out["likes_volume"] = True
    elif re.search(r"plus de volume|plus d'heures|plus de km|augment|davantage", t):
        out["volume_factor"] = 1.15
    elif re.search(r"moins de volume|moins d'heures|moins de km|rédui|redui|baisse|allège|allege", t):
        out["volume_factor"] = 0.85
    from .week_max import looks_like_week_max, parse_week_max
    if looks_like_week_max(t):                     # v0.49 : « je peux monter à 7 h par semaine » = un plafond
        wx = parse_week_max(t, bare_hours=False)
        out.update({k: v for k, v in (("max_week_hours", wx.hours), ("max_week_km", wx.km),
                                      ("max_week_dplus", wx.dplus_m)) if v})
    elif m := re.search(r"(\d+(?:[.,]\d)?)\s*h(?:eures?)?\s*(?:par|/|la)\s*semaine", t):
        out["weekly_hours"] = _num(m[1])
    m = re.search(r"\b(\d)\s*semaines?\s*(?:de|d')\s*(?:vitesse|bloc|travail)", t) or \
        re.search(r"bloc\D{0,10}(\d)\s*semaines?", t)
    if m:
        out["speed_weeks"] = int(m[1])
    if re.search(r"pas dispo|indispo|absent|pas libre|je ne peux pas courir|je peux pas courir", t):
        d = parse_date_fr(t, today)
        if d:
            out["unavailable"] = [d]
    if re.search(r"longues?\D{0,25}en semaine", t) and "long_day" not in out:
        out["long_day"] = 2
    if re.search(r"week-?end", t) and re.search(r"pas (trop|beaucoup)|peu de temps|famille|limit", t):
        out["weekend_max_min"] = 60
    if re.search(r"double[- ]?seuil", t):
        out["double_threshold"] = True
    m = re.search(r"doubles?(?: sorties?| séances?)?([^.!?]*)", t)
    if m:
        jours = [_WD[j] for j in re.findall(r"\b(" + "|".join(JOURS) + r")\b", m[1])]
        if jours:
            out["double_days"] = sorted(set(jours))
    prof = parse_profile(text, today)
    if not prof.skip:
        for k in ("rest_days", "strength", "strength_days", "other_sports", "strava_complete", "hr_source", "busy",
                  "discipline", "bonus", "quality_days", "club_quality", "quality_minutes", "bonus_days",
                  "strength_with_quality", "no_other_sports", "days_per_week", "long_day", "week_start"):
            v = getattr(prof, k)
            if k in ("days_per_week", "long_day") and k in out:
                continue
            if v not in (None, []):
                out[k] = [x.model_dump() if hasattr(x, "model_dump") else x for x in v] if isinstance(v, list) else v
    changes = {k: v for k, v in out.items() if v not in (None, [], "")}
    if changes:
        intent = "modify"
    elif ACCEPT.search(t):
        intent = "accept"
    elif re.match(r"^\s*non\b", t):
        intent = "reject"
    elif t.endswith("?"):
        intent = "question"
    else:
        intent = "other"
    reply = {"modify": "Bien noté, j'ajuste.", "reject": "D'accord. Qu'est-ce que tu voudrais changer ?",
             "question": "Bonne question : en mode hors ligne je ne peux pas y répondre en détail.",
             "other": "Je n'ai pas bien compris ce que tu veux changer."}.get(intent, "")
    return TurnExtract.model_validate({"intent": intent, **changes, "reply": reply})


_PERMANENT = re.compile(r"\btous les\b|\bchaque\b|\btoujours\b|\btjrs\b|\bjamais\b|plus de double|"
                        r"\b(lun|mar|mercre|jeu|vendre|same)dis\b|\bdimanches\b|d[ée]sormais|dor[ée]navant|"
                        r"\bhabitude|en g[ée]n[ée]ral", re.I)


def sanitize_turn(t: TurnExtract, text: str, prefs: Prefs) -> TurnExtract:
    """v0.52 : garde-fous sur l'extraction d'une correction APRÈS l'accueil (préférences permanentes).
    - le jour du club ne bouge que si le message parle du club ;
    - « pas de double seuil jeudi » (cette semaine) ne retire pas le double seuil pour toujours ;
    - « moins / plus de volume » n'est pris que s'il est dit."""
    low = text.lower()
    upd: dict = {}
    if prefs.club_quality and prefs.quality_days and not re.search(r"\bclub\b", low):
        if t.quality_days and t.quality_days != prefs.quality_days:
            upd["quality_days"] = None
        if t.club_quality is False:
            upd["club_quality"] = None
    if t.double_threshold is not None and t.double_threshold != prefs.double_threshold \
            and not _PERMANENT.search(low):
        upd["double_threshold"] = None
    if (t.volume_factor or t.likes_volume) and not re.search(r"volume|d'heures|de km|kilom|charge", low):
        upd["volume_factor"], upd["likes_volume"] = None, None
    return t.model_copy(update=upd) if upd else t


def apply_turn(t: TurnExtract, p: Profile, prefs: Prefs, today: date) -> tuple[Profile, Prefs, list[str]]:
    """Applique une demande de l'athlète, dans les bornes du code. Retourne ce qui a été pris en compte."""
    p, prefs, changed = apply_profile_fields(t, p, prefs, today)
    if t.resume_from and t.resume_from >= today:
        prefs.resume_from = t.resume_from
        changed.insert(0, f"reprise sérieuse le {_fmt_day(t.resume_from)}")
    if t.weekend_max_min:
        prefs.weekend_max_min = max(30, min(120, t.weekend_max_min))
        changed.append(f"week-end léger : {prefs.weekend_max_min} min maximum par jour, pas de séance dure")
    if t.double_threshold is not None:
        prefs.double_threshold = t.double_threshold
        changed.append("double seuil (matin + soir, intensité contrôlée)" if t.double_threshold
                       else "pas de double seuil")
    if t.double_days:
        prefs.double_days = sorted({d for d in t.double_days if 0 <= d <= 6})
        changed.append("doubles possibles le " + ", ".join(JOURS[d] for d in prefs.double_days)
                       + " (footing du matin, en semaine de vitesse)")
    if t.likes_volume:
        prefs.likes_volume = True
        prefs.volume_factor = max(prefs.volume_factor, 1.15)
        changed.append("plus de volume (+15 %, plafonné selon ton historique)")
    if t.weekly_hours:
        prefs.weekly_hours = max(3.0, min(14.0, t.weekly_hours))
        if prefs.max_week_min and prefs.weekly_hours * 60 > prefs.max_week_min:     # v0.49 : jamais au-delà du max
            prefs.weekly_hours = prefs.max_week_min / 60
        changed.append(f"environ {prefs.weekly_hours:g} h par semaine")
    elif t.volume_factor and not t.likes_volume:
        prefs.volume_factor = max(0.6, min(1.3, prefs.volume_factor * t.volume_factor))
        changed.append("plus de volume" if t.volume_factor > 1 else "moins de volume")
    if t.speed_weeks:
        prefs.speed_weeks = max(2, min(6, t.speed_weeks))
        changed.append(f"{prefs.speed_weeks} semaines de vitesse")
    if t.unavailable:
        prefs.unavailable = sorted(set(prefs.unavailable) | {d for d in t.unavailable if d >= today})
        changed.append("jours indisponibles retirés")
    if t.goal_date and t.goal_date > today:
        p.goal_date = t.goal_date
        changed.append(f"objectif le {t.goal_date:%d/%m/%Y}")
    if t.goal_name and len(t.goal_name.strip()) >= 2:
        p.goal_name = t.goal_name.strip()[:120]
    if t.goal_distance_km:
        p.goal_distance_km = t.goal_distance_km
    if t.goal_dplus_m:
        p.goal_dplus_m = t.goal_dplus_m
    return p, prefs, changed


# --------------------------------------------------------------------------- #
# Questions de l'accueil (v0.15)
# --------------------------------------------------------------------------- #
def _fr1(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def profile_questions(d: Diagnosis, layout_known: bool = False) -> str:
    """Les questions posées avant le premier plan, avec ce que Strava montre déjà."""
    q = ["Avant de construire ton plan, quelques questions pour l'adapter à ta vie. Réponds en un seul message, "
         "dans le désordre si tu veux :", ""]
    q.append("🏔️ Tu cours plutôt sur route, en trail, ou les deux ?")
    if not layout_known:
        q.append(f"📅 Combien de jours de course par semaine, et quel jour pour la sortie longue ? (en ce moment tu "
                 f"cours environ {d.typical_days_per_week} jours par semaine)")
    rare = [JOURS[x] for x in d.rare_run_days if x not in (5, 6)] or [JOURS[x] for x in d.rare_run_days]
    q.append("😴 Tes jours de repos : un ou deux jours fixes sans course ?"
             + (f" (je vois que tu ne cours presque jamais le {' ni le '.join(rare[:2])})" if rare else ""))
    hint = (f"~{_fr1(d.strength_per_week)} séance par semaine sur Strava" if d.strength_per_week >= 0.3
            else "aucune séance de renfo sur Strava ces 12 dernières semaines")
    q.append(f"🏋️ Renfo : tu en fais ? À la salle, ou chez toi sans matériel ? ({hint}) C'est la meilleure "
             "protection connue contre les blessures, donc je le prévois dans le plan.")
    if d.other_sports:
        seen = ", ".join(f"{k.lower()} ~{_fr1(v)} h" for k, v in list(d.other_sports.items())[:3])
        q.append(f"🚴 Autres sports : je vois {seen} par semaine. D'autres activités (foot, escalade, natation…), et "
                 "quels jours ?")
    else:
        q.append("🚴 Autres sports : vélo, natation, foot, escalade… lesquels, et quels jours ?")
    hr = (f"FC présente sur {round(d.hr_share * 100)} % de tes sorties récentes" if d.hr_share >= 0.05
          else "je ne vois pas de FC sur tes sorties récentes")
    q.append("⌚ Strava et cardio : tu enregistres toutes tes séances ? Ta fréquence cardiaque vient d'une ceinture "
             f"ou de la montre au poignet ? ({hr})")
    q.append("🏖️ Vacances, déplacements ou semaines chargées dans les deux prochains mois ? Dis-moi si tu pourras "
             "courir un peu, ou pas du tout.")
    q.append("🗓️ Ta semaine commence quel jour ? Lundi par défaut ; dis-moi « dimanche » (ou un autre jour) si tu "
             "comptes tes semaines autrement : vue Semaine, volume maximum et récap de fin de semaine suivront.")
    q += ["", "Tu peux aussi répondre « passe » : je pars sur des valeurs par défaut que tu pourras changer ensuite."]
    return "\n".join(q)


_SPORTS_FR = {"vélo": "vélo", "velo": "vélo", "vtt": "vélo", "home trainer": "vélo", "natation": "natation",
              "nage": "natation", "piscine": "natation", "foot": "foot", "football": "foot", "futsal": "foot",
              "tennis": "tennis", "padel": "padel", "squash": "squash", "badminton": "badminton",
              "escalade": "escalade", "grimpe": "escalade", "crossfit": "crossfit", "hyrox": "hyrox",
              "yoga": "yoga", "pilates": "pilates", "ski": "ski", "basket": "basket", "hand": "handball",
              "handball": "handball", "rugby": "rugby", "boxe": "boxe", "rando": "rando", "randonnée": "rando",
              "volley": "volley", "aviron": "aviron", "danse": "danse"}


def _days_in(text: str) -> list[int]:
    return sorted({_WD[j] for j in re.findall(r"\b(" + "|".join(JOURS) + r")s?\b", text)})


def _ranges(t: str, today: date) -> list[tuple[date, date, str]]:
    """Périodes citées : « du 21/12 au 3/1 », « du 21 au 28 décembre », « semaine du 14/12 », « fin décembre »."""
    out = []
    mois = "|".join(sorted(MOIS, key=len, reverse=True))
    for m in re.finditer(r"du (\d{1,2})(?:\s*/\s*(\d{1,2}))?(?:\s+(" + mois + r"))? au (\d{1,2})\s*(?:/\s*(\d{1,2})|"
                         r"(" + mois + r"))", t):
        m2 = int(m[5]) if m[5] else MOIS[m[6]]
        m1 = int(m[2]) if m[2] else (MOIS[m[3]] if m[3] else m2)
        a = _next_occurrence(int(m[1]), m1, today)
        b = _next_occurrence(int(m[4]), m2, a or today) if a else None
        if a and b:
            out.append((a, b if b >= a else a, t[max(0, m.start() - 60):m.end() + 60]))
    for m in re.finditer(r"semaine du (\d{1,2})\s*(?:/\s*(\d{1,2})|(" + mois + r"))?", t):
        mo = int(m[2]) if m[2] else (MOIS[m[3]] if m[3] else today.month)
        a = _next_occurrence(int(m[1]), mo, today)
        if a:
            a = _monday(a)
            out.append((a, a + timedelta(days=6), t[max(0, m.start() - 60):m.end() + 60]))
    return out


_DAY_RE = r"\b(" + "|".join(JOURS) + r")s?\b|\bweek-?ends?\b"
_INTENSITY = r"fractionn|intensit|interval|\bvma\b|piste|seuil|qualit|\bdure\b|vitesse|\bclub\b"


def _days_we(text: str) -> list[int]:
    """Jours cités, « week-end » = samedi et dimanche."""
    out = set(_days_in(text))
    if re.search(r"\bweek-?ends?\b|\bwe\b", text):
        out |= {5, 6}
    return sorted(out)


def _duration_min(text: str) -> int | None:
    m = re.search(r"\b(\d)\s*h\s*(\d{2})?\b|\b(\d{2,3})\s*(?:min|mn|minutes)\b", text)
    if not m:
        return None
    return int(m[1]) * 60 + int(m[2] or 0) if m[1] else int(m[3])


def christmas_holidays(today: date) -> tuple[date, date]:
    """Vacances scolaires de Noël (France, toutes zones) : du samedi précédant le 21 décembre, deux semaines."""
    y = today.year - 1 if (today.month == 1 and today.day <= 4) else today.year
    d20 = date(y, 12, 20)
    start = d20 - timedelta(days=(d20.weekday() - 5) % 7)
    return start, start + timedelta(days=15)


def parse_week_routine(text: str, today: date) -> dict:
    """v0.48 : sa semaine type écrite librement (« au club le mardi, 1h15, et le vendredi seule sur route ; quand je
    peux une sortie le week-end »). Retourne les champs de ProfileFields reconnus (heuristique hors ligne)."""
    t = text.lower()
    t = re.sub(r"\btjrs\b", "toujours", t)
    out: dict = {}
    clauses = [c for c in re.split(r"[.;\n!?,]|\bet (?=(?:l[ae]|ma|mon|une?|du) )", t) if c and c.strip()]
    fixed: list[int] = []
    optional: list[int] = []
    for k, cl in enumerate(clauses):
        days = _days_we(cl)
        if not days or re.search(r"repos|\boff\b|renfo|muscu|vacances", cl):
            continue
        runny = re.search(r"cour[st]?\b|courir|club|sortie|footing|s[ée]ance|seule?\b|route|trail|longue|bonus|"
                          r"fractionn|intensit", cl)
        if not runny:
            continue
        if re.search(r"quand je peux|si je peux|parfois|de temps en temps|occasionnel|bonus|en plus|quand j'ai le temps",
                     cl):
            optional += days
            continue
        if re.search(_INTENSITY, cl):
            q = list(_days_in(cl))
            m = re.search(r"(?:" + _INTENSITY + r")[^,]{0,40}?\b(" + "|".join(JOURS) + r")s?\b", cl) or \
                re.search(r"\b(" + "|".join(JOURS) + r")s?\b[^,]{0,25}?(?:" + _INTENSITY + r")", cl)
            if m:
                q = [_WD[m[1]]]
            if q:
                out.setdefault("quality_days", [])
                out["quality_days"] = sorted(set(out["quality_days"]) | {q[0]})
                if re.search(r"\bclub\b", cl):
                    out["club_quality"] = True
                after = (cl[cl.find(JOURS[q[0]]):] + " " + (clauses[k + 1] if k + 1 < len(clauses) else ""))[:60]
                mn = _duration_min(after)
                if mn and 30 <= mn <= 150:
                    out["quality_minutes"] = mn
        lm = re.search(r"(?:longue|long)\D{0,12}\b(" + "|".join(JOURS) + r")\b", cl)
        if lm:
            out["long_day"] = _WD[lm[1]]
        bm = re.search(r"bonus\D{0,15}(" + _DAY_RE + r")", cl)
        if bm:
            optional += _days_we(cl[bm.start():])
        fixed += [d for d in days if d not in optional and not (bm and d in _days_we(cl[bm.start():]))]
    fixed = sorted(set(fixed) - set(optional))
    if optional:
        out["bonus_days"] = sorted(set(optional) - set(fixed))
        out["bonus"] = True
    q = out.get("quality_days", [])
    if "long_day" not in out and q and len(fixed) >= 2:
        rest = [d for d in fixed if d not in q]
        if rest:
            out["long_day"] = rest[-1] if len(rest) == 1 else rest[-1]
    said = re.sub(r"(?:minimum|au moins|garder|maintenir)\D{0,12}\d\s*\w+", "", t)
    if len(fixed) >= 2 and optional and not re.search(r"\b[2-7]\s*(?:jours|s[ée]ances|sorties|fois)\b", said):
        out["days_per_week"] = len(fixed)        # ses sorties fixes ; la sortie « quand je peux » devient le bonus
    if re.search(r"pas (le )?m[êe]me jour|jamais le m[êe]me jour|pas (de renfo )?(à c[ôo]t[ée]|en plus) de (la|ma) "
                 r"(s[ée]ance|fractionn)", t):
        out["strength_with_quality"] = False
    if re.search(r"pas d'autres? sports?|aucun autre sport|je ne fais (que|rien d'autre)|pas d'autre activit", t):
        out["no_other_sports"] = True
    if re.search(r"no[ëe]l|f[êe]tes de fin d'ann[ée]e", t) and not _ranges(t, today):
        a, b = christmas_holidays(today)
        ctx = t[re.search(r"no[ëe]l|f[êe]tes de fin", t).start():]
        off = bool(re.search(r"pas (du tout|courir|de course)|aucune sortie|sans courir|impossible", ctx[:120]))
        m = re.search(r"(?:minimum|au moins|garder|maintenir)\D{0,12}(\d)\s*(?:sorties|s[ée]ances|fois|runs)", t)
        out["busy"] = [{"start": a, "end": b, "kind": "off" if off else "light", "label": "vacances de Noël",
                        **({"min_runs": int(m[1])} if m else {})}]
    return out


_WS_DAYS = r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)"


def parse_week_start(text: str) -> int | None:
    """« ma semaine commence le dimanche », « semaine du dimanche au samedi », « je compte du samedi au vendredi »."""
    t = text.lower()
    m = (re.search(r"semaines?\s+(?:commencen?t?|d[ée]marren?t?|d[ée]buten?t?|part(?:ent)?)\s+(?:le\s+|au\s+)?" + _WS_DAYS, t)
         or re.search(r"semaines?\s+(?:va\s+|vont\s+|court\s+)?du\s+" + _WS_DAYS + r"\s+au\s+" + _WS_DAYS, t)
         or re.search(r"(?:commence|d[ée]marre|d[ée]bute)\s+(?:ma|mes|la|les)\s+semaines?\s+(?:le\s+)?" + _WS_DAYS, t)
         or re.search(r"(?:je compte|je raisonne|je fonctionne)\s+du\s+" + _WS_DAYS + r"\s+au\s+" + _WS_DAYS, t))
    return JOURS.index(m[1]) if m else None


def parse_profile(text: str, today: date) -> ProfileExtract:
    """Heuristique hors ligne pour les réponses aux questions de l'accueil."""
    t = text.lower().strip()
    if re.fullmatch(r"\W*(passe|skip|plus tard|on verra|par défaut)\W*", t):
        return ProfileExtract(skip=True, reply="Pas de souci, je pars sur des valeurs par défaut.")
    out: dict = {}
    c = _common(t, today)
    for k in ("days_per_week", "long_day"):
        if k in c:
            out[k] = c[k]
    clauses = re.split(r"[.;\n!?]|,\s*(?=et\b|mais\b)", t)
    for cl in clauses:
        if re.search(r"repos|\boff\b|sans courir|jamais le|je ne cours pas", cl) and not re.search(r"vacances", cl):
            parts = re.split(r"\bet (?=je|j'|du|de la|le (?:" + "|".join(_SPORTS_FR) + r"))|,|\bmais\b", cl)
            days = [d for part in parts if re.search(r"repos|\boff\b|sans courir|jamais le|je ne cours pas", part)
                    for d in _days_in(part)]
            if days:
                out["rest_days"] = sorted(set(out.get("rest_days", [])) | set(days))
        if re.search(r"renfo|muscu|salle|gainage|poids du corps|renforcement", cl):
            if re.search(r"pas de (renfo|muscu)|jamais de (renfo|muscu)|aucun renfo|pas fan|déteste|pas envie", cl):
                out["strength"] = "none"
            elif re.search(r"salle|muscu|haltère|barre|squat|fonte|club", cl):
                out["strength"] = "gym"
            elif re.search(r"maison|sans matériel|poids du corps|chez moi", cl):
                out["strength"] = "bodyweight"
            days = _days_in(cl)
            if days and out.get("strength") != "none":
                out["strength_days"] = days
        hits = sorted((m.start(), m.end(), name) for word, name in _SPORTS_FR.items()
                      for m in re.finditer(r"\b" + re.escape(word) + r"\b", cl)
                      if not re.search(r"pas de " + re.escape(word) + r"\b", cl)
                      and not re.search(r"que (les|en|pendant les) vacances|seulement (en|pendant les) vacances|"
                                        r"en vacances seulement|pas d'autres? sports?", cl))
        for i, (a, b, name) in enumerate(hits):          # jours : juste après la mention, sinon juste avant
            nxt = hits[i + 1][0] if i + 1 < len(hits) else len(cl)
            cut = re.search(r"\bet (?:je|j'|le reste)|\bmais\b|,|\brepos\b", cl[b:nxt])
            nxt = b + cut.start() if cut else nxt
            prev = hits[i - 1][1] if i else 0
            days = _days_in(cl[b:nxt]) or _days_in(cl[prev:a])
            sports = out.setdefault("other_sports", [])
            if not any(x["name"] == name for x in sports):
                sports.append({"name": name, "days": days, "hard": name in HARD_SPORTS})
        if re.search(r"ceinture", cl):
            out["hr_source"] = "ceinture"
        elif re.search(r"poignet|montre", cl) and re.search(r"cardio|fc|fréquence", cl):
            out["hr_source"] = "poignet"
        elif re.search(r"pas de (cardio|ceinture|capteur)|sans cardio", cl):
            out["hr_source"] = "aucune"
        if re.search(r"strava|enregistr", cl):
            if re.search(r"pas tout|pas toujours|oubli|pas (les|mes|le)|sauf|pas systématique", cl):
                out["strava_complete"] = False
            elif re.search(r"tout|toutes|oui|systématique", cl):
                out["strava_complete"] = True
    for a, b, ctx in _ranges(t, today):
        off = bool(re.search(r"pas (du tout|courir|de course|pouvoir)|aucune|sans courir|impossible|ne pourrai", ctx))
        label = ("vacances" if "vacances" in ctx or "congé" in ctx else "déplacement" if "déplacement" in ctx
                 else "semaine chargée")
        out.setdefault("busy", []).append({"start": a, "end": b, "kind": "off" if off else "light", "label": label})
    road, trail = re.search(r"\broute\b|bitume|asphalte", t), re.search(r"\btrail|montagne|sentier", t)
    if re.search(r"les deux|un peu des deux|mixte|route et (en )?trail|trail et (sur )?route", t) or (road and trail):
        out["discipline"] = "mixte"
    elif road and not re.search(r"pas (de|sur) (la )?route", t):
        out["discipline"] = "route"
    elif trail and re.search(r"(plut[oô]t|surtout|que du|uniquement|je fais du|traileur|en) ?trail", t):
        out["discipline"] = "trail"
    if re.search(r"pas de (s[ée]ance )?bonus|sans bonus|pas de s[ée]ances? (en plus|facultatives?)", t):
        out["bonus"] = False
    ws = parse_week_start(t)                               # v0.50 : « ma semaine commence le dimanche »
    if ws is not None:
        out["week_start"] = ws
    life = parse_week_routine(text, today)                 # v0.48 : jours imposés, Noël, « pas d'autres sports »
    for k, v in life.items():
        if k == "busy":
            out["busy"] = out.get("busy", []) + v
        elif k not in out or k in ("long_day", "days_per_week") and k not in c:
            out[k] = v
    if life.get("no_other_sports"):
        out["other_sports"] = [o for o in out.get("other_sports", []) if o["days"]]
    return ProfileExtract.model_validate({**out, "reply": "Merci, c'est noté." if out else ""})


def apply_profile_fields(x: ProfileFields, p: Profile, prefs: Prefs, today: date,
                         defaults: bool = False) -> tuple[Profile, Prefs, list[str]]:
    """Applique les réponses (accueil ou discussion) ; retourne ce qui a été pris en compte, en clair.
    defaults=True : on annonce aussi ce qui est supposé faute de réponse."""
    p, prefs, taken = p.model_copy(deep=True), prefs.model_copy(deep=True), []
    if x.days_per_week:
        p.days_per_week = max(2, min(7, x.days_per_week))
        taken.append(f"{p.days_per_week} jours de course par semaine")
    if x.long_day is not None and 0 <= x.long_day <= 6:
        p.long_day = x.long_day
        taken.append(f"sortie longue le {JOURS[p.long_day]}")
    if x.rest_days:
        rest = sorted({d for d in x.rest_days if 0 <= d <= 6} - {p.long_day})
        prefs.rest_days = rest
        if rest:
            taken.append("repos le " + " et le ".join(JOURS[d] for d in rest) + " : aucune course ce jour-là")
        if p.long_day in x.rest_days:
            taken.append(f"le {JOURS[p.long_day]} reste le jour de ta sortie longue : dis-moi si tu veux la déplacer")
    if x.strength:
        prefs.strength = x.strength
        prefs.strength_per_week = 0 if x.strength == "none" else 2
        taken.append({"gym": "renfo à la salle : 2 séances par semaine (une de force lourde, une de prévention)",
                      "bodyweight": "renfo sans matériel : 2 séances par semaine (force et prévention)",
                      "none": "pas de renfo, c'est ton choix. Je le dis une seule fois : c'est la meilleure protection "
                              "connue contre les blessures (Lauersen 2014) ; 20 minutes par semaine de mollets et "
                              "gainage suffisent pour commencer, si tu changes d'avis."}[x.strength])
    elif defaults and prefs.strength is None:
        taken.append("renfo sans matériel, 2 fois 25 à 35 min par semaine (par défaut : dis-moi si tu as accès à une "
                     "salle)")
    if x.strength_days:
        prefs.strength_days = sorted({d for d in x.strength_days if 0 <= d <= 6})
        taken.append("renfo le " + " et le ".join(JOURS[d] for d in prefs.strength_days))
    if x.other_sports:
        known = {o.name: o for o in prefs.other_sports}
        for o in x.other_sports:
            o.name = o.name.strip().lower()[:30]
            o.hard = o.hard or o.name in HARD_SPORTS
            known[o.name] = o
            when = (" le " + " et le ".join(JOURS[d] for d in o.days)) if o.days else ""
            taken.append(f"{o.name}{when} : " + ("pas de séance dure ce jour-là ni le lendemain" if o.hard and o.days
                                                 else "compté dans ta semaine, sans séance dure par-dessus" if o.days
                                                 else "compté dans ta charge (dis-moi les jours pour que j'évite "
                                                      "les séances dures autour)"))
        prefs.other_sports = list(known.values())
    if x.quality_days:
        q = sorted({d for d in x.quality_days if 0 <= d <= 6} - {p.long_day})
        if q:
            prefs.quality_days = q
            if x.club_quality is not None:
                prefs.club_quality = x.club_quality
            if prefs.strength_with_quality is None and x.strength_with_quality is None:
                prefs.strength_with_quality = False          # son jour d'intensité reste une seule séance
            what = "séance d'intensité au club" if prefs.club_quality else "séance d'intensité"
            taken.append(f"{what} le " + " et le ".join(JOURS[d] for d in q) + " : gardée telle quelle, c'est ta "
                         "séance de qualité de la semaine")
    elif x.club_quality is not None:
        prefs.club_quality = x.club_quality
    if x.quality_minutes:
        prefs.quality_minutes = max(30, min(150, int(x.quality_minutes)))
    if x.bonus_days:
        prefs.bonus_days = sorted({d for d in x.bonus_days if 0 <= d <= 6} - {p.long_day} - set(prefs.quality_days))
        if prefs.bonus_days:
            prefs.bonus = True if x.bonus is None else x.bonus
            taken.append("sortie bonus facultative le " + " ou le ".join(JOURS[d] for d in prefs.bonus_days)
                         + ", seulement quand tu peux")
    if x.strength_with_quality is not None:
        prefs.strength_with_quality = x.strength_with_quality
        if not x.strength_with_quality:
            taken.append("renfo jamais le même jour que la séance d'intensité")
    if x.no_other_sports:
        prefs.other_sports = []
    if x.discipline:
        prefs.discipline = x.discipline
        taken.append({"route": "coureur sur route", "trail": "traileur", "mixte": "route et trail"}[x.discipline])
    if x.bonus is not None:
        prefs.bonus = x.bonus
        if not (x.bonus and x.bonus_days):
            taken.append("une séance bonus facultative par semaine" if x.bonus else "pas de séance bonus")
    if x.strava_complete is not None:
        prefs.strava_complete = x.strava_complete
        taken.append("toutes tes séances sont sur Strava : ma lecture de ta charge est fiable" if x.strava_complete
                     else "tout n'est pas sur Strava : ta vraie charge est un peu plus haute que ce que je vois, je "
                          "reste prudent sur le volume ; valide les séances non enregistrées avec un effort ressenti")
    if x.hr_source:
        prefs.hr_source = x.hr_source
        taken.append({"ceinture": "ceinture cardio : je me fie à ta FC, y compris au seuil",
                      "poignet": "FC au poignet : fiable en endurance, moins en fractionné ; pour la VMA et le seuil on "
                                 "se cale sur l'allure et l'effort ressenti",
                      "aucune": "pas de cardio : on travaille à l'allure et à l'effort ressenti"}[x.hr_source])
    if x.busy:
        keep = [b for b in prefs.busy if b.end >= today]
        for b in x.busy:
            if b.end < today:
                continue
            keep.append(b)
            what = "pas de course" if b.kind == "off" else "semaine allégée" + (
                f", au moins {b.min_runs} sorties" if b.min_runs else "")
            taken.append(f"{b.label or 'période chargée'} du {b.start:%d/%m} au {b.end:%d/%m} : {what}")
        prefs.busy = keep
    if x.week_start is not None and 0 <= x.week_start <= 6:            # v0.50 : premier jour de sa semaine
        prefs.week_start = x.week_start
        taken.append(f"semaine du {JOURS[x.week_start]} au {JOURS[(x.week_start + 6) % 7]} (vue Semaine, volume "
                     "maximum, récap du soir du dernier jour)")
    if x.max_week_hours or x.max_week_km or x.max_week_dplus:          # v0.49 : volume maximum
        from .week_max import WeekMaxExtract, clamp, describe
        mn, km, dp = clamp(WeekMaxExtract(hours=x.max_week_hours, km=x.max_week_km, dplus_m=x.max_week_dplus))
        if prefs.max_week_inferred:                    # une valeur donnée remplace toute l'estimation
            prefs.max_week_min = prefs.max_week_km = prefs.max_week_dplus = None
        prefs.max_week_min = mn or prefs.max_week_min
        prefs.max_week_km = km or prefs.max_week_km
        prefs.max_week_dplus = dp or prefs.max_week_dplus
        prefs.max_week_inferred = False
        if prefs.weekly_hours and prefs.max_week_min and prefs.weekly_hours * 60 > prefs.max_week_min:
            prefs.weekly_hours = prefs.max_week_min / 60
        taken.append("volume maximum : " + describe(prefs.max_week_min, prefs.max_week_km, prefs.max_week_dplus)
                     + ", jamais au-delà")
    return p, prefs, taken


def commitments(p: Profile, prefs: Prefs, today: date | None = None) -> list[str]:
    """Ce que l'athlète a dit et que le plan respecte, dans l'état final (v0.17) : affiché dans l'app, donné au
    coach à chaque message, enregistré avec le plan validé."""
    prefs = merge_club_sport(prefs)                 # v0.52 : pas de « club de trail » en double
    today = today or date.today()
    out = [f"Objectif : {p.goal_name} le {p.goal_date:%d/%m/%Y}"
           + (f" ({p.goal_distance_km:g} km" + (f", {p.goal_dplus_m} m D+" if p.goal_dplus_m else "") + ")"
              if p.goal_distance_km else "")]
    for e in p.events:
        when = f" le {e.date:%d/%m/%Y}" if e.date else ""
        role = {"B": "course secondaire", "C": "plus tard"}.get(e.role, "")
        out.append(f"{e.name}{when} : {role}" + (f" ({e.note})" if e.note else ""))
    out.append(f"{p.days_per_week} jours de course par semaine, sortie longue le {JOURS[p.long_day]}")
    if prefs.quality_days:
        out.append(("Séance d'intensité au club le " if prefs.club_quality else "Séance d'intensité le ")
                   + " et le ".join(JOURS[d] for d in prefs.quality_days)
                   + (f" ({_h(prefs.quality_minutes)})" if prefs.quality_minutes else "") + " : à garder")
    if prefs.bonus_days and prefs.bonus:
        out.append("Sortie bonus facultative le " + " ou le ".join(JOURS[d] for d in prefs.bonus_days))
    if prefs.strength_with_quality is False:
        out.append("Renfo jamais le jour de la séance d'intensité")
    if prefs.rest_days:
        out.append("Repos le " + " et le ".join(JOURS[d] for d in prefs.rest_days))
    if prefs.resume_from and prefs.resume_from >= today - timedelta(days=60):
        out.append(f"Reprise sérieuse souhaitée le {_fmt_day(prefs.resume_from)}")
    if prefs.weekend_max_min:
        out.append(f"Week-end léger (famille) : {prefs.weekend_max_min} min maximum par jour, pas de séance dure")
    if prefs.double_threshold:
        out.append("Aime le double seuil (matin + soir, contrôlé)")
    if prefs.double_days:
        out.append("Doubles possibles le " + ", ".join(JOURS[d] for d in prefs.double_days))
    if prefs.max_week_min or prefs.max_week_km or prefs.max_week_dplus:
        from .week_max import describe
        out.append("Volume maximum : " + describe(prefs.max_week_min, prefs.max_week_km, prefs.max_week_dplus)
                   + (" (estimé d'après son historique)" if prefs.max_week_inferred else ""))
    if prefs.week_start:
        out.append(f"Semaine du {JOURS[prefs.week_start]} au {JOURS[(prefs.week_start + 6) % 7]}")
    if prefs.likes_volume:
        out.append("Aime le gros volume")
    elif prefs.weekly_hours:
        out.append(f"Environ {prefs.weekly_hours:g} h par semaine")
    elif abs(prefs.volume_factor - 1) > 0.04:
        out.append("Préfère " + ("plus" if prefs.volume_factor > 1 else "moins") + " de volume")
    out.append({"gym": "Renfo à la salle, 2 fois par semaine", "bodyweight": "Renfo sans matériel, 2 fois par semaine",
                "none": "Pas de renfo (son choix)", None: "Renfo sans matériel (par défaut)"}[prefs.strength]
               + (" ; créneaux : " + ", ".join(JOURS[d] for d in prefs.strength_days) if prefs.strength_days else ""))
    for o in prefs.other_sports:
        out.append(f"Autre sport : {o.name}" + (" le " + ", ".join(JOURS[d] for d in o.days) if o.days else "")
                   + (" (intense)" if o.hard else ""))
    if prefs.strava_complete is not None:
        out.append("Toutes ses séances sont sur Strava" if prefs.strava_complete
                   else "Tout n'est pas sur Strava : charge réelle un peu plus haute")
    if prefs.hr_source:
        out.append({"ceinture": "FC : ceinture cardio", "poignet": "FC : montre au poignet",
                    "aucune": "Pas de cardio"}[prefs.hr_source])
    for b in prefs.busy:
        if b.end >= today:
            out.append(f"{(b.label or 'Période chargée').capitalize()} du {b.start:%d/%m} au {b.end:%d/%m} : "
                       + ("pas de course" if b.kind == "off" else "semaine allégée"
                          + (f", au moins {b.min_runs} sorties" if b.min_runs else "")))
    if prefs.unavailable:
        days = [d for d in prefs.unavailable if d >= today]
        if days:
            out.append("Indisponible le " + ", ".join(f"{d:%d/%m}" for d in days))
    return out


class OnboardingMessage(BaseModel):
    message: str = Field(min_length=20, max_length=6000)


def month_label(d: date) -> str:
    return f"{MOIS_NOMS[d.month - 1]} {d.year}"
