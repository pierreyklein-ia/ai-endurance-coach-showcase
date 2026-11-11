"""v0.49 — Volume maximum par semaine : ce que l'athlète PEUT faire (temps, et s'il le dit km et D+).

« Il faut aussi demander quel est le volume (temps, D+, distance) max que la personne peut faire par semaine. »
Le plafond est demandé à l'accueil (ou estimé d'après l'historique s'il ne sait pas), enregistré dans les
préférences (`max_week_min`, `max_week_km`, `max_week_dplus`) et appliqué partout :

- génération du plan (1er plan, nouveaux plans, phase spécifique) : `fit_week` raccourcit d'abord les footings,
  puis la sortie longue, sans descendre sous des planchers raisonnables ;
- propositions du coach : règle R10_week_max des garde-fous (bloquante) ; déplacement par l'athlète : alerte.

Le plan ne contient que des durées : les km et le D+ d'une séance sont ESTIMÉS à partir des habitudes de l'athlète
(vitesse moyenne et D+ par heure de ses 12 dernières semaines), ou du D+ par km écrit dans les séances spécifiques
(« ~32 m D+/km »).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel

DEFAULT_KMH = 9.0               # vitesse moyenne par défaut (footing, trail compris)
INFER_RATIO = 1.3               # « je ne sais pas » : 1,3 × la meilleure moyenne sur 4 semaines récente
INFER_WEEKS = 26                # ... sur les 6 derniers mois
INFER_FLOOR_MIN = 120
INFER_DEFAULT_MIN = 300         # pas d'historique exploitable : 5 h
EASY_FLOOR = 30                 # footing
LONG_FLOOR_SOFT = 60            # sortie longue : on la raccourcit jusque-là avant de retirer des footings
LONG_FLOOR = 45
QUALITY_FLOOR = 45
TOLERANCE = 0.05                # garde-fou : 5 % de marge (arrondis des durées, estimation des km)
FLAT = {"vo2", "threshold", "tempo"}   # séances sur piste ou plat : peu de D+


@dataclass(frozen=True)
class WeekMax:
    minutes: int | None = None
    km: float | None = None
    dplus: int | None = None
    kmh: float = DEFAULT_KMH        # vitesse moyenne habituelle (pour estimer les km d'une séance)
    dph: float = 0.0                # D+ habituel par heure de course (pour estimer le D+)
    inferred: bool = False          # estimé d'après l'historique (« je ne sais pas »)
    week_start: int = 0             # v0.50 : premier jour de la semaine de l'athlète (0 = lundi)

    def any(self) -> bool:
        return bool(self.minutes or self.km or self.dplus)


def h_txt(minutes: float) -> str:
    m = int(round(minutes / 5) * 5)
    return f"{m // 60} h {m % 60:02d}".replace(" h 00", " h") if m >= 60 else f"{m} min"


def describe(minutes: int | None, km: float | None, dplus: int | None) -> str:
    """« 6 h, 50 km et 1500 m D+ par semaine »."""
    bits = [x for x in [h_txt(minutes) if minutes else "", f"{km:g} km" if km else "",
                        f"{dplus} m D+" if dplus else ""] if x]
    if not bits:
        return ""
    return (", ".join(bits[:-1]) + " et " + bits[-1] if len(bits) > 1 else bits[0]) + " par semaine"


def _get(prefs: Any, k: str):
    return prefs.get(k) if isinstance(prefs, dict) else getattr(prefs, k, None)


def rates(diag: Any) -> tuple[float, float]:
    """(km/h, m D+/h) habituels d'après le bilan (dict ou Diagnosis)."""
    if diag is None:
        return DEFAULT_KMH, 0.0
    hours = _get(diag, "run_hours_per_week") or 0
    km = _get(diag, "run_km_per_week") or 0
    dp = _get(diag, "dplus_per_week") or 0
    kmh = km / hours if hours > 0.5 and km > 0 else DEFAULT_KMH
    return max(5.0, min(16.0, kmh)), (max(0.0, min(900.0, dp / hours)) if hours > 0.5 else 0.0)


