"""v0.38 — Simulateur de course de l'objectif principal : temps de passage, allures par tronçon, barrières, « et si ».

Déterministe (aucun LLM), rapide (< 300 ms) :

1. Parcours. Fichier de course (`data/races/<course>-<année>.json` : points officiels, km, services,
   barrières, sources) et, si l'athlète l'a déposé, son GPX (gardé dans son dossier privé, jamais
   versionné : Trace de Trail = usage personnel). Sans GPX : **profil approximatif** reconstruit à partir
   des altitudes des points et du D+ officiel (réparti par moitié de course grâce au découpage du relais :
   la partie non expliquée par les dénivelés nets est étalée au prorata des km, en petites bosses).
   Tronçons de 50 m dans les deux cas.
2. Vitesse relative selon la pente. La courbe personnelle du Race Pacer (`data/athletes/pilote.json`,
   calibrée sur les activités de l'athlète) si elle existe ; sinon le prior physiologique de Minetti
   (2002), avec un plafond « technique » en descente raide. Fatigue v ∝ (1 + t)^-β (β du profil, 0,08
   par défaut).
3. Ancrage. Le temps total de chaque scénario est CALÉ sur un pronostic : celui enregistré pour
   l'athlète (clé `race_forecast` du store : médiane / fourchette, ou score ITRA effectif + fourchette),
   sinon celui du fichier de course (VVX : 9 h 15, 8 h 50 – 9 h 50, ITRA ~605), sinon l'estimation de
   l'onglet Progrès (marathon × distance-effort, ±6 %). Le modèle ne fait que RÉPARTIR ce temps entre les
   tronçons (plus de temps dans les montées, fatigue en fin de course) : c'est ce qui rend les temps de
   passage cohérents avec un temps final crédible.
4. « Et si ». Chaleur (fraîche / normale / chaude : ralentit aux heures chaudes, augmente eau et sodium),
   forme du jour (−5 % … +5 % de vitesse), ou temps visé (réparti par le même modèle, la somme fait
   exactement le temps visé).
5. Barrières horaires (édition indiquée dans le fichier, marquées « approximatives » quand elles viennent
   d'une autre édition), marge du scénario prudent : vert ≥ 60 min, orange 20-60 min, rouge < 20 min.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from .pacer.model import minetti_cost, solar_elevation_deg

ROOT = Path(__file__).resolve().parents[1]
RACES = ROOT / "data" / "races"
PACER_PROFILE = ROOT / "data" / "athletes" / "pilote.json"
SEG_M = 50.0
DEFAULT_BETA = 0.08
SCENARIOS = (("ambitieux", "Ambitieux"), ("cible", "Cible"), ("prudent", "Prudent"))
STOP_MULT = {"ambitieux": 0.7, "cible": 1.0, "prudent": 1.5}
HEATS = {"fraiche": "fraîche", "normale": "normale", "chaude": "chaude"}
AID = {"ravito", "ravito_chaud", "base_vie", "eau"}
CLIMB_DROP_M, CLIMB_MIN_M = 50.0, 150.0
GPX_TOL_KM, GPX_TOL_DPLUS = 0.08, 0.15


# --------------------------------------------------------------------------- utilitaires
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return re.sub(r"[^a-z0-9 ]", " ", "".join(c for c in s if not unicodedata.combining(c)))


def _words(s: str) -> set[str]:
    return {w for w in _norm(s).split() if len(w) >= 3 or w.isdigit()}


def goal_key(name: str, day: date | None) -> str:
    return "-".join(_norm(name).split())[:60] + (f"-{day.isoformat()}" if day else "")


def clock(start: datetime, seconds: float) -> str:
    t = start + timedelta(minutes=round(float(seconds) / 60))
    return f"{t:%H:%M}"


def hm(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60} h {m % 60:02d}"


# --------------------------------------------------------------------------- fichiers de course
def find_course(name: str, day: date | None, root: Path = RACES) -> dict | None:
    """Fichier de course de l'objectif : même jour et un mot en commun, ou même année et deux mots."""
    words = _words(name)
    best, score = None, 0
    for p in sorted(Path(root).glob("*.json")):
        if p.name == "catalog.json":
            continue
        try:
            meta = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or "points" not in meta:
            continue
        d = meta.get("date") or str(meta.get("start_local", ""))[:10]
        s = len(words & _words(meta.get("race", "") + " " + " ".join(meta.get("aliases", []))))
        same_day = bool(day and d == day.isoformat())
        same_year = bool(day and meta.get("year") == day.year)
        ok = (same_day and s >= 1) or (same_year and s >= 2)
        if ok and s + 3 * same_day > score:
            best, score = {**meta, "_path": str(p)}, s + 3 * same_day
    return best


