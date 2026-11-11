"""v0.50 — Contenu structuré d'une séance : échauffement, série principale, récupérations, retour au calme.

Retour du test : « Seuil · 40 min » affiché sur la carte, mais la description disait « 20 min d'échauffement, 3 × 8 min,
récup 2 min, 10 min de retour au calme » (≈ 58 min). Les plafonds (115 % de la 1re semaine, volume maximum, règle de
Frandsen…) raccourcissaient `duration_min` sans toucher au texte.

Une seule source désormais : `Workout` décrit la séance ; `build` en tire le texte ET la durée ; `resize` adapte une
séance à une nouvelle durée (moins de répétitions, échauffement plus court… ou l'inverse) en réécrivant ses nombres ;
`harmonize` est le filet de sécurité appliqué à chaque écriture du plan ; `check` est le contrôle utilisé par les tests
(|somme − durée| ≤ 5 min) et par la réparation des plans déjà enregistrés.

Formats lus : « X min d'échauffement » (ou « Échauffement X min »), « N × M min » (« 6 × 3-4 min »), « S × (N × 30 s
rapide / 30 s trot) », « N × 30/30 », « récup R min », « R min entre les blocs », « Y min de retour au calme ».
Fonctions pures, testées.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace

TOLERANCE_MIN = 5                     # écart toléré entre la somme des blocs et la durée affichée
WARM_SOFT, COOL_SOFT = 15, 10         # on raccourcit d'abord jusque-là ...
WARM_MIN, COOL_MIN = 10, 5            # ... puis, en dernier recours, jusque-là
WARM_MAX, COOL_MAX, WARM_LAST, COOL_LAST = 25, 15, 30, 20


def round5(x: float) -> int:
    return int(5 * math.floor(x / 5 + 0.5))


def _n(x: float) -> str:
    """2.0 -> « 2 », 2.5 -> « 2,5 »."""
    return f"{round(x, 1):g}".replace(".", ",")


def _f(s: str) -> float:
    return float(s.replace(",", "."))


@dataclass
class Workout:
    """Séance structurée. `on_s` / `off_s` : effort et récupération d'UNE répétition, en secondes."""
    warmup: float | None
    reps: int
    on_s: float
    off_s: float = 0.0
    sets: int = 1
    set_rest_s: float = 0.0
    cooldown: float | None = None
    short: bool = False                # 30/30 : répétitions courtes (la FC n'a pas le temps de monter)
    # texte (rendu par `render`)
    prefix: str = ""                   # « Double seuil, séance du matin : »
    on_txt: str = ""                   # après « N × M min » : « de montée régulière (8-15 %) »
    pace: str = ""                     # « à 4:00/km »
    off_txt: str = ""                  # après « récup R min » : « en redescendant en trottinant »
    middle: str = ""                   # entre la série et le retour au calme
    suffix: str = ""                   # après le retour au calme
    on_fixed: bool = False             # durée de répétition donnée en fourchette (« 3-4 min ») : pas réécrite
    spans: dict = field(default_factory=dict, repr=False, compare=False)   # positions des nombres (texte lu)

    def main_min(self) -> float:
        per_set = self.reps * self.on_s + max(0, self.reps - 1) * self.off_s
        return (self.sets * per_set + max(0, self.sets - 1) * self.set_rest_s) / 60

    def minutes(self) -> float:
        return (self.warmup or 0) + self.main_min() + (self.cooldown or 0)

    @property
    def complete(self) -> bool:
        return self.warmup is not None and self.cooldown is not None


# --------------------------------------------------------------------------- rendu (générateurs du plan)
def main_text(w: Workout) -> str:
    if w.short:
        if w.sets > 1:
            return (f"{w.sets} × ({w.reps} × {_n(w.on_s)} s rapide / {_n(w.off_s)} s trot){w.pace}, "
                    f"{_n(w.set_rest_s / 60)} min entre les blocs")
        return f"{w.reps} × {_n(w.on_s)}/{_n(w.off_s)}{w.pace}"
    txt = f"{w.reps} × {_n(w.on_s / 60)} min{w.on_txt}{w.pace}"
    if w.off_s and w.reps > 1:
        txt += f", récup {_n(w.off_s / 60)} min{w.off_txt}"
    return txt


def render(w: Workout) -> str:
    return (f"{w.prefix}{_n(w.warmup or 0)} min d'échauffement, {main_text(w)}{w.middle}, "
            f"{_n(w.cooldown or 0)} min de retour au calme.{w.suffix}")


def build(w: Workout, target: int | None = None) -> tuple[str, int]:
    """Texte et durée d'une séance, tirés de la même structure. `target` : durée voulue (le contenu s'y adapte)."""
    if target is not None:
        w = fit(w, target)
        return render(w), int(target) if abs(w.minutes() - target) <= TOLERANCE_MIN else round5(w.minutes())
    return render(w), round5(w.minutes())