def from_prefs(prefs: Any, diag: Any = None) -> WeekMax | None:
    if prefs is None:
        return None
    mn, km, dp = _get(prefs, "max_week_min"), _get(prefs, "max_week_km"), _get(prefs, "max_week_dplus")
    if not (mn or km or dp):
        return None
    kmh, dph = rates(diag)
    return WeekMax(int(mn) if mn else None, float(km) if km else None, int(dp) if dp else None, kmh, dph,
                   bool(_get(prefs, "max_week_inferred")), int(_get(prefs, "week_start") or 0) % 7)


# --------------------------------------------------------------------------- estimations
def counted(s) -> bool:
    """Séance qui compte dans le volume : ni repos/renfo, ni course (jour J)."""
    return s.intensity not in ("rest", "race") and not s.is_race and s.duration_min > 0


def session_km(s, wm: WeekMax) -> float:
    k = 1.1 if s.intensity in FLAT else 0.85 if s.intensity == "hills" else 1.0
    return s.duration_min / 60 * wm.kmh * k


_DPK = re.compile(r"~\s*(\d+)\s*m D\+/km")


def session_dplus(s, wm: WeekMax) -> float:
    if s.elevation_gain_m:
        return float(s.elevation_gain_m)
    m = _DPK.search(s.description or "")
    if m:                                       # séance spécifique : le D+ par km de la course
        return session_km(s, wm) * int(m[1])
    k = 0.3 if s.intensity in FLAT else 1.5 if s.intensity == "hills" else 1.0
    return s.duration_min / 60 * wm.dph * k


def totals(sessions, wm: WeekMax) -> tuple[float, float, float]:
    xs = [s for s in sessions if counted(s)]
    return (float(sum(s.duration_min for s in xs)), sum(session_km(s, wm) for s in xs),
            sum(session_dplus(s, wm) for s in xs))


def excess_minutes(tot: tuple[float, float, float], wm: WeekMax, done: tuple[float, float, float] = (0, 0, 0),
                   tol: float = 0.0) -> float:
    """Dépassement exprimé en minutes de course (le plus contraignant des trois plafonds)."""
    mn, km, dp = (tot[i] + done[i] for i in range(3))
    over = 0.0
    if wm.minutes:
        over = max(over, mn - wm.minutes * (1 + tol))
    if wm.km:
        over = max(over, (km - wm.km * (1 + tol)) * 60 / max(wm.kmh, 1))
    if wm.dplus and wm.dph > 0:
        over = max(over, (dp - wm.dplus * (1 + tol)) * 60 / wm.dph)
    elif wm.dplus and dp > wm.dplus * (1 + tol):  # D+ écrit dans les séances sans habitude connue
        over = max(over, 5.0)
    return over


def over_text(tot: tuple[float, float, float], wm: WeekMax, tol: float = 0.0) -> list[str]:
    out = []
    if wm.minutes and tot[0] > wm.minutes * (1 + tol):
        out.append(f"{h_txt(tot[0])} > {h_txt(wm.minutes)}")
    if wm.km and tot[1] > wm.km * (1 + tol):
        out.append(f"~{tot[1]:.0f} km > {wm.km:g} km")
    if wm.dplus and tot[2] > wm.dplus * (1 + tol):
        out.append(f"~{tot[2]:.0f} m D+ > {wm.dplus} m D+")
    return out


# --------------------------------------------------------------------------- application au plan
def _resize(s, minutes: int):
    """v0.50 : durée, texte (moins de répétitions, échauffement plus court), D+ et charge restent cohérents."""
    from .session_content import resize_session
    return resize_session(s, minutes)