def generic_course(name: str, day: date | None, km: float, dplus: float | None, points: list[dict] | None) -> dict:
    """Course sans fichier : départ, ravitos saisis par l'athlète, arrivée. Départ supposé à 7 h."""
    pts = [{"name": "Départ", "km": 0, "services": []}]
    for p in sorted(points or [], key=lambda x: x["km"]):
        if 0 < p["km"] < km:
            pts.append({"name": p["name"][:60] or f"Ravito km {p['km']:g}", "km": float(p["km"]),
                        "services": ["ravito"], "stop_min": 1.5})
    pts.append({"name": "Arrivée", "km": km, "services": []})
    start = f"{(day or date.today()).isoformat()}T07:00:00"
    return {"race": name, "official_km": km, "official_dplus": dplus or 0, "start_local": start, "points": pts,
            "generic": True, "start_note": "heure de départ inconnue : 7 h supposé"}


# --------------------------------------------------------------------------- profil
@dataclass
class Prof:
    mode: str                       # "gpx" | "approx"
    dx: np.ndarray
    dz: np.ndarray
    alt0: float
    pos_m: list[float]              # position de chaque point sur le profil (m)
    alts: list[float | None]        # altitude de chaque point (GPX si disponible)
    plot: list[list[float]]         # [[km, alt], ...] pour le graphique
    lat: float
    lon: float
    snapped: list[bool] = field(default_factory=list)

    @property
    def grade(self) -> np.ndarray:
        return self.dz / self.dx

    @property
    def x0(self) -> np.ndarray:
        return np.concatenate([[0.0], np.cumsum(self.dx)[:-1]])

    @property
    def total_m(self) -> float:
        return float(self.dx.sum())


def _alts(meta: dict) -> list[float]:
    pts = meta["points"]
    known = [(p["km"], p["alt"]) for p in pts if p.get("alt") is not None]
    if not known:
        return [500.0] * len(pts)
    ks, als = zip(*known)
    return [float(p["alt"]) if p.get("alt") is not None else float(np.interp(p["km"], ks, als)) for p in pts]


def approx_profile(meta: dict) -> Prof:
    """Profil reconstruit sans GPX. Les sommes de D+ par moitié (relais) et totales sont exactes."""
    pts = sorted(meta["points"], key=lambda p: p["km"])
    km_tot = float(meta["official_km"])
    alts = _alts({"points": pts})
    kms = [float(p["km"]) for p in pts]
    halves = []                                          # [(km_fin, D+)]
    rel = meta.get("relay_split") or []
    if len(rel) >= 2 and meta.get("official_dplus"):
        tot = sum(r["km"] for r in rel)
        acc = 0.0
        for r in rel:
            acc += r["km"]
            halves.append((km_tot * acc / tot, float(r["dplus"]) * meta["official_dplus"] / sum(x["dplus"] for x in rel)))
    else:
        halves.append((km_tot, float(meta.get("official_dplus") or 0)))
    breaks = sorted(set(kms + [h[0] for h in halves[:-1]] + [km_tot]))
    pieces = [(a, b) for a, b in zip(breaks[:-1], breaks[1:]) if b - a > 1e-6]
    alt_at = lambda k: float(np.interp(k, kms, alts))     # noqa: E731
    net = [alt_at(b) - alt_at(a) for a, b in pieces]
    up = [0.0] * len(pieces)
    lo = 0.0
    for hi, dplus in halves:
        idx = [i for i, (a, b) in enumerate(pieces) if a >= lo - 1e-6 and b <= hi + 1e-6]
        L = sum(pieces[i][1] - pieces[i][0] for i in idx) or 1.0
        extra = max(0.0, dplus - sum(max(net[i], 0.0) for i in idx))
        for i in idx:
            up[i] = max(net[i], 0.0) + extra * (pieces[i][1] - pieces[i][0]) / L
        lo = hi
    dxs, dzs = [], []
    for (a, b), u, n in zip(pieces, up, net):
        L = (b - a) * 1000
        nseg = max(1, int(round(L / SEG_M)))
        dx = np.full(nseg, L / nseg)
        d = u - n
        dz = np.zeros(nseg)
        if u + d > 1e-6:
            k = max(1, int(round(L / 2000)))              # petites bosses de ~2 km
            mid = (np.arange(nseg) + 0.5) / nseg * k % 1.0
            is_up = mid < u / (u + d)
            if not is_up.any():
                is_up[0] = True
            if is_up.all() and d > 0:
                is_up[-1] = False
            dz[is_up] = u / is_up.sum()
            if d > 0:
                dz[~is_up] = -d / (~is_up).sum()
        dxs.append(dx)
        dzs.append(dz)
    plot = [[round(k, 2), round(a)] for k, a in zip(kms, alts)]
    return Prof("approx", np.concatenate(dxs), np.concatenate(dzs), alts[0], [k * 1000 for k in kms], alts, plot,
                float(meta.get("lat", 45.0)), float(meta.get("lon", 3.0)), [False] * len(pts))