# --------------------------------------------------------------------------- lecture d'un texte
_NUM = r"(\d+(?:[.,]\d+)?)"
_WARM = re.compile(_NUM + r"\s*min(?:utes)?\s+d['’]\s*[ée]chauffement|[ée]chauffement\s*(?:de\s*|:\s*)?" + _NUM + r"\s*min",
                   re.I)
_COOL = re.compile(_NUM + r"\s*min(?:utes)?\s+(?:de\s+|d['’]\s*)?retour au calme|retour au calme\s*(?:de\s*|:\s*)?"
                   + _NUM + r"\s*min", re.I)
_SETS = re.compile(r"(\d+)\s*[×x]\s*\(\s*(\d+)\s*[×x]\s*(\d+)\s*(?:s\b[^/)]*)?/\s*(\d+)\s*(?:s\b)?[^)]*\)", re.I)
_BETWEEN = re.compile(_NUM + r"\s*min\s+entre\s+(?:les\s+)?(?:blocs|séries|series)", re.I)
_SHORT = re.compile(r"(\d+)\s*[×x]\s*(\d+)\s*(?:s\s*)?/\s*(\d+)\b(?!\s*(?:km|min))", re.I)
_MINREPS = re.compile(r"(\d+)\s*[×x]\s*" + _NUM + r"(?:\s*[-–]\s*" + _NUM + r")?\s*min\b", re.I)
_REC = re.compile(r"r[ée]cup(?:ération)?\s*(?:de\s*|:\s*|en\s+\w+\s+)?" + _NUM + r"\s*min", re.I)


def _grp(m: re.Match, *idx: int) -> tuple[float, tuple[int, int]] | None:
    for i in idx:
        if m.group(i) is not None:
            return _f(m.group(i)), m.span(i)
    return None


def parse(desc: str) -> Workout | None:
    """Structure lue dans un texte de séance (None : pas de série principale reconnue)."""
    if not desc:
        return None
    spans: dict = {}
    w = dict(warmup=None, cooldown=None)
    m = _WARM.search(desc)
    if m:
        v, sp = _grp(m, 1, 2)
        w["warmup"], spans["warmup"] = v, sp
    c = _COOL.search(desc)
    if c:
        v, sp = _grp(c, 1, 2)
        w["cooldown"], spans["cooldown"] = v, sp
    after = m.end() if m else 0
    s = _SETS.search(desc, after)
    if s:
        spans.update(sets=s.span(1), reps=s.span(2))
        b = _BETWEEN.search(desc, s.end())
        return Workout(reps=int(s[2]), on_s=float(s[3]), off_s=float(s[4]), sets=int(s[1]),
                       set_rest_s=_f(b[1]) * 60 if b else 0.0, short=True, spans=spans, **w)
    sh = _SHORT.search(desc, after)
    mr = _MINREPS.search(desc, after)
    if sh and (not mr or sh.start() < mr.start()):
        spans["reps"] = sh.span(1)
        return Workout(reps=int(sh[1]), on_s=float(sh[2]), off_s=float(sh[3]), short=True, spans=spans, **w)
    if not mr:
        return None
    spans["reps"] = mr.span(1)
    lo, hi = _f(mr[2]), (_f(mr[3]) if mr[3] else None)
    on_fixed = hi is not None
    if not on_fixed:
        spans["on"] = mr.span(2)
    rc = _REC.search(desc, mr.end())
    nxt = _MINREPS.search(desc, mr.end())
    if rc and nxt and nxt.start() < rc.start():         # récup d'une autre série : ignorée
        rc = None
    if rc and c and rc.start() > c.start():
        rc = None
    return Workout(reps=int(mr[1]), on_s=(lo + hi) / 2 * 60 if on_fixed else lo * 60,
                   off_s=_f(rc[1]) * 60 if rc else 0.0, on_fixed=on_fixed, spans=spans, **w)


def rewrite(desc: str, old: Workout, new: Workout) -> str:
    """Réécrit les nombres du texte (échauffement, séries, répétitions, durée, retour au calme) selon `new`."""
    vals = {"warmup": new.warmup, "cooldown": new.cooldown, "sets": new.sets, "reps": new.reps,
            "on": new.on_s / 60}
    out = desc
    for k, (a, b) in sorted(old.spans.items(), key=lambda kv: -kv[1][0]):
        v = vals.get(k)
        if v is None:
            continue
        out = out[:a] + _n(v) + out[b:]
    return out