def fit_week(sessions: list, wm: WeekMax, done: tuple[float, float, float] = (0, 0, 0),
             keep: set[str] | None = None, min_days: int = 2) -> tuple[list, bool]:
    """Ramène UNE semaine sous le plafond. Ordre : footings (30 min mini), sortie longue (jusqu'à 1 h), retrait de
    footings (on garde au moins `min_days` jours de course), sortie longue (45 min mini), séances de qualité
    (45 min mini, sauf celles de `keep`, ex. la séance du club). Renvoie (séances, raccourci ?)."""
    keep = keep or set()
    out = list(sessions)
    trimmed = False

    def over() -> float:
        return excess_minutes(totals([s for s in out if s is not None], wm), wm, done)

    def cut(pred, floor: int) -> None:
        nonlocal trimmed
        for _ in range(500):                            # par pas de 5 min, la plus longue d'abord : équilibré
            if over() <= 0:
                return
            cands = [i for i, s in enumerate(out) if s is not None and counted(s) and pred(s)
                     and s.duration_min > floor]
            if not cands:
                return
            i = max(cands, key=lambda j: (out[j].duration_min, j))
            s = out[i]
            out[i] = _resize(s, max(floor, s.duration_min - 5))
            trimmed = True

    easy = lambda s: s.intensity in ("z1", "z2") and s.session_id not in keep   # noqa: E731
    long_ = lambda s: s.intensity == "long" and s.session_id not in keep       # noqa: E731
    quality = lambda s: s.intensity in ("vo2", "threshold", "tempo", "hills") and s.session_id not in keep  # noqa: E731
    cut(easy, EASY_FLOOR)
    cut(long_, LONG_FLOOR_SOFT)
    while over() > 0:                                   # retirer des footings, les plus courts d'abord
        days = {s.day for s in out if s is not None and counted(s)}
        cands = [i for i, s in enumerate(out) if s is not None and counted(s) and easy(s)]
        if not cands or len(days) <= min_days:
            break
        i = min(cands, key=lambda j: out[j].duration_min)
        out[i] = None
        trimmed = True
    cut(long_, LONG_FLOOR)
    cut(quality, QUALITY_FLOOR)
    return [s for s in out if s is not None], trimmed


def fit_plan(sessions: list, wm: WeekMax, start: date, done_first_week: tuple[float, float, float] = (0, 0, 0),
             keep: set[str] | None = None, is_bonus=lambda s: False) -> tuple[list, list[date]]:
    """Applique le plafond semaine par semaine (semaines de l'athlète : lundi-dimanche par défaut, v0.50 : son premier
    jour). Les séances bonus facultatives comptent : elles sont retirées des semaines où elles feraient dépasser.
    Renvoie (séances, débuts de semaine raccourcis)."""
    if not wm or not wm.any():
        return sessions, []
    ws = wm.week_start % 7
    mondays = sorted({s.day - timedelta(days=(s.day.weekday() - ws) % 7) for s in sessions})
    first = start - timedelta(days=(start.weekday() - ws) % 7)
    out, touched = [], []
    for m in mondays:
        wk = [s for s in sessions if m <= s.day < m + timedelta(days=7)]
        bonus = [s for s in wk if is_bonus(s)]
        core = [s for s in wk if not is_bonus(s)]
        done = done_first_week if m == first else (0, 0, 0)
        fitted, t = fit_week(core, wm, done, keep)
        for b in bonus:
            if excess_minutes(totals(fitted + [b], wm), wm, done) <= 0:
                fitted.append(b)
            else:
                t = True
        if t:
            touched.append(m)
        out += fitted
    return sorted(out, key=lambda s: (s.day, s.session_id)), touched