def _hav(lat0, lon0, lat, lon) -> np.ndarray:
    la0, lo0, la, lo = map(np.radians, (lat0, lon0, lat, lon))
    a = np.sin((la - la0) / 2) ** 2 + np.cos(la0) * np.cos(la) * np.sin((lo - lo0) / 2) ** 2
    return 2 * 6_371_000 * np.arcsin(np.sqrt(a))


def snap_points(track, meta: dict) -> tuple[list[float], list[bool]]:
    """Position de chaque point sur le tracé : GPS (point du tracé le plus proche, dans une fenêtre autour
    du km officiel ramené à la longueur du GPX), sinon km officiel ramené à la longueur du GPX."""
    d = np.maximum.accumulate(track.dist)
    scale = d[-1] / (float(meta["official_km"]) * 1000)
    out, snapped, last = [], [], 0.0
    pts = sorted(meta["points"], key=lambda p: p["km"])
    for i, p in enumerate(pts):
        exp = float(p["km"]) * 1000 * scale
        pos, ok = exp, False
        if i == 0:
            pos = 0.0
        elif i == len(pts) - 1:
            pos = float(d[-1])
        elif p.get("lat") is not None and track.lat is not None:
            win = max(3000.0, 0.12 * exp)
            sel = np.where((d >= max(last, exp - win)) & (d <= exp + win))[0]
            if len(sel):
                dist = _hav(p["lat"], p["lon"], track.lat[sel], track.lon[sel])
                j = int(np.argmin(dist))
                if dist[j] <= 600:
                    pos, ok = float(d[sel[j]]), True
        pos = min(max(pos, last), float(d[-1]))
        out.append(pos)
        snapped.append(ok)
        last = pos
    return out, snapped


