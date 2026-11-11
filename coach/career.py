"""Carrière du coureur (v0.24) : expérience (XP), niveaux, trophées et avatar.

Principe : chaque trophée rapporte de l'XP, une seule fois, à la date où il a été gagné ; l'historique Strava
est relu au premier lancement, la carrière est donc reconstruite d'un coup (« tu as déjà 47 trophées »).
Les séances du plan validées rapportent un peu d'XP à chaque fois (régularité).

Niveaux (v0.25) : passer du niveau 1 au 2 demande 50 XP, du 2 au 3 100 XP, puis 200, 400… (le double à chaque
niveau), sans niveau maximum. Au niveau n, total cumulé = 50 × (2^(n-1) − 1).

Garde-fous (pour que le jeu ne pousse pas à la blessure) :
- aucun XP pour avoir fait plus que le plan, aucune série de jours d'affilée (on compte des semaines) ;
- les vitesses ne comptent que sur une sortie entière à plat, de la bonne distance, à une vitesse plausible ;
- une famille « intelligence » récompense la régularité sans pic de charge et le retour de séance au coach.
Tout est calculé, sans LLM.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from .diagnosis import FLAT_DPLUS_PER_KM, dplus_per_km, is_run, run_time

# v0.34 : chaque niveau coûte un peu plus que le précédent (380 XP, puis +58 XP par niveau) : niveau 7 à 3 150 XP
# (inchangé), niveau 10 à ~5 500 XP, niveau 20 à ~17 000 XP. L'ancien doublement rendait le niveau 20 inatteignable.
LEVEL_A, LEVEL_B = 380, 58
MAX_ICON_LEVEL = 20                               # au-delà : une étoile de plus par niveau

RANKS = ["Débutant", "Joggeur", "Coureur", "Coureur confirmé", "Traileur", "Traileur aguerri", "Ultra-traileur",
         "Ultra-traileur expert", "Finisher en série", "Coureur des cimes", "Arpenteur", "Grimpeur", "Montagnard",
         "Aventurier", "Élite amateur", "Vétéran des sentiers", "Maître des sentiers", "Seigneur des crêtes", "Mythe",
         "Légende"]

FAMILIES = {"jalons": "Jalons", "vitesse": "Vitesse", "endurance": "Endurance et dénivelé", "force": "Force",
            "regularite": "Régularité", "intelligence": "Intelligence", "evenements": "Événements"}

# équipement de l'avatar, débloqué par niveau (le client dessine chaque pièce) ; v0.34 : étalé jusqu'au niveau 20
GEAR = [(2, "bandeau", "Bandeau"), (3, "montre", "Montre GPS"), (4, "casquette", "Casquette de trail"),
        (5, "gilet", "Gilet de trail et flasques"), (6, "batons", "Bâtons"), (7, "dossard", "Dossard"),
        (8, "medaille", "Médaille de finisher"), (10, "frontale", "Lampe frontale"),
        (12, "laurier", "Couronne de laurier"), (14, "chaussures_or", "Chaussures dorées"), (16, "aura", "Aura dorée"),
        (20, "camelbak", "Sac d'ultra à poche à eau")]
FACE_LEVEL = 10                                   # v0.34 : photo du visage pour un avatar IA ressemblant
# v0.30 : accessoires gagnés avec un trophée (pas seulement avec les niveaux)
TROPHY_GEAR = [("dist_21.1", "lunettes", "Lunettes de soleil"), ("dplus_1000", "buff", "Tour de cou"),
               ("dist_42.2", "manchons", "Manchons de compression"), ("duree_6", "coupe_vent", "Veste coupe-vent"),
               ("leve_tot", "gants", "Gants fins"), ("dist_50", "guetres", "Guêtres de trail"),
               ("renfo_50", "poignets", "Poignets éponge"),
               # v0.33 : accessoires de saison (portés d'office seulement pendant leur période)
               ("halloween", "chapeau_sorciere", "Chapeau de sorcière"), ("noel", "bonnet_noel", "Bonnet de Noël"),
               ("anniversaire", "chapeau_fete", "Chapeau d'anniversaire"), ("nouvel_an", "cotillons", "Cotillons")]
HATS = ("chapeau_sorciere", "bonnet_noel", "chapeau_fete")       # un seul chapeau, il remplace casquette et bandeau


def easter(y: int) -> date:
    """Dimanche de Pâques (calendrier grégorien, algorithme anonyme de Meeus-Jones-Butcher)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    n = h + l_ - 7 * m + 114
    return date(y, n // 31, n % 31 + 1)


def birthday_of(mmdd: str | None, year: int) -> date | None:
    if not mmdd:
        return None
    mo, dd = (int(x) for x in mmdd.split("-"))
    if (mo, dd) == (2, 29):
        try:
            return date(year, 2, 29)
        except ValueError:
            return date(year, 2, 28)
    return date(year, mo, dd)


def in_season(gear_id: str, today: date, birthday: str | None = None) -> bool:
    """Période où l'accessoire de saison est porté d'office (mode automatique)."""
    md = (today.month, today.day)
    if gear_id == "chapeau_sorciere":
        return (10, 17) <= md <= (11, 2)
    if gear_id == "bonnet_noel":
        return md >= (12, 1) or md <= (1, 6)
    if gear_id == "cotillons":
        return md >= (12, 28) or md <= (1, 7)
    if gear_id == "chapeau_fete":
        b = birthday_of(birthday, today.year)
        return b is not None and abs((today - b).days) <= 3
    return True


SEASONAL = {"chapeau_sorciere", "bonnet_noel", "chapeau_fete", "cotillons"}
# au-delà : une étoile de plus à chaque niveau (pas de niveau maximum)
# v0.30 : décors au choix, débloqués par niveau ou par trophée
SCENE_LABELS = {"route": "Route", "foret": "Forêt", "collines": "Collines", "montagne": "Montagne",
                "nuit": "Nuit étoilée", "sommet": "Sommet au lever du soleil", "ville": "Ville",
                "bord_de_mer": "Bord de mer", "neige": "Neige", "volcan": "Volcan",
                "printemps": "Printemps", "ete": "Été", "automne": "Automne", "hiver": "Hiver"}
# v0.34 : un décor de plus presque à chaque niveau, dont les 4 saisons
LEVEL_SCENES = [(1, "route"), (2, "collines"), (3, "foret"), (4, "printemps"), (5, "bord_de_mer"), (6, "montagne"),
                (7, "automne"), (8, "ete"), (9, "hiver"), (11, "nuit"), (13, "sommet")]
TROPHY_SCENES = [("dist_10", "ville"), ("dplus_2500", "neige"), ("dist_100", "volcan")]
SEASON_SCENE = {12: "hiver", 1: "hiver", 2: "hiver", 3: "printemps", 4: "printemps", 5: "printemps", 6: "ete", 7: "ete",
                8: "ete", 9: "automne", 10: "automne", 11: "automne"}
# v0.47 : deux choix qui se croisent — le lieu (le paysage) et l'ambiance (saison, neige, nuit) : montagne + neige,
# bord de mer + nuit… Chaque décor garde sa règle de déblocage (niveau ou trophée) ; « aucune » ambiance est toujours là.
PLACES = ("route", "collines", "foret", "bord_de_mer", "montagne", "sommet", "ville", "volcan")
AMBIANCES = ("printemps", "ete", "automne", "hiver", "neige", "nuit")
NO_AMBIANCE = "aucune"
PLACE_LABELS = {**{i: SCENE_LABELS[i] for i in PLACES}, "sommet": "Sommet"}
AMBIANCE_LABELS = {NO_AMBIANCE: "Aucune", **{i: SCENE_LABELS[i] for i in AMBIANCES}, "nuit": "Nuit"}


def scene_unlocks(level: int, got: set | None = None, names: dict | None = None) -> list[dict]:
    """Tous les décors (lieux et ambiances) avec leur règle de déblocage ; `got` : trophées obtenus."""
    names = names or {}
    out = [{"id": i, "label": SCENE_LABELS[i], "unlocked": level >= lv, "how": f"niv. {lv}"} for lv, i in LEVEL_SCENES]
    out += [{"id": i, "label": SCENE_LABELS[i], "unlocked": tid in (got or set()),
             "how": f"trophée « {names.get(tid, tid)} »"} for tid, i in TROPHY_SCENES]
    return out


def auto_place(level: int) -> str:
    """Lieu automatique : le dernier lieu débloqué par niveau (route au niveau 1, montagne au 6, sommet au 13)."""
    by_level = [i for lv, i in LEVEL_SCENES if level >= lv and i in PLACES]
    return by_level[-1] if by_level else "route"


def auto_ambiance(level: int, today: date, unlocked: set | None = None) -> str:
    """Ambiance automatique : la saison du moment si elle est débloquée, sinon aucune."""
    season = SEASON_SCENE[today.month]
    ok = unlocked if unlocked is not None else {i for lv, i in LEVEL_SCENES if level >= lv}
    return season if season in ok else NO_AMBIANCE


def split_scene(avatar: dict) -> tuple[str | None, str | None]:
    """(lieu, ambiance) choisis, None = automatique. Migration : un ancien `scene` (avant v0.47) qui est une ambiance
    (« hiver ») donne lieu auto + cette ambiance ; un ancien lieu (« montagne ») donne ce lieu + ambiance auto."""
    if "place" in avatar or "ambiance" in avatar:
        place, amb = avatar.get("place"), avatar.get("ambiance")
        derived = place or (amb if amb != NO_AMBIANCE else None)
        if "scene" not in avatar or avatar["scene"] == derived:
            return place, amb
        # un ancien client a changé `scene` sans connaître lieu et ambiance : c'est `scene` qui compte
    old = avatar.get("scene")
    if old in PLACES:
        return old, None
    if old in AMBIANCES:
        return None, old
    return None, None


def resolve_scene(avatar: dict, level: int, today: date, unlocked: set | None = None) -> dict:
    """Lieu et ambiance réellement affichés (les choix « auto » remplacés), et le décor unique pour les anciens clients."""
    place, amb = split_scene(avatar)
    p = place or auto_place(level)
    a = amb or auto_ambiance(level, today, unlocked)
    if place:
        legacy = place
    elif amb and amb != NO_AMBIANCE:
        legacy = amb
    elif amb is None and a != NO_AMBIANCE:
        legacy = a                       # comme avant : la saison du moment
    else:
        legacy = p
    return {"place": p, "ambiance": a, "scene": legacy}

SESSION_XP = {"done": 3, "modified": 2}
KEY_INTENSITIES = {"threshold", "vo2", "long", "hills", "tempo", "race"}
KEY_SESSION_XP = 6
BONUS_XP = 4                                     # v0.25 : séance bonus facultative faite (sautée : rien ne se perd)


def need_for(level: int) -> int:
    """XP pour passer du niveau `level` au suivant."""
    return LEVEL_A + LEVEL_B * (level - 1)


def level_for(xp: int) -> dict:
    lvl, start = 1, 0
    while xp >= start + need_for(lvl):
        start += need_for(lvl)
        lvl += 1
    need = need_for(lvl)
    rank = RANKS[min(lvl, len(RANKS)) - 1] + (f" ★{lvl - len(RANKS)}" if lvl > len(RANKS) else "")
    return {"level": lvl, "rank": rank, "xp": xp, "level_start": start, "next_at": start + need,
            "into": xp - start, "need": need, "pct": round(100 * (xp - start) / need)}


def total_for_level(n: int) -> int:
    return LEVEL_A * (n - 1) + LEVEL_B * (n - 1) * (n - 2) // 2


@dataclass
class Trophy:
    id: str
    family: str
    label: str
    desc: str
    xp: int
    tier: int                                   # 1 bronze, 2 argent, 3 or, 4 légende
    check: Callable[[Facts], date | None]
    progress: Callable[[Facts], str | None] | None = None


# --------------------------------------------------------------------------- faits tirés de l'historique
class Facts:
    def __init__(self, acts, feedback: list[dict] | None = None, plan=None, meta: dict | None = None,
                 strength: list[dict] | None = None, today: date | None = None, races: list | None = None,
                 tz: str = "Europe/Paris", birthday: str | None = None):
        self.today = today or date.today()
        self.birthday = birthday                  # v0.33 : « MM-JJ », sans l'année
        self.acts = sorted(acts, key=lambda a: (a.local_day, a.start_utc))
        self.runs = [a for a in self.acts if is_run(a)]
        self.renfo = [a for a in self.acts if a.sport == "Renfo"]
        self.feedback = feedback or []
        self.plan = {s.session_id: s for s in (plan or [])}
        self.meta = meta or {}
        self.strength = sorted(strength or [], key=lambda x: x["day"])
        self.races = races or []                  # [(nom, date, distance_km)] : objectif et courses B
        self.others = [a for a in self.acts if not is_run(a) and a.sport != "Renfo"
                       and not getattr(a, "suspect", False)]          # v0.48 : sortie douteuse : nulle part
        try:
            from zoneinfo import ZoneInfo
            z = ZoneInfo(tz)
            self.local_hour = lambda a: a.start_utc.astimezone(z).hour
        except Exception:                         # noqa: BLE001
            self.local_hour = lambda a: a.start_utc.hour

    # --- v0.25 : semaines
    def _weeks(self, acts) -> dict:
        by: dict[date, list] = {}
        for a in acts:
            by.setdefault(a.local_day - timedelta(days=a.local_day.weekday()), []).append(a)
        return by

    def active_streak_first(self, weeks: int) -> date | None:
        """Série « à la Strava » : semaines consécutives avec au moins une activité (tous sports)."""
        return self.weekly_streak_first(lambda w: len(w) >= 1, weeks, self.acts)

    def current_active_streak(self) -> int:
        by, n = self._weeks(self.acts), 0
        w = self.today - timedelta(days=self.today.weekday())
        if not by.get(w):
            w -= timedelta(days=7)                 # semaine en cours pas encore commencée : on part de la précédente
        while by.get(w):
            n += 1
            w -= timedelta(days=7)
        return n

    def weeks_with_runs_first(self, n_weeks: int, runs_per_week: int = 3) -> date | None:
        by = self._weeks(self.runs)
        k = 0
        for w in sorted(by):
            if len(by[w]) >= runs_per_week:
                k += 1
                if k >= n_weeks:
                    return min(w + timedelta(days=6), self.today)
        return None

    def weeks_with_runs(self, runs_per_week: int = 3) -> int:
        return sum(1 for v in self._weeks(self.runs).values() if len(v) >= runs_per_week)

    def all_months_first(self) -> date | None:
        """Première année civile avec au moins une sortie chaque mois."""
        months: dict[int, set] = {}
        for a in self.runs:
            months.setdefault(a.local_day.year, set()).add(a.local_day.month)
            if len(months[a.local_day.year]) == 12:
                return a.local_day
        return None

    def nth_first(self, acts, n: int) -> date | None:
        return acts[n - 1].local_day if len(acts) >= n else None

    def race_done_first(self) -> date | None:
        for _name, d, km in self.races:
            if not d or d > self.today:
                continue
            for a in self.runs:
                if a.local_day == d and (not km or a.distance_m >= 0.8 * km * 1000):
                    return d
        return None

    def full_block_first(self, weeks: int = 4) -> date | None:
        """Semaines de plan consécutives (passées) avec au moins 90 % des séances validées faites."""
        done = {f["session_id"] for f in self.feedback if f.get("status") in ("done", "modified")}
        by: dict[date, list] = {}
        for sid, s in self.plan.items():
            if s.is_race or sid.endswith("-bonus") or s.day >= self.today - timedelta(days=self.today.weekday()):
                continue
            by.setdefault(s.day - timedelta(days=s.day.weekday()), []).append(sid)
        streak, prev = 0, None
        for w in sorted(by):
            ok = sum(1 for x in by[w] if x in done) >= 0.9 * len(by[w])
            streak = streak + 1 if ok and (prev is None or w - prev == timedelta(days=7)) else (1 if ok else 0)
            prev = w
            if streak >= weeks:
                return w + timedelta(days=6)
        return None

    # premier jour où une sortie remplit la condition
    # --- v0.33 : événements du calendrier
    def runs_on(self, days_of_year) -> list[date]:
        """Jours (une fois par jour) où une course tombe sur un jour d'événement ; days_of_year(année) -> dates."""
        out, seen = [], set()
        for a in self.runs:
            d = a.local_day
            if d not in seen and d in days_of_year(d.year):
                seen.add(d)
                out.append(d)
        return out

    def window_count_first(self, start_md, end_md, n) -> date | None:
        """Première année où au moins n jours avec une course tombent entre start_md et end_md (inclus)."""
        by: dict[int, list[date]] = {}
        for a in self.runs:
            d = a.local_day
            if start_md <= (d.month, d.day) <= end_md and d not in by.setdefault(d.year, []):
                by[d.year].append(d)
        hits = [days[n - 1] for _, days in sorted(by.items()) if len(days) >= n]
        return hits[0] if hits else None

    def first_run(self, cond) -> date | None:
        return next((a.local_day for a in self.runs if cond(a)), None)

    def best_time(self, dist_m: float) -> float | None:
        c = [run_time(a) * dist_m / a.distance_m for a in self.runs if _flat_effort(a, dist_m)]
        return min(c) if c else None

    def first_under(self, dist_m: float, limit_s: float) -> date | None:
        return self.first_run(lambda a: _flat_effort(a, dist_m) and run_time(a) * dist_m / a.distance_m <= limit_s)

    def cumul_first(self, value, threshold) -> date | None:
        tot = 0.0
        for a in self.runs:
            tot += value(a)
            if tot >= threshold:
                return a.local_day
        return None

    def window_first(self, value, threshold, kind="month", acts=None) -> date | None:
        """Premier mois (ou année) civil dont le cumul atteint le seuil : date de la sortie qui le franchit."""
        tot, key = 0.0, None
        for a in (acts if acts is not None else self.runs):
            k = (a.local_day.year, a.local_day.month) if kind == "month" else a.local_day.year
            if k != key:
                key, tot = k, 0.0
            tot += value(a)
            if tot >= threshold:
                return a.local_day
        return None

    def weekly_streak_first(self, ok, weeks: int, acts=None) -> date | None:
        """Première fin de série de `weeks` semaines consécutives où ok(sorties de la semaine) est vrai."""
        src = acts if acts is not None else self.runs
        if not src:
            return None
        by: dict[date, list] = {}
        for a in src:
            by.setdefault(a.local_day - timedelta(days=a.local_day.weekday()), []).append(a)
        start = min(by)
        streak, w = 0, start
        last = self.today - timedelta(days=self.today.weekday())
        while w <= last:
            if ok(by.get(w, [])):
                streak += 1
                if streak >= weeks:
                    return w + timedelta(days=6) if w + timedelta(days=6) <= self.today else self.today
            else:
                streak = 0
            w += timedelta(days=7)
        return None

    def no_spike_weeks_first(self, weeks: int) -> date | None:
        """Semaines consécutives entraînées (≥ 2 sorties) sans pic de charge : charge de la semaine
        ≤ 1,5 × la moyenne des 4 semaines précédentes."""
        loads: dict[date, float] = {}
        for a in self.acts:
            w = a.local_day - timedelta(days=a.local_day.weekday())
            loads[w] = loads.get(w, 0.0) + (a.load or 0)
        if not loads:
            return None
        counts: dict[date, int] = {}
        for a in self.runs:
            w = a.local_day - timedelta(days=a.local_day.weekday())
            counts[w] = counts.get(w, 0) + 1
        w, streak, last = min(loads), 0, self.today - timedelta(days=self.today.weekday() + 7)
        while w <= last:
            prev = [loads.get(w - timedelta(days=7 * k), 0.0) for k in range(1, 5)]
            chronic = sum(prev) / 4
            good = counts.get(w, 0) >= 2 and chronic > 0 and loads.get(w, 0.0) <= 1.5 * chronic
            streak = streak + 1 if good else 0
            if streak >= weeks:
                return w + timedelta(days=6)
            w += timedelta(days=7)
        return None

    def strength_ratio_first(self, ratio: float) -> date | None:
        for s in self.strength:
            lift = s.get("bodyweight") and s.get("kg") and s["exercise"] in ("squat", "box_squat", "trap_bar", "deadlift")
            if lift and one_rm(s["kg"], s.get("reps", 1)) >= ratio * s["bodyweight"]:
                return date.fromisoformat(s["day"])
        return None

    def best_ratio(self) -> float | None:
        r = [one_rm(s["kg"], s.get("reps", 1)) / s["bodyweight"] for s in self.strength
             if s.get("bodyweight") and s.get("kg") and s["exercise"] in ("squat", "box_squat", "trap_bar", "deadlift")]
        return max(r) if r else None

    def feedback_count_first(self, n: int) -> date | None:
        fb = sorted(f["day"] for f in self.feedback if f.get("rpe") or (f.get("note") or "").strip())
        return date.fromisoformat(fb[n - 1]) if len(fb) >= n else None


def one_rm(kg: float, reps: int) -> float:
    """Epley : estimation du maximum sur une répétition (fiable jusqu'à ~10 répétitions)."""
    reps = max(1, min(int(reps or 1), 12))
    return kg if reps == 1 else kg * (1 + reps / 30)


def _flat_effort(a, dist_m: float) -> bool:
    """Sortie entière proche de la distance (−2 % / +15 %), à plat, à une vitesse humaine plausible."""
    if not (0.98 * dist_m <= a.distance_m <= 1.15 * dist_m) or dplus_per_km(a) >= FLAT_DPLUS_PER_KM:
        return False
    v = a.distance_m / max(run_time(a), 1) * 3.6
    return 6 <= v <= 24


def _hms(s: float) -> str:
    s = round(s)
    return f"{s // 3600} h {s % 3600 // 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# --------------------------------------------------------------------------- catalogue
def _catalog() -> list[Trophy]:
    T: list[Trophy] = []
    km = lambda a: a.distance_m / 1000

    # Jalons : la distance d'une sortie
    for d, label, xp, tier in [(5, "Premier 5 km", 10, 1), (10, "Premier 10 km", 15, 1), (21.1, "Premier semi", 25, 1),
                               (30, "Premiers 30 km", 30, 2), (42.2, "Premier marathon (ou plus)", 50, 2),
                               (50, "Premier 50 km", 75, 2), (80, "Premier 80 km", 120, 3),
                               (100, "Premier 100 km", 200, 3), (160, "Premières 100 miles", 400, 4)]:
        T.append(Trophy(f"dist_{d:g}", "jalons", label, f"Une sortie d'au moins {d:g} km.".replace(".", ",", 1)
                        if d % 1 else f"Une sortie d'au moins {d:g} km.", xp, tier,
                        lambda f, d=d: f.first_run(lambda a: a.distance_m >= 0.97 * d * 1000),
                        lambda f, d=d: f"record : {max((km(a) for a in f.runs), default=0):.1f} km".replace(".", ",")))
    T.append(Trophy("objectif", "jalons", "Objectif atteint", "Finir ta course objectif (ou une course B) du plan.",
                    300, 4, lambda f: f.race_done_first()))
    # Endurance et dénivelé
    for m, xp, tier in [(1000, 20, 1), (2500, 50, 2), (5000, 100, 3), (9000, 250, 4)]:
        T.append(Trophy(f"dplus_{m}", "endurance", f"{m:,} m de D+ d'un coup".replace(",", " "),
                        f"Une sortie avec au moins {m} m de dénivelé positif.", xp, tier,
                        lambda f, m=m: f.first_run(lambda a: (a.dplus_m or 0) >= m),
                        lambda f: f"record : {max((a.dplus_m or 0 for a in f.runs), default=0):.0f} m"))
    for hrs, xp, tier in [(3, 20, 1), (6, 50, 2), (12, 120, 3), (24, 300, 4)]:
        T.append(Trophy(f"duree_{hrs}", "endurance", f"{hrs} heures en mouvement", f"Une sortie de {hrs} h ou plus.",
                        xp, tier, lambda f, h=hrs: f.first_run(lambda a: a.duration_s >= h * 3600)))
    for tot, xp, tier in [(1000, 30, 1), (5000, 100, 2), (10_000, 250, 3), (20_000, 500, 4)]:
        T.append(Trophy(f"cumul_{tot}", "endurance", f"{tot:,} km au compteur".replace(",", " "),
                        f"{tot} km de course cumulés.", xp, tier, lambda f, t=tot: f.cumul_first(km, t),
                        lambda f: f"{sum(km(a) for a in f.runs):,.0f} km".replace(",", " ")))
    T.append(Trophy("km_vertical", "endurance", "Kilomètre vertical", "900 m de D+ ou plus en 6 km ou moins.", 60, 2,
                    lambda f: f.first_run(lambda a: (a.dplus_m or 0) >= 900 and a.distance_m <= 6_000)))
    for m, xp, tier in [(50_000, 60, 2), (100_000, 120, 3), (250_000, 300, 4)]:
        T.append(Trophy(f"cumul_dplus_{m}", "endurance", f"{m:,} m de D+ cumulés".replace(",", " "),
                        f"{m} m de dénivelé positif en course depuis le début de l'historique.", xp, tier,
                        lambda f, m=m: f.cumul_first(lambda a: a.dplus_m or 0, m),
                        lambda f: f"{sum(a.dplus_m or 0 for a in f.runs):,.0f} m".replace(",", " ")))
    T.append(Trophy("nuit", "endurance", "Coureur de nuit", "Une sortie commencée après 20 h ou avant 5 h.", 15, 1,
                    lambda f: f.first_run(lambda a: f.local_hour(a) >= 20 or f.local_hour(a) < 5)))
    T.append(Trophy("explorateur", "endurance", "Explorateur", "20 séances d'autres sports (vélo, natation, rando, ski…).",
                    20, 1, lambda f: f.nth_first(f.others, 20), lambda f: f"{len(f.others)} séances"))
    T.append(Trophy("mois_dplus_10000", "endurance", "Mois de montagne", "10 000 m de D+ dans le même mois.", 80, 3,
                    lambda f: f.window_first(lambda a: a.dplus_m or 0, 10_000)))
    for tot, xp, tier in [(2000, 60, 2), (3000, 100, 3)]:
        T.append(Trophy(f"annee_{tot}", "endurance", f"Année à {tot} km", f"{tot} km de course dans l'année civile.",
                        xp, tier, lambda f, t=tot: f.window_first(km, t, "year")))

    # Vitesse : sortie entière à plat
    speed = {5_000: [(25 * 60, 10, 1), (22 * 60, 20, 1), (20 * 60, 40, 2), (18 * 60, 80, 3), (17 * 60, 150, 4)],
             10_000: [(50 * 60, 10, 1), (45 * 60, 20, 1), (40 * 60, 40, 2), (37 * 60, 80, 3), (35 * 60, 150, 4)],
             21_097: [(105 * 60, 20, 1), (90 * 60, 40, 2), (85 * 60, 80, 3), (80 * 60, 150, 4)],
             42_195: [(240 * 60, 30, 1), (210 * 60, 60, 2), (180 * 60, 120, 3), (165 * 60, 250, 4)]}
    names = {5_000: "5 km", 10_000: "10 km", 21_097: "semi", 42_195: "marathon"}
    for dist, tiers in speed.items():
        for lim, xp, tier in tiers:
            T.append(Trophy(f"vit_{dist}_{lim}", "vitesse", f"{names[dist].capitalize()} en moins de {_hms(lim)}",
                            "Sur une sortie à plat de la distance (GPS), temps ramené à la distance exacte.", xp, tier,
                            lambda f, d=dist, l=lim: f.first_under(d, l),
                            lambda f, d=dist: (f"meilleur : {_hms(f.best_time(d))}" if f.best_time(d) else None)))

    # Force : séances enregistrées et charges déclarées
    for n, xp, tier in [(10, 10, 1), (50, 30, 2), (100, 60, 3), (250, 120, 4)]:
        T.append(Trophy(f"renfo_{n}", "force", f"{n} séances de renfo", f"{n} séances « Renfo » enregistrées.", xp, tier,
                        lambda f, n=n: f.renfo[n - 1].local_day if len(f.renfo) >= n else None,
                        lambda f: f"{len(f.renfo)} séances"))
    T.append(Trophy("renfo_8sem", "force", "Renfo régulier", "8 semaines de suite avec au moins une séance de renfo.",
                    40, 2, lambda f: f.weekly_streak_first(lambda w: len(w) >= 1, 8, f.renfo)))
    for r, xp, tier in [(1.0, 10, 1), (1.5, 25, 1), (2.0, 60, 2), (2.25, 120, 3), (2.5, 200, 4)]:
        T.append(Trophy(f"squat_{r:g}", "force", f"Squat à {r:g} fois ton poids".replace(".", ","),
                        "Maximum estimé (squat, box squat, trap bar ou soulevé de terre) déclaré dans Carrière.",
                        xp, tier, lambda f, r=r: f.strength_ratio_first(r),
                        lambda f: (f"meilleur : {f.best_ratio():.2f} × ton poids".replace(".", ",")
                                   if f.best_ratio() else "déclare une charge")))

    # Régularité : des semaines, jamais des jours d'affilée (v0.25 : série « à la Strava » + semaines à 3 sorties)
    for wk, xp, tier in [(12, 10, 1), (26, 20, 1), (52, 60, 2), (104, 150, 3), (208, 300, 4)]:
        T.append(Trophy(f"serie_{wk}", "regularite", f"Série de {wk} semaines",
                        f"{wk} semaines de suite avec au moins une activité, tous sports (comme la série Strava).",
                        xp, tier, lambda f, w=wk: f.active_streak_first(w),
                        lambda f: f"série en cours : {f.current_active_streak()} semaines"))
    for n, xp, tier in [(25, 15, 1), (100, 50, 2), (200, 120, 3)]:
        T.append(Trophy(f"sem3_{n}", "regularite", f"{n} semaines à 3 sorties",
                        f"{n} semaines (pas forcément de suite) avec au moins 3 sorties de course.", xp, tier,
                        lambda f, n=n: f.weeks_with_runs_first(n), lambda f: f"{f.weeks_with_runs()} semaines"))
    T.append(Trophy("douze_mois", "regularite", "12 mois sur 12", "Au moins une sortie chaque mois d'une année civile.",
                    40, 2, lambda f: f.all_months_first()))
    T.append(Trophy("leve_tot", "regularite", "Lève-tôt", "20 sorties commencées avant 7 h.", 20, 1,
                    lambda f: f.nth_first([a for a in f.runs if f.local_hour(a) < 7], 20),
                    lambda f: f"{sum(1 for a in f.runs if f.local_hour(a) < 7)} sorties"))

    # Intelligence : progresser sans se blesser
    for wk, xp, tier in [(8, 30, 1), (26, 80, 2), (52, 150, 3)]:
        T.append(Trophy(f"sage_{wk}", "intelligence", f"{wk} semaines sans pic de charge",
                        f"{wk} semaines entraînées de suite sans dépasser 1,5 × ta charge habituelle.", xp, tier,
                        lambda f, w=wk: f.no_spike_weeks_first(w)))
    T.append(Trophy("bloc_complet", "intelligence", "Bloc complet", "4 semaines de suite où au moins 90 % des séances "
                    "du plan sont faites (bonus non compris).", 60, 2, lambda f: f.full_block_first(4)))
    for n, xp, tier in [(5, 10, 1), (25, 30, 2), (100, 80, 3)]:
        T.append(Trophy(f"retours_{n}", "intelligence", f"{n} retours au coach",
                        f"{n} séances validées avec ton ressenti ou une note.", xp, tier,
                        lambda f, n=n: f.feedback_count_first(n), lambda f: None))
    # Événements (v0.33) : une fois chacun, rejoués d'année en année dans la progression
    def day_event(tid, label, desc, xp, days, tier=1):
        T.append(Trophy(tid, "evenements", label, desc, xp, tier,
                        lambda f: next(iter(f.runs_on(days)), None),
                        lambda f: f"couru {len(f.runs_on(days))} fois" if f.runs_on(days) else None))
    day_event("nouvel_an", "Premier de l'an", "Courir le 1er janvier.", 20, lambda y: {date(y, 1, 1)})
    T.append(Trophy("resolutions", "evenements", "Bonnes résolutions", "8 jours de course entre le 1er et le 14 janvier.",
                    30, 2, lambda f: f.window_count_first((1, 1), (1, 14), 8)))
    day_event("paques", "Chasse aux œufs", "Courir le dimanche ou le lundi de Pâques.", 15,
              lambda y: {easter(y), easter(y) + timedelta(days=1)})
    day_event("solstice", "Plus long jour", "Courir le 21 juin, jour du solstice d'été.", 15, lambda y: {date(y, 6, 21)})
    day_event("fete_nationale", "14 Juillet", "Courir le jour de la fête nationale.", 15, lambda y: {date(y, 7, 14)})
    day_event("halloween", "Course d'Halloween", "Courir le 31 octobre.", 20, lambda y: {date(y, 10, 31)})
    T.append(Trophy("avent", "evenements", "Calendrier de l'Avent", "12 jours de course entre le 1er et le 24 décembre.",
                    40, 2, lambda f: f.window_count_first((12, 1), (12, 24), 12)))
    day_event("noel", "Course de Noël", "Courir le 24 ou le 25 décembre.", 20, lambda y: {date(y, 12, 24), date(y, 12, 25)})
    day_event("saint_sylvestre", "Saint-Sylvestre", "Courir le 31 décembre.", 15, lambda y: {date(y, 12, 31)})
    T.append(Trophy("anniversaire", "evenements", "Joyeux anniversaire", "Courir le jour de ton anniversaire.", 20, 1,
                    lambda f: next(iter(f.runs_on(lambda y: {birthday_of(f.birthday, y)} if f.birthday else set())), None),
                    lambda f: None if f.birthday else "date d'anniversaire non renseignée (carrière › Mon anniversaire)"))
    return T


# événements à venir (v0.33) : (id du trophée, nom, première date de l'année, dernière date)
def _event_windows(year: int, birthday: str | None) -> list[tuple[str, str, date, date]]:
    ev = [("nouvel_an", "Premier de l'an", date(year, 1, 1), date(year, 1, 1)),
          ("resolutions", "Bonnes résolutions", date(year, 1, 1), date(year, 1, 14)),
          ("paques", "Pâques", easter(year), easter(year) + timedelta(days=1)),
          ("solstice", "Solstice d'été", date(year, 6, 21), date(year, 6, 21)),
          ("fete_nationale", "14 Juillet", date(year, 7, 14), date(year, 7, 14)),
          ("halloween", "Halloween", date(year, 10, 31), date(year, 10, 31)),
          ("avent", "Calendrier de l'Avent", date(year, 12, 1), date(year, 12, 24)),
          ("noel", "Noël", date(year, 12, 24), date(year, 12, 25)),
          ("saint_sylvestre", "Saint-Sylvestre", date(year, 12, 31), date(year, 12, 31))]
    b = birthday_of(birthday, year)
    if b:
        ev.append(("anniversaire", "Ton anniversaire", b, b))
    return ev


def upcoming_events(f: Facts, trophies: list[dict], horizon: int = 14) -> list[dict]:
    """Événements en cours ou dans les `horizon` prochains jours, avec ce qu'il faut faire et la progression."""
    by_id = {t["id"]: t for t in trophies}
    out = []
    for year in (f.today.year, f.today.year + 1):
        for tid, name, start, end in _event_windows(year, f.birthday):
            if end < f.today or (start - f.today).days > horizon:
                continue
            t = by_id.get(tid)
            if not t:
                continue
            ran = sorted({a.local_day for a in f.runs if start <= a.local_day <= end})
            need = 8 if tid == "resolutions" else 12 if tid == "avent" else 1
            out.append({"id": tid, "name": name, "label": t["label"], "desc": t["desc"], "xp": t["xp"],
                        "start": start.isoformat(), "end": end.isoformat(),
                        "days_left": max(0, (start - f.today).days), "live": start <= f.today <= end,
                        "done": len(ran) >= need, "count": len(ran), "need": need, "earned": bool(t["day"])})
    return sorted(out, key=lambda e: e["start"])


CATALOG = _catalog()


# --------------------------------------------------------------------------- calcul
def career(facts: Facts) -> dict:
    trophies, events = [], []
    for t in CATALOG:
        d = t.check(facts)
        if d and d > facts.today:
            d = facts.today
        item = {"id": t.id, "family": t.family, "label": t.label, "desc": t.desc, "xp": t.xp, "tier": t.tier,
                "day": d.isoformat() if d else None}
        if not d and t.progress:
            try:
                item["progress"] = t.progress(facts)
            except Exception:
                item["progress"] = None
        trophies.append(item)
        if d:
            events.append((d, t.xp, "trophy", t.label))
    # séances du plan validées : un peu d'XP à chaque fois
    session_xp = 0
    for f in facts.feedback:
        base = SESSION_XP.get(f.get("status"), 0)
        s = facts.plan.get(f["session_id"])
        if f["session_id"].endswith("-bonus"):
            base = BONUS_XP if f.get("status") in ("done", "modified") else 0
        elif base and s is not None and s.intensity in KEY_INTENSITIES:
            base = KEY_SESSION_XP if f["status"] == "done" else base
        if base:
            session_xp += base
            events.append((date.fromisoformat(f["day"]), base, "session", None))
    events.sort(key=lambda e: e[0])
    xp_by_day: dict[str, int] = {}                     # v0.50 : XP gagnée par jour (récap de la semaine)
    for d, gain, _k, _l in events:
        xp_by_day[d.isoformat()] = xp_by_day.get(d.isoformat(), 0) + gain
    xp, levels, timeline = 0, [], []
    lvl = 1
    for d, gain, kind, label in events:
        xp += gain
        new = level_for(xp)["level"]
        if kind == "trophy":
            timeline.append({"day": d.isoformat(), "label": label, "xp": gain})
        while lvl < new:
            lvl += 1
            levels.append({"day": d.isoformat(), "level": lvl, "rank": level_for(total_for_level(lvl))["rank"]})
    lv = level_for(xp)
    got = {t["id"]: t["label"] for t in trophies if t["day"]}
    names = {t["id"]: t["label"] for t in trophies}
    unlocked = [{"level": l, "id": i, "label": n, "unlocked": lv["level"] >= l, "how": f"niv. {l}"} for l, i, n in GEAR]
    unlocked += [{"level": None, "id": i, "label": n, "unlocked": tid in got, "how": f"trophée « {names.get(tid, tid)} »",
                  **({"seasonal": True, "in_season": in_season(i, facts.today, facts.birthday)} if i in SEASONAL else {})}
                 for tid, i, n in TROPHY_GEAR]
    # décor automatique : la saison du moment si elle est débloquée, sur le dernier lieu débloqué par niveau
    scenes = scene_unlocks(lv["level"], set(got), names)
    ok = {x["id"] for x in scenes if x["unlocked"]}
    auto = resolve_scene({}, lv["level"], facts.today, ok)
    scene = auto["scene"]
    by_id = {x["id"]: x for x in scenes}
    places = [{**by_id[i], "label": PLACE_LABELS[i]} for i in PLACES]
    ambiances = [{"id": NO_AMBIANCE, "label": AMBIANCE_LABELS[NO_AMBIANCE], "unlocked": True, "how": ""}]
    ambiances += [{**by_id[i], "label": AMBIANCE_LABELS[i]} for i in AMBIANCES]
    done = [t for t in trophies if t["day"]]
    return {**lv, "stars": max(0, lv["level"] - MAX_ICON_LEVEL), "face_level": FACE_LEVEL, "trophies": trophies, "families": FAMILIES, "timeline": timeline[::-1], "levels": levels[::-1],
            "gear": unlocked, "scene": scene, "auto_scene": scene, "scenes": scenes,
            "place": auto["place"], "ambiance": auto["ambiance"], "auto_place": auto["place"],
            "auto_ambiance": auto["ambiance"], "places": places, "ambiances": ambiances,
            "events": upcoming_events(facts, trophies), "birthday": facts.birthday, "session_xp": session_xp, "count": len(done), "total": len(trophies),
            "next_levels": [{"level": n, "at": total_for_level(n)} for n in range(lv["level"] + 1, lv["level"] + 4)],
            "xp_by_day": xp_by_day}


AVATAR_DEFAULT = {"skin": 2, "hair": "court", "hair_color": 1, "beard": False, "shirt": 0, "shorts": 1, "shoes": 2,
                  "gear": None, "scene": None, "place": None, "ambiance": None}
AVATAR_CHOICES = {"skin": range(6), "hair": ("court", "long", "chignon", "boucles", "rase"), "hair_color": range(6),
                  "shirt": range(8), "shorts": range(6), "shoes": range(6)}


def clean_avatar(x: dict, level: int, unlocked_gear: set | None = None, unlocked_scenes: set | None = None) -> dict:
    """Garde seulement des choix connus et débloqués. `unlocked_*` : ce que career() a débloqué (niveaux ET trophées) ;
    sans eux, seuls les niveaux comptent."""
    out = dict(AVATAR_DEFAULT)
    for k, allowed in AVATAR_CHOICES.items():
        if k in x and x[k] in allowed:
            out[k] = x[k]
    out["beard"] = bool(x.get("beard", False))
    allowed_gear = unlocked_gear if unlocked_gear is not None else {i for l, i, n in GEAR if level >= l}
    scenes_ok = unlocked_scenes if unlocked_scenes is not None else {i for l, i in LEVEL_SCENES if level >= l}
    # v0.47 : lieu et ambiance, chacun None = automatique ; un ancien `scene` seul est migré (split_scene)
    place, amb = split_scene(x)
    out["place"] = place if place in PLACES and place in scenes_ok else None
    out["ambiance"] = amb if amb == NO_AMBIANCE or (amb in AMBIANCES and amb in scenes_ok) else None
    # `scene` gardé pour les anciens clients : le choix explicite (lieu d'abord), sinon None = décor automatique
    out["scene"] = out["place"] or (out["ambiance"] if out["ambiance"] != NO_AMBIANCE else None)
    # gear None = « auto » : tout l'équipement débloqué est porté
    out["gear"] = [g for g in x["gear"] if g in allowed_gear] if isinstance(x.get("gear"), list) else None
    return out


def worn_gear(avatar: dict, gear: list[dict]) -> list[str]:
    """Équipement porté (même règle que le client) : auto = tout le débloqué, sauf les accessoires de saison hors
    saison ; un seul chapeau, qui remplace casquette, bandeau et laurier."""
    unlocked = [g["id"] for g in gear if g["unlocked"]]
    season = {g["id"] for g in gear if g.get("seasonal") and not g.get("in_season")}
    worn = ([g for g in unlocked if g not in season] if avatar.get("gear") is None
            else [g for g in avatar["gear"] if g in unlocked])
    hats = [h for h in HATS if h in worn]
    if hats:
        worn = [g for g in worn if g not in ("casquette", "bandeau", "laurier") and (g not in HATS or g == hats[0])]
    return [g for g in worn if not (g == "bandeau" and "casquette" in worn)]