# --------------------------------------------------------------------------- historique : « je ne sais pas »
def best_4w_avg_min(acts, today: date, weeks: int = INFER_WEEKS) -> float:
    """Meilleure moyenne hebdomadaire (minutes de course) sur 4 semaines glissantes, sur les `weeks` dernières."""
    from .diagnosis import is_run
    lo = today - timedelta(days=7 * weeks)
    per_week: dict[date, float] = {}
    for a in acts:
        if a.local_day < lo or a.local_day >= today or not is_run(a):
            continue
        m = a.local_day - timedelta(days=a.local_day.weekday())
        per_week[m] = per_week.get(m, 0.0) + a.duration_s / 60
    if not per_week:
        return 0.0
    m0 = min(per_week)
    mondays = [m0 + timedelta(weeks=i) for i in range((max(per_week) - m0).days // 7 + 1)]
    vals = [per_week.get(m, 0.0) for m in mondays]
    if len(vals) < 4:
        return sum(vals) / 4
    return max(sum(vals[i:i + 4]) / 4 for i in range(len(vals) - 3))


def infer(acts, today: date, typical_min: float = 0.0) -> tuple[int, float]:
    """Plafond estimé (minutes, arrondi au quart d'heure) et la base utilisée (meilleure moyenne sur 4 semaines)."""
    best = best_4w_avg_min(acts, today)
    base = best or typical_min
    mins = INFER_RATIO * base if base else INFER_DEFAULT_MIN
    return int(max(INFER_FLOOR_MIN, round(mins / 15) * 15)), best


# --------------------------------------------------------------------------- lecture de la réponse
class WeekMaxExtract(BaseModel):
    """Réponse à « quel volume maximum peux-tu faire par semaine ? »."""
    hours: float | None = None
    km: float | None = None
    dplus_m: int | None = None
    unknown: bool = False           # « je ne sais pas » : estimé d'après l'historique
    no_limit: bool = False          # « pas de limite »
    reply: str = ""


WEEK_MAX_SYSTEM = """Tu es un coach de course à pied et de trail. Tu as demandé à un nouvel athlète le volume MAXIMUM
qu'il peut faire sur une semaine (temps, et éventuellement distance et dénivelé) et il répond. Extrais UNIQUEMENT ce
qu'il dit :
- hours : heures de course par semaine au maximum (« 6 h » = 6 ; « 6h30 » = 6.5 ; « 300 min » = 5) ;
- km : kilomètres par semaine au maximum s'il en donne ; dplus_m : mètres de D+ par semaine au maximum s'il en donne ;
- unknown = true s'il ne sait pas (« je ne sais pas », « aucune idée », « à toi de voir ») ;
- no_limit = true s'il dit n'avoir aucune limite.
Un nombre seul sans unité (« 6 ») = des heures. N'invente rien : un champ non mentionné reste vide.
`reply` : une phrase de coach qui accuse réception, sans chiffre qu'il n'a pas donné."""

_UNKNOWN = re.compile(r"je (ne )?sais pas|j'?sais pas|\bsais pas\b|aucune id[ée]e|pas d'id[ée]e|\bnsp\b|"
                      r"à toi de voir|a toi de voir|je te laisse|je ne vois pas|aucune idée|pas s[uû]r|"
                      r"\bne sait pas\b|je n'en sais rien|bonne question", re.I)
_NO_LIMIT = re.compile(r"pas de (limite|max|plafond)|aucune limite|sans limite|illimit|pas vraiment de limite", re.I)
_MAX_WORD = re.compile(r"\bmax\b|maximum|\bmaxi\b|au plus|pas plus d|jusqu'?(à|a)\b|monter (à|a|jusqu)|plafond|"
                       r"\blimite\b|ne (peux|pourrai) pas (faire )?plus|je peux (faire|aller|monter|courir)|"
                       r"au-del[àa]", re.I)


def _f(x: str) -> float:
    return float(x.replace(",", ".").replace(" ", "").replace(" ", "").replace(" ", ""))


def parse_week_max(text: str, bare_hours: bool = True) -> WeekMaxExtract:
    """Heuristique hors ligne : « 6 h max », « pas plus de 50 km et 1500 m D+ », « 6h30 », « je ne sais pas »."""
    t = text.lower().strip()
    out: dict = {}
    m = re.search(r"(\d[\d\s  ]*(?:[.,]\d+)?)\s*(?:m(?:ètres?|etres?)?\s*)?(?:de\s+)?"
                  r"(?:d\+|d\s?\+|dénivel|denivel|déniv|deniv)", t)
    if m:
        out["dplus_m"] = int(_f(m[1]))
        t2 = t[:m.start()] + " " + t[m.end():]
    else:
        t2 = t
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:km|kilom)", t2)
    if m:
        out["km"] = _f(m[1])
        t2 = t2[:m.start()] + " " + t2[m.end():]
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:h(?:eures?)?\b|h(?=\d))\s*(\d{1,2})?", t2)
    if m:
        out["hours"] = _f(m[1]) + (int(m[2]) / 60 if m[2] else 0)
    else:
        m = re.search(r"(\d{2,3})\s*min", t2)
        if m:
            out["hours"] = int(m[1]) / 60
        elif bare_hours and not out:
            m = re.fullmatch(r"\D{0,30}?(\d{1,2}(?:[.,]5)?)\s*(?:max(?:imum)?)?\W*", t2.strip())
            if m and 1 <= _f(m[1]) <= 30:
                out["hours"] = _f(m[1])
    if not out:
        if _NO_LIMIT.search(t):
            return WeekMaxExtract(no_limit=True, reply="D'accord, pas de plafond : je reste guidé par ton historique.")
        if _UNKNOWN.search(t):
            return WeekMaxExtract(unknown=True, reply="Pas de souci, je l'estime d'après ton historique.")
        return WeekMaxExtract()
    return WeekMaxExtract(**out, reply="C'est noté.")