def gpx_profile(track, meta: dict) -> Prof:
    from .pacer.tracks import segment
    seg = segment(track, seg_len=SEG_M)
    pos, snapped = snap_points(track, meta)
    d = np.maximum.accumulate(track.dist)
    alt_s = np.concatenate([[0.0], np.cumsum(seg.dz)])
    marks = np.concatenate([[0.0], np.cumsum(seg.dx)])
    a0 = float(np.nanmedian(track.alt[:5])) if np.isfinite(track.alt[:5]).any() else 0.0
    alts = [round(a0 + float(np.interp(x, marks, alt_s))) for x in pos]
    step = max(1, len(marks) // 400)
    plot = [[round(float(marks[i]) / 1000, 2), round(a0 + float(alt_s[i]))] for i in range(0, len(marks), step)]
    if plot[-1][0] < round(float(marks[-1]) / 1000, 2):
        plot.append([round(float(marks[-1]) / 1000, 2), round(a0 + float(alt_s[-1]))])
    lat = float(np.nanmean(track.lat)) if track.lat is not None else float(meta.get("lat", 45.0))
    lon = float(np.nanmean(track.lon)) if track.lon is not None else float(meta.get("lon", 3.0))
    del d
    return Prof("gpx", seg.dx, seg.dz, a0, pos, alts, plot, lat, lon, snapped)


def check_gpx(track, meta: dict) -> dict:
    """Contrôle d'un GPX déposé : distance et D+ (tronçons de 50 m) contre les chiffres officiels.
    Hors tolérance (±8 % km, ±15 % D+) : accepté avec un avertissement visible."""
    from .pacer.course import validate
    km_off, dp_off = float(meta.get("official_km") or 0), float(meta.get("official_dplus") or 0)
    if not km_off:
        from .pacer.tracks import elevation_gain, segment
        return {"ok": True, "km": round(float(track.dist[-1]) / 1000, 1),
                "dplus": round(elevation_gain(segment(track, seg_len=SEG_M))), "issues": [],
                "warning": "pas de distance officielle connue pour vérifier le tracé"}
    rep = validate(track, km_off, dp_off or 1.0, tol_km=GPX_TOL_KM, tol_dplus=GPX_TOL_DPLUS if dp_off else 99)
    warn = ("Le tracé ne correspond pas bien aux chiffres officiels (" + " ; ".join(rep.issues)
            + ") : vérifie que c'est la bonne édition.") if rep.issues else ""
    return {"ok": rep.ok, "km": round(rep.km, 1), "dplus": round(rep.dplus), "issues": rep.issues, "warning": warn}


# --------------------------------------------------------------------------- modèle de vitesse
@dataclass
class Curve:
    grades: np.ndarray
    rel: np.ndarray                 # vitesse relative au plat
    beta: float
    source: str

    def at(self, g: np.ndarray) -> np.ndarray:
        return np.interp(g, self.grades, self.rel)


def prior_curve() -> Curve:
    g = np.linspace(-0.45, 0.45, 91)
    ratio = minetti_cost(0.0) / minetti_cost(g)
    tech = 1.25 - np.maximum(0.0, -g - 0.10) * 2.5       # descente raide : la technique limite, pas le cardio
    rel = np.where(g < 0, np.minimum(ratio, np.maximum(tech, 0.55)), ratio)
    return Curve(g, rel, DEFAULT_BETA, "prior physiologique (coût énergétique de Minetti 2002, descente raide plafonnée)")


_CURVE_CACHE: dict[str, tuple[float, Curve]] = {}


def athlete_curve(path: Path = PACER_PROFILE) -> Curve:
    """Courbe pente-vitesse personnelle du Race Pacer si un profil calibré existe, sinon le prior."""
    try:
        mt = path.stat().st_mtime
    except OSError:
        return prior_curve()
    hit = _CURVE_CACHE.get(str(path))
    if hit and hit[0] == mt:
        return hit[1]
    try:
        from .pacer.athlete_store import AthleteProfile
        from .pacer.model import GRADE_CENTERS
        prof = AthleteProfile.load(path)
        rel = np.asarray(prof.model.bin_speed, float) / float(prof.model.v_flat)
        cal = prof.meta.get("calibrated", "")
        c = Curve(np.asarray(GRADE_CENTERS, float), rel, float(np.clip(prof.model.beta, 0.03, 0.2)),
                  "ta courbe pente-vitesse personnelle (Race Pacer" + (f", calibrée le {cal}" if cal else "") + ")")
    except Exception:                                            # noqa: BLE001 : profil illisible -> prior
        c = prior_curve()
    _CURVE_CACHE[str(path)] = (mt, c)
    return c


def heat_factor(heat: str, hour: np.ndarray) -> np.ndarray:
    """Facteur de vitesse selon la chaleur et l'heure (les heures chaudes : 11 h - 18 h)."""
    w = np.clip((hour - 10.0) / 3.0, 0, 1) * np.clip((20.0 - hour) / 2.0, 0, 1)
    if heat == "fraiche":
        return np.full_like(hour, 1.01)
    if heat == "chaude":
        return 1.0 - (0.02 + 0.06 * w)
    return 1.0 - 0.015 * w


@dataclass
class Run:
    dt: np.ndarray                  # s par tronçon de 50 m
    t_start: np.ndarray             # s écoulées au début de chaque tronçon (arrêts compris)


def _times(dx, r, start_h, scale, beta, heat, form, stop_after, t_start, iters=3) -> Run:
    dt = dx / (scale * r)
    for _ in range(iters):
        h = t_start / 3600.0
        v = scale * r * np.power(1.0 + h, -beta) * heat_factor(heat, start_h + h) * (1.0 + form)
        dt = dx / v
        t_start = np.concatenate([[0.0], np.cumsum(dt + stop_after)[:-1]])
    return Run(dt, t_start)


def _solve(total_s, dx, r, start_h, beta, heat, stop_after) -> tuple[float, Run]:
    """Échelle de vitesse telle que le temps total (arrêts compris) vaille `total_s`."""
    moving = max(total_s - float(stop_after.sum()), 600.0)
    scale = float(np.sum(dx / r)) / moving
    t_start = np.linspace(0, total_s, len(dx), endpoint=False)
    run = Run(dx / (scale * r), t_start)
    for _ in range(10):
        run = _times(dx, r, start_h, scale, beta, heat, 0.0, stop_after, run.t_start, iters=2)
        m = float(run.dt.sum())
        scale *= m / moving
        if abs(m / moving - 1) < 2e-4:
            break
    run = _times(dx, r, start_h, scale, beta, heat, 0.0, stop_after, run.t_start, iters=2)
    run.dt *= moving / float(run.dt.sum())                       # la somme fait exactement le temps visé
    run.t_start = np.concatenate([[0.0], np.cumsum(run.dt + stop_after)[:-1]])
    return scale, run


# --------------------------------------------------------------------------- ancrage
def anchors(meta: dict, forecast: dict | None, progress_est: tuple[int, int, int] | None) -> dict:
    """(ambitieux, cible, prudent) en secondes + d'où ça vient."""
    k = (meta.get("itra_calibration") or {}).get("k_h")

    def from_fc(fc: dict, src: str) -> dict | None:
        if fc.get("median_h") and fc.get("lo_h") and fc.get("hi_h"):
            return {"lo_s": fc["lo_h"] * 3600, "median_s": fc["median_h"] * 3600, "hi_s": fc["hi_h"] * 3600,
                    "source": src, "itra_effective": fc.get("itra_effective"), "itra_range": fc.get("itra_range")}
        if k and fc.get("itra_effective") and fc.get("itra_range"):
            lo_sc, hi_sc = sorted(fc["itra_range"])
            return {"lo_s": k * 1000 / hi_sc * 3600, "median_s": k * 1000 / fc["itra_effective"] * 3600,
                    "hi_s": k * 1000 / lo_sc * 3600, "source": src + " (temps = 5,617 h × 1000 / score ITRA)",
                    "itra_effective": fc["itra_effective"], "itra_range": fc["itra_range"]}
        return None

    out = None
    if forecast:
        out = from_fc(forecast, forecast.get("source") or "ton pronostic enregistré")
    if out is None and meta.get("forecast_default"):
        out = from_fc(meta["forecast_default"], meta["forecast_default"].get("source", "pronostic"))
    if out is None and progress_est:
        lo, mid, hi = progress_est
        out = {"lo_s": float(lo), "median_s": float(mid), "hi_s": float(hi), "itra_effective": None, "itra_range": None,
               "source": "estimation de l'onglet Progrès (ta référence de vitesse × distance-effort, ±6 %)"}
    if out is None:
        return {}
    out["k_h"] = k
    if k:
        out["itra_equiv"] = {n: round(k * 1000 / (out[f"{x}_s"] / 3600)) for n, x in
                             (("ambitieux", "lo"), ("cible", "median"), ("prudent", "hi"))}
    return out


# --------------------------------------------------------------------------- simulation
def _kind(km: float, dplus: float, dminus: float) -> str:
    if km <= 0:
        return "roulant"
    if dplus / km >= 45 and dplus >= 2 * dminus:
        return "montee"
    if dminus / km >= 45 and dminus >= 2 * dplus:
        return "descente"
    return "roulant"


KIND_LABEL = {"montee": "montée", "descente": "descente", "roulant": "roulant"}


def _climbs(prof: Prof, bounds: list[int], names: list[str], dt: np.ndarray) -> list[dict]:
    alt = prof.alt0 + np.concatenate([[0.0], np.cumsum(prof.dz)])
    marks = np.concatenate([[0.0], np.cumsum(prof.dx)])
    res, lo, hi = [], 0, 0
    for i in range(1, len(alt)):
        if alt[i] > alt[hi]:
            hi = i
        elif alt[hi] - alt[i] > CLIMB_DROP_M:
            if alt[hi] - alt[lo] >= CLIMB_MIN_M:
                res.append((lo, hi))
            lo = hi = i
            continue
        if alt[i] < alt[lo]:
            lo = hi = i
    if alt[hi] - alt[lo] >= CLIMB_MIN_M:
        res.append((lo, hi))
    out = []
    for a, b in res:
        gain = float(prof.dz[a:b][prof.dz[a:b] > 0].sum())
        t = float(dt[a:b].sum())
        km0, km1 = marks[a] / 1000, marks[b] / 1000
        near = [(abs(bounds[j] - b), names[j]) for j in range(len(bounds)) if abs(marks[min(bounds[j], len(marks) - 1)] - marks[b]) <= 2000]
        name = f"Montée vers {min(near)[1]}" if near else f"Montée du km {km0:.0f}"
        out.append({"name": name, "km0": round(float(km0), 1), "km1": round(float(km1), 1), "gain_m": round(gain),
                    "net_m": round(float(alt[b] - alt[a])),
                    "grade_pct": round(float(alt[b] - alt[a]) / max(float(marks[b] - marks[a]), 1.0) * 100, 1),
                    "time_s": round(t), "vam_m_h": round(gain / t * 3600) if t > 0 else None})
    return out


def _daylight(lat: float, lon: float, start: datetime) -> dict:
    t0 = datetime(start.year, start.month, start.day, tzinfo=start.tzinfo).timestamp()
    ts = t0 + np.arange(0, 86400, 60)
    e = solar_elevation_deg(lat, lon, ts)

    def first(th):
        i = np.where(e > th)[0]
        return datetime.fromtimestamp(ts[i[0]], start.tzinfo) if len(i) else None

    def last(th):
        i = np.where(e > th)[0]
        return datetime.fromtimestamp(ts[i[-1]], start.tzinfo) if len(i) else None
    return {"dawn": first(-6.0), "sunrise": first(-0.833), "sunset": last(-0.833), "dusk": last(-6.0)}


@dataclass
class Course:
    meta: dict
    prof: Prof
    start: datetime


def build_course(meta: dict, track=None) -> Course:
    prof = gpx_profile(track, meta) if track is not None else approx_profile(meta)
    start = datetime.fromisoformat(meta["start_local"])
    return Course(meta, prof, start)


def simulate(course: Course, anchor: dict, *, heat: str = "normale", form: float = 0.0,
             target_s: float | None = None, curve: Curve | None = None) -> dict:
    t0 = time.perf_counter()
    meta, prof, start = course.meta, course.prof, course.start
    heat = heat if heat in HEATS else "normale"
    form = float(np.clip(form, -0.05, 0.05))
    curve = curve or prior_curve()
    pts = sorted(meta["points"], key=lambda p: p["km"])
    dx = prof.dx
    r = np.maximum(curve.at(prof.grade), 0.08)
    marks = np.concatenate([[0.0], np.cumsum(dx)])
    bounds = [int(np.clip(np.searchsorted(marks, pos - 1e-6), 0, len(dx))) for pos in prof.pos_m]
    bounds[0], bounds[-1] = 0, len(dx)
    for j in range(1, len(bounds)):
        bounds[j] = max(bounds[j], bounds[j - 1])
    start_h = start.hour + start.minute / 60
    hot = 0.5 if heat == "chaude" else 0.0

    def stops_for(sc: str) -> tuple[np.ndarray, list[float]]:
        st = np.zeros(len(dx))
        per = []
        for j, p in enumerate(pts):
            m = 0.0
            if 0 < j < len(pts) - 1 and set(p.get("services", [])) & AID:
                m = (float(p.get("stop_min", 1.5)) + (hot if "eau" not in p["services"] else 0)) * STOP_MULT[sc]
            per.append(m * 60)
            if m and bounds[j] > 0:
                st[bounds[j] - 1] += m * 60
        return st, per

    med = anchor["median_s"]
    totals = {"ambitieux": anchor["lo_s"], "cible": med, "prudent": anchor["hi_s"]}
    if target_s:
        totals = {k: v * target_s / med for k, v in totals.items()}
    runs: dict[str, Run] = {}
    stops: dict[str, list[float]] = {}
    for sc, _ in SCENARIOS:
        st, per = stops_for(sc)
        stops[sc] = per
        if target_s:
            _, run = _solve(totals[sc], dx, r, start_h, curve.beta, heat, st)
        else:
            scale, run0 = _solve(totals[sc], dx, r, start_h, curve.beta, "normale", st)
            run = _times(dx, r, start_h, scale, curve.beta, heat, form, st, run0.t_start, iters=4) \
                if (heat != "normale" or form) else run0
        runs[sc] = run
    cm = {sc: np.concatenate([[0.0], np.cumsum(runs[sc].dt)]) for sc in runs}

    def arrival(sc: str, j: int) -> float:
        return float(cm[sc][bounds[j]] + sum(stops[sc][:j]))

    finish = {sc: arrival(sc, len(pts) - 1) for sc, _ in SCENARIOS}
    sun = _daylight(prof.lat, prof.lon, start)
    limit_s = float(meta["time_limit_h"]) * 3600 if meta.get("time_limit_h") else None

    def cutoff_of(j: int, p: dict) -> dict | None:
        c = p.get("cutoff")
        lim = None
        if c:
            hh, mm = map(int, c["clock"].split(":"))
            lim = (datetime(start.year, start.month, start.day, hh, mm, tzinfo=start.tzinfo) - start).total_seconds()
            if lim <= 0:
                lim += 86400
            label, approx, ed = c.get("label", c["clock"]), bool(c.get("approx")), c.get("edition")
        elif j == len(pts) - 1 and limit_s:
            lim, label, approx, ed = limit_s, f"temps limite {hm(limit_s)}", True, None
        if lim is None:
            return None
        margins = {sc: round((lim - arrival(sc, j)) / 60) for sc, _ in SCENARIOS}
        m = margins["prudent"]
        return {"limit_s": round(lim), "clock": clock(start, lim), "label": label, "approx": approx, "edition": ed,
                "margin_min": margins, "status": "ok" if m >= 60 else "juste" if m >= 20 else "danger"}

    out_pts = []
    for j, p in enumerate(pts):
        svc = list(p.get("services", []))
        kind = "depart" if j == 0 else "arrivee" if j == len(pts) - 1 else (
            "eau" if svc == ["eau"] else "ravito" if set(svc) & AID else "repere")
        out_pts.append({"name": p["name"], "km": round(prof.pos_m[j] / 1000, 2), "km_official": p["km"],
                        "alt": prof.alts[j], "alt_approx": bool(p.get("alt_approx")) and prof.mode == "approx",
                        "kind": kind, "services": svc, "note": p.get("note", ""), "estimate": bool(p.get("estimate")),
                        "snapped": prof.snapped[j] if prof.snapped else False,
                        "stop_min": round(stops["cible"][j] / 60, 1),
                        "arrival": {sc: {"s": round(arrival(sc, j)), "clock": clock(start, arrival(sc, j))}
                                    for sc, _ in SCENARIOS},
                        "cutoff": cutoff_of(j, p)})
    sections = []
    g = prof.grade
    for j in range(len(pts) - 1):
        a, b = bounds[j], bounds[j + 1]
        dz = prof.dz[a:b]
        km = float(dx[a:b].sum()) / 1000
        dp, dm = float(dz[dz > 0].sum()), float(-dz[dz < 0].sum())
        kind = _kind(km, dp, dm)
        tm = {sc: float(runs[sc].dt[a:b].sum()) for sc, _ in SCENARIOS}
        terr = []
        for cat, sel in (("montee", g[a:b] > 0.05), ("roulant", np.abs(g[a:b]) <= 0.05), ("descente", g[a:b] < -0.05)):
            L = float(dx[a:b][sel].sum()) / 1000
            if L < 0.3:
                continue
            t = float(runs["cible"].dt[a:b][sel].sum())
            gain = float(dz[sel & (dz > 0)].sum()) if cat == "montee" else 0.0
            terr.append({"kind": cat, "km": round(L, 1), "time_s": round(t), "pace_s_km": round(t / L),
                         "vam_m_h": round(gain / t * 3600) if cat == "montee" and t > 0 else None})
        t_cible = tm["cible"]
        st_clock = start + timedelta(seconds=float(arrival("cible", j) + stops["cible"][j]))
        en_clock = start + timedelta(seconds=float(arrival("cible", j + 1)))
        day = ""
        if sun["sunrise"] and st_clock < sun["sunrise"]:
            day = f"avant le lever du soleil ({sun['sunrise']:%H:%M}) : frontale utile au début"
        elif sun["sunset"] and en_clock > sun["sunset"]:
            day = f"après le coucher du soleil ({sun['sunset']:%H:%M}) : frontale"
        elif heat == "chaude" and en_clock.hour >= 11 and st_clock.hour < 19:
            day = "heures chaudes : ralentis, mouille-toi, bois régulièrement"
        sections.append({
            "i": j, "from": pts[j]["name"], "to": pts[j + 1]["name"],
            "km0": round(marks[a] / 1000, 2), "km1": round(marks[b] / 1000, 2), "km": round(km, 2),
            "dplus": round(dp), "dminus": round(dm), "kind": kind, "kind_label": KIND_LABEL[kind],
            "time_s": {sc: round(v) for sc, v in tm.items()},
            "pace_s_km": round(t_cible / km) if km > 0 else None,
            "vam_m_h": round(dp / t_cible * 3600) if kind == "montee" and t_cible > 0 else None,
            "terrain": terr, "daylight": day,
            "effort": ("montée : marche active dès que ça dépasse ~15 %, garde de la marge pour la suite" if kind == "montee"
                       else "descente : relâché, cadence haute, épargne les quadriceps" if kind == "descente"
                       else "roulant : allure régulière, c'est ici que se joue le chrono"),
        })
    climbs = _climbs(prof, bounds, [p["name"] for p in pts], runs["cible"].dt)
    itra = None
    if anchor.get("k_h"):
        itra = {sc: round(anchor["k_h"] * 1000 / (finish[sc] / 3600)) for sc, _ in SCENARIOS}
    sun_txt = {k: (v.strftime("%H:%M") if v else None) for k, v in sun.items()}
    head = {"finish_s": round(finish["cible"]), "finish_clock": clock(start, finish["cible"]),
            "lo_s": round(finish["ambitieux"]), "lo_clock": clock(start, finish["ambitieux"]),
            "hi_s": round(finish["prudent"]), "hi_clock": clock(start, finish["prudent"]),
            "text": (f"Arrivée estimée {clock(start, finish['cible'])} ({hm(finish['cible'])}) — fourchette "
                     f"{clock(start, finish['ambitieux'])} – {clock(start, finish['prudent'])}")}
    notes = [f"Courbe pente-vitesse : {curve.source}.",
             f"Fatigue : vitesse × (1 + heures)^-{str(round(curve.beta, 2)).replace('.', ',')}.",
             "Temps total calé sur " + anchor["source"] + (" ; ici réparti pour faire exactement le temps visé." if target_s else ".")]
    if heat == "chaude":
        notes.append("Chaleur : jusqu'à −8 % de vitesse aux heures chaudes (11 h - 18 h), plus d'eau et de sodium.")
    elif heat == "fraiche":
        notes.append("Temps frais : +1 % de vitesse, moins d'eau.")
    if form and not target_s:
        notes.append(f"Forme du jour : {form * 100:+.0f} % de vitesse.".replace("+-", "−"))
    return {
        "mode": prof.mode, "headline": head,
        "scenarios": [{"id": sc, "label": lbl, "finish_s": round(finish[sc]), "finish_clock": clock(start, finish[sc]),
                       "itra_equiv": itra[sc] if itra else None} for sc, lbl in SCENARIOS],
        "points": out_pts, "sections": sections, "climbs": climbs,
        "profile": prof.plot, "total_km": round(prof.total_m / 1000, 2),
        "daylight": {**sun_txt, "note": (f"Départ {start:%H:%M}, lever du soleil {sun_txt['sunrise']} (aube civile "
                                         f"{sun_txt['dawn']}), coucher {sun_txt['sunset']}.")},
        "model_notes": notes, "conditions": {"heat": heat, "form": form if not target_s else 0.0,
                                             "target_s": round(target_s) if target_s else None},
        "_runs": runs, "_bounds": bounds, "_stops": stops,
        "compute_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def nutrition_legs(sim: dict, scenario: str = "cible") -> list[dict]:
    """Tronçons entre deux ravitaillements (départ, ravitos, points d'eau, arrivée) pour le plan nutrition."""
    pts = sim["points"]
    idx = [j for j, p in enumerate(pts) if p["kind"] in ("depart", "ravito", "eau", "arrivee")]
    legs = []
    for a, b in zip(idx[:-1], idx[1:]):
        pa, pb = pts[a], pts[b]
        start_s = pa["arrival"][scenario]["s"] + pa["stop_min"] * 60
        legs.append({"from": pa["name"], "to": pb["name"], "km0": pa["km"], "km1": pb["km"],
                     "km": round(pb["km"] - pa["km"], 2), "duration_s": pb["arrival"][scenario]["s"] - start_s,
                     "start_s": start_s, "aid_at_start": pa["kind"] in ("ravito", "eau"),
                     "water_only": pa["kind"] == "eau"})
    return legs


def parse_target(x) -> float | None:
    """« 540 » (minutes), « 9:00 », « 9h00 », « 9 h » -> secondes."""
    if x is None or x == "":
        return None
    s = str(x).strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})[:h](\d{0,2})", s)
    if m:
        v = int(m.group(1)) * 3600 + int(m.group(2) or 0) * 60
    elif re.fullmatch(r"\d+(\.\d+)?", s):
        v = float(s) * 60
    else:
        raise ValueError("temps visé : « 9:00 » ou un nombre de minutes")
    if not 1800 <= v <= 72 * 3600:
        raise ValueError("temps visé hors limites")
    return float(v)


__all__ = ["find_course", "generic_course", "approx_profile", "gpx_profile", "check_gpx", "athlete_curve",
           "prior_curve", "anchors", "build_course", "simulate", "nutrition_legs", "parse_target", "goal_key", "clock", "hm"]