# --------------------------------------------------------------------------- adaptation à une durée
def fit(w: Workout, target: float) -> Workout:
    """Adapte la séance à `target` minutes. Plus court : échauffement (15 min), retour au calme (10 min), moins de
    répétitions, puis échauffement 10 min / retour au calme 5 min, puis des répétitions plus courtes. Plus long :
    échauffement (25 min), retour au calme (15 min), une répétition de plus si elle tient, échauffement (30 min)."""
    w = replace(w, spans=dict(w.spans))
    orig_reps = w.reps
    min_reps = 1 if orig_reps <= 1 else (6 if w.short and w.sets == 1 else 2)

    def over() -> float:
        return w.minutes() - target

    for _ in range(300):
        o = over()
        if o <= 0:
            break
        if w.warmup is not None and w.warmup > WARM_SOFT:
            w.warmup = max(WARM_SOFT, w.warmup - math.ceil(o))
        elif w.cooldown is not None and w.cooldown > COOL_SOFT:
            w.cooldown = max(COOL_SOFT, w.cooldown - math.ceil(o))
        elif w.short and w.sets > 1 and w.reps > 6:
            w.reps -= 1
        elif w.short and w.sets > 1:
            w.sets -= 1
        elif w.reps > min_reps:
            w.reps -= 1
        elif w.warmup is not None and w.warmup > WARM_MIN:
            w.warmup = max(WARM_MIN, w.warmup - math.ceil(o))
        elif w.cooldown is not None and w.cooldown > COOL_MIN:
            w.cooldown = max(COOL_MIN, w.cooldown - math.ceil(o))
        elif not w.short and not w.on_fixed and w.on_s > 120:
            w.on_s -= 60
        elif w.reps > 1:
            w.reps -= 1
        else:
            break
    max_reps = max(orig_reps + 2, orig_reps * 2)
    for _ in range(300):
        gap = target - w.minutes()
        if gap < 1 or (gap <= TOLERANCE_MIN / 2 and w.complete):
            break
        rep_cost = w.sets * (w.on_s + w.off_s) / 60
        if w.warmup is not None and w.warmup < WARM_MAX:
            w.warmup = min(WARM_MAX, w.warmup + math.floor(gap))
        elif w.cooldown is not None and w.cooldown < COOL_MAX:
            w.cooldown = min(COOL_MAX, w.cooldown + math.floor(gap))
        elif w.reps < max_reps and rep_cost <= gap:
            w.reps += 1
        elif w.warmup is not None and w.warmup < WARM_LAST:
            w.warmup = min(WARM_LAST, w.warmup + math.floor(gap))
        elif w.cooldown is not None and w.cooldown < COOL_LAST:
            w.cooldown = min(COOL_LAST, w.cooldown + math.floor(gap))
        else:
            break
    return w


def resize(desc: str, minutes: int) -> str:
    """Texte adapté à une nouvelle durée (inchangé s'il n'a pas de structure complète)."""
    w = parse(desc)
    if w is None or not w.complete:
        return desc
    if abs(w.minutes() - minutes) <= TOLERANCE_MIN / 2:
        return desc
    return rewrite(desc, w, fit(w, minutes))


def resize_session(s, minutes: int):
    """PlannedSession raccourcie (ou allongée) : durée, texte, D+ et charge prévue cohérents."""
    from .pilot.planner import estimate_load
    minutes = int(minutes)
    k = minutes / s.duration_min if s.duration_min else 1
    dp = int(s.elevation_gain_m * k)
    return s.model_copy(update={"duration_min": minutes, "description": resize(s.description, minutes),
                                "elevation_gain_m": dp,
                                "planned_load": estimate_load(s.intensity, minutes, dp) if s.planned_load else 0})


def check(desc: str, duration_min: float) -> str | None:
    """Contrôle de cohérence : message si la somme des blocs s'écarte de la durée de plus de 5 min (structure
    complète), ou si la série principale seule dépasse la durée (structure partielle). None si c'est cohérent."""
    w = parse(desc)
    if w is None:
        return None
    if w.complete:
        tot = w.minutes()
        if abs(tot - duration_min) > TOLERANCE_MIN:
            return f"blocs ≈ {tot:.0f} min pour une séance de {duration_min:.0f} min : « {desc[:90]} »"
        return None
    if w.main_min() > duration_min + TOLERANCE_MIN:
        return f"série principale ≈ {w.main_min():.0f} min pour une séance de {duration_min:.0f} min : « {desc[:90]} »"
    return None


def harmonize(s):
    """Filet de sécurité : une séance dont le texte ne correspond plus à la durée voit son texte réécrit
    (la durée, qui porte les plafonds, est gardée)."""
    if s.is_race or s.intensity in ("rest", "race") or not s.description:
        return s
    w = parse(s.description)
    if w is None or not w.complete or abs(w.minutes() - s.duration_min) <= TOLERANCE_MIN:
        return s
    new = rewrite(s.description, w, fit(w, s.duration_min))
    return s.model_copy(update={"description": new}) if new != s.description else s


def short_reps(desc: str) -> bool:
    """Répétitions courtes (moins de 2 min) : la FC monte en retard, l'allure et l'effort font foi."""
    w = parse(desc)
    return bool(w and (w.short or w.on_s < 120))


__all__ = ["Workout", "build", "render", "parse", "rewrite", "fit", "resize", "resize_session", "check",
           "harmonize", "short_reps", "round5", "TOLERANCE_MIN"]