def looks_like_week_max(text: str) -> bool:
    """Dans la discussion (après l'accueil) : « je peux monter à 7 h par semaine », « 50 km max par semaine »."""
    t = text.lower()
    if not _MAX_WORD.search(t):
        return False
    x = parse_week_max(t, bare_hours=False)
    return bool(x.hours or x.km or x.dplus_m) and bool(re.search(r"semaine|hebdo|/\s*sem", t))


def clamp(x: WeekMaxExtract) -> tuple[int | None, float | None, int | None]:
    mn = int(max(60, min(40 * 60, round(x.hours * 60 / 5) * 5))) if x.hours else None
    km = float(max(5.0, min(400.0, round(x.km, 1)))) if x.km else None
    dp = int(max(100, min(20_000, x.dplus_m))) if x.dplus_m else None
    return mn, km, dp


def question(best_min: float = 0.0) -> str:
    seen = (f" Pour repère, ta plus grosse période récente tourne autour de {h_txt(best_min)} par semaine sur "
            "4 semaines." if best_min >= 30 else "")
    return ("Dernière question : quel est le volume maximum que tu peux faire sur une semaine ? En temps surtout "
            "(heures), et si tu as aussi une limite en distance (km) ou en dénivelé (m D+), dis-le. Par exemple : "
            "« 6 h max », « pas plus de 50 km et 1500 m D+ »." + seen
            + " Je ne dépasserai jamais ce plafond, même au cœur de la prépa (tu pourras le changer dans "
              "Carrière › Réglages).")


QUICK = ["3 h", "5 h", "8 h", "Je ne sais pas"]


def coach_brief(prefs: Any) -> str:
    """Phrase pour le contexte du coach (profil de l'athlète)."""
    if prefs is None:
        return ""
    txt = describe(_get(prefs, "max_week_min"), _get(prefs, "max_week_km"), _get(prefs, "max_week_dplus"))
    if not txt:
        return ""
    how = " (estimé d'après son historique, il ne savait pas)" if _get(prefs, "max_week_inferred") else ""
    return (f"Volume maximum que l'athlète peut faire : {txt}{how}. C'est un plafond dur : ne propose jamais une "
            "semaine au-delà (le garde-fou R10 la refuse) et explique tes plans dans ce cadre.")
