"""Race Pacer V0 : modèle vitesse = f(pente) x fatigue(t) x nuit x terrain.

Pourquoi pas ARIMA : on ne prolonge pas une série temporelle, on transpose
une capacité (vitesse selon la pente, à FC contrôlée) sur un profil inconnu
de 180 km. C'est une régression par segment + propagation d'incertitude.

1. Courbe pente -> vitesse PERSONNELLE, à FC plafonnée (ta stratégie : marche
   active en montée pour tenir la FC). Bins de pente, médiane empirique,
   rétrécie vers un prior physiologique (coût énergétique de Minetti et al.
   2002) là où tes données sont rares (pentes > 25 % autour de Barbechat...).
2. Fatigue : v(t) = v_frais x (1 + t_h)^(-beta), beta estimé sur tes sorties
   longues, combiné à un prior large si tu as peu d'efforts > 8 h.
3. Monte Carlo : forme du jour, bruit par bloc de 10 km, beta, facteur nuit,
   facteur terrain, durée des arrêts -> P10 / P50 / P90 par point de passage.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from .tracks import Segments

GRADE_EDGES = np.round(np.arange(-0.45, 0.4501, 0.03), 4)
GRADE_CENTERS = (GRADE_EDGES[:-1] + GRADE_EDGES[1:]) / 2


def minetti_cost(i: np.ndarray | float) -> np.ndarray:
    """Coût énergétique de la course en pente (J/kg/m), Minetti et al. 2002."""
    i = np.clip(i, -0.45, 0.45)
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


def prior_speed(g: np.ndarray, v_flat: float, down_cap: float) -> np.ndarray:
    """Puissance métabolique constante ; en descente, plafond technique."""
    ratio = minetti_cost(0.0) / minetti_cost(g)
    return v_flat * np.where(g < 0, np.minimum(ratio, down_cap), ratio)


@dataclass
class AthleteModel:
    v_flat: float
    bin_speed: np.ndarray            # vitesse "fraîche" par bin de pente
    bin_n: np.ndarray                # nb de segments observés par bin
    beta: float
    beta_sd: float
    resid_sd: float                  # écart-type log des résidus
    down_cap: float
    hr_cap: float | None
    n_long_segments: int = 0         # segments utilisés pour la fatigue (> 1 h)
    notes: list[str] = field(default_factory=list)
    block_sd: float | None = None    # variabilité log par bloc de 10 km (mesurée)

    def fresh_speed(self, g: np.ndarray) -> np.ndarray:
        return np.interp(g, GRADE_CENTERS, self.bin_speed)


def time_consistent_speed(v: np.ndarray, trim: float = 0.05) -> float:
    """Vitesse "équivalente temps" d'un ensemble de segments de même longueur.

    Le temps total vaut Σ dx/v : l'estimateur sans biais est la moyenne
    (tronquée) de l'ALLURE 1/v, pas la médiane de la vitesse. Sur tes sorties
    réelles, la médiane sous-estimait systématiquement les temps de 5 à 15 %
    (inégalité de Jensen, forte variabilité en terrain technique).
    """
    pace = np.sort(1.0 / v)
    k = int(len(pace) * trim)
    core = pace[k:len(pace) - k] if len(pace) - 2 * k >= 5 else pace
    return float(1.0 / core.mean())


def _pool(segs: list[Segments], hr_cap: float | None):
    g, v, t, k, x = [], [], [], [], []
    for i, s in enumerate(segs):
        m = s.moving.copy()
        if hr_cap is not None and s.hr is not None:
            m &= s.hr <= hr_cap
        g.append(s.grade[m]); v.append(s.speed[m]); t.append(s.t0[m] / 3600)
        k.append(np.full(int(m.sum()), i)); x.append(s.x0[m])
    return tuple(np.concatenate(a) for a in (g, v, t, k, x))


def fit_athlete(segs: list[Segments], *, hr_cap: float | None = None,
                beta_prior: tuple[float, float] = (0.10, 0.05),
                down_cap: float = 1.25, k_prior: float = 20.0,
                iterations: int = 4) -> AthleteModel:
    """Estimation alternée courbe <-> fatigue (quelques itérations suffisent).

    `beta_prior` = (moyenne, écart-type) : HYPOTHÈSE à remplacer dès que tes
    ultras passés sont chargés (ils dominent alors l'estimation).
    """
    g, v, t, trk, x0 = _pool(segs, hr_cap)
    if len(g) < 200:
        raise ValueError(f"Trop peu de segments exploitables ({len(g)}) : ajoute des activités.")
    beta, beta_sd = beta_prior
    notes: list[str] = []
    long_mask = t > 1.0
    for _ in range(iterations):
        v_fresh = v * (1 + t) ** beta                 # on "retire" la fatigue
        flat = np.abs(g) < 0.02
        if flat.sum() < 20:
            raise ValueError("Pas assez de segments plats pour estimer la vitesse de référence.")
        v_flat = time_consistent_speed(v_fresh[flat])
        prior = prior_speed(GRADE_CENTERS, v_flat, down_cap)
        idx = np.clip(np.digitize(g, GRADE_EDGES) - 1, 0, len(GRADE_CENTERS) - 1)
        bin_speed, bin_n = prior.copy(), np.zeros(len(GRADE_CENTERS))
        for b in range(len(GRADE_CENTERS)):
            sel = idx == b
            n = int(sel.sum())
            bin_n[b] = n
            if n:
                bin_speed[b] = (n * time_consistent_speed(v_fresh[sel]) + k_prior * prior[b]) / (n + k_prior)
        # Fatigue : log(v / v_frais(g)) = a_k - beta_k * log(1 + t), PAR ACTIVITÉ k.
        # Les segments d'une même sortie sont corrélés : l'incertitude doit venir
        # de la dispersion ENTRE sorties longues, pas du nombre de segments
        # (sinon beta_sd est absurdement petit et les intervalles trop étroits).
        betas = []
        for k in np.unique(trk):
            sel = (trk == k) & long_mask
            if sel.sum() < 60 or t[trk == k].max() < 3.0:
                continue
            y = np.log(v[sel] / np.interp(g[sel], GRADE_CENTERS, bin_speed))
            x = np.log1p(t[sel])
            X = np.column_stack([np.ones_like(x), -x])
            betas.append(np.linalg.lstsq(X, y, rcond=None)[0][1])
        if len(betas) >= 3:
            # Pondération par la durée : les ultras renseignent mieux la fin de course.
            w_dur = np.array([t[trk == k].max() for k in np.unique(trk)
                              if ((trk == k) & long_mask).sum() >= 60 and t[trk == k].max() >= 3.0])
            b_hat = float(np.average(betas, weights=w_dur))
            se = float(np.std(betas, ddof=1) / math.sqrt(len(betas)))
            w0, w1 = 1 / beta_prior[1] ** 2, 1 / max(se, 1e-3) ** 2
            beta = (w0 * beta_prior[0] + w1 * b_hat) / (w0 + w1)
            beta_sd = math.sqrt(1 / (w0 + w1))
    fitted = np.interp(g, GRADE_CENTERS, bin_speed) * (1 + t) ** (-beta)
    resid_sd = float(np.std(np.log(v / fitted)))
    # Variabilité à l'échelle de 10 km (celle qui compte sur 180 km) : écart
    # temps prévu / temps réel par bloc, mesuré sur tes sorties.
    blk_res = []
    for kk in np.unique(trk):
        for b in np.unique((x0[trk == kk] // 10_000).astype(int)):
            sel = (trk == kk) & ((x0 // 10_000).astype(int) == b)
            if sel.sum() >= 150:                       # >= ~4 km de segments de 25 m
                blk_res.append(np.log(np.sum(1 / fitted[sel]) / np.sum(1 / v[sel])))
    block_sd = float(np.std(blk_res)) if len(blk_res) >= 8 else resid_sd / 2
    n_long_tracks = sum(1 for k in np.unique(trk) if t[trk == k].max() >= 3.0)
    if n_long_tracks < 3:
        notes.append(f"Fatigue = prior uniquement ({n_long_tracks} sortie(s) > 3 h).")
    if t.max() < 8:
        notes.append(f"Effort le plus long observé : {t.max():.1f} h -> extrapolation forte sur 180 km.")
    sparse = [f"{c:+.0%}" for c, n in zip(GRADE_CENTERS, bin_n) if n < 30 and abs(c) >= 0.15]
    if sparse:
        notes.append("Pentes peu observées (prior dominant) : " + ", ".join(sparse))
    return AthleteModel(v_flat, bin_speed, bin_n, float(beta), float(beta_sd), resid_sd,
                        down_cap, hr_cap, int(long_mask.sum()), notes, block_sd)


# --------------------------------------------------------------------------- #
# Nuit : élévation solaire (approximation NOAA, ~1°)
# --------------------------------------------------------------------------- #
def solar_elevation_deg(lat: float, lon: float, epoch_s: np.ndarray) -> np.ndarray:
    d = epoch_s / 86400.0 + 2440587.5 - 2451545.0
    g = np.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    L = np.radians((q + 1.915 * np.sin(g) + 0.020 * np.sin(2 * g)) % 360)
    e = np.radians(23.439 - 0.00000036 * d)
    ra = np.arctan2(np.cos(e) * np.sin(L), np.cos(L))
    dec = np.arcsin(np.sin(e) * np.sin(L))
    gmst_h = (18.697374558 + 24.06570982441908 * d) % 24
    ha = np.radians(gmst_h * 15 + lon) - ra
    la = math.radians(lat)
    return np.degrees(np.arcsin(np.sin(la) * np.sin(dec) + np.cos(la) * np.cos(dec) * np.cos(ha)))


# --------------------------------------------------------------------------- #
# Simulation Monte Carlo sur le parcours
# --------------------------------------------------------------------------- #
@dataclass
class Checkpoint:
    name: str
    km: float
    stop_min: float = 0.0        # arrêt prévu (ravito, base vie)
    stop_sd_min: float = 0.0


@dataclass
class RaceAssumptions:
    night_factor: tuple[float, float] = (0.92, 0.04)    # HYPOTHÈSE à calibrer (sorties de nuit)
    terrain_factor: tuple[float, float] = (1.00, 0.08)  # HYPOTHÈSE : technicité vs tes terrains
    day_form_sd: float = 0.04
    effort_factor: tuple[float, float] = (1.0, 0.0)     # (FC visée / FC réf.)^gamma, calibré
    block_km: float = 10.0
    night_sun_deg: float = -6.0                         # crépuscule civil


@dataclass
class Prediction:
    checkpoints: list[dict]
    finish_hours: dict[str, float]
    n_sims: int
    moving_share_night: float
    finish_samples_h: np.ndarray | None = None


def simulate(course: Segments, model: AthleteModel, start: datetime,
             checkpoints: list[Checkpoint], assumptions: RaceAssumptions = RaceAssumptions(),
             n: int = 2000, seed: int = 0) -> Prediction:
    rng = np.random.default_rng(seed)
    a = assumptions
    beta = np.clip(rng.normal(model.beta, model.beta_sd, n), 0.0, 0.4)
    night = np.clip(rng.normal(*a.night_factor, n), 0.6, 1.1)
    terrain = np.clip(rng.normal(*a.terrain_factor, n), 0.6, 1.3)
    form = np.exp(rng.normal(0, a.day_form_sd, n))
    effort = np.clip(rng.normal(*a.effort_factor, n), 0.5, 1.5)
    n_blocks = int(course.x0[-1] // (a.block_km * 1000)) + 1
    bsd = model.block_sd if model.block_sd is not None else model.resid_sd / 2
    block = np.exp(rng.normal(0, bsd, (n_blocks, n)))

    v_fresh = model.fresh_speed(course.grade)
    lat0 = float(np.nanmean(course.lat)) if np.isfinite(course.lat).any() else 0.0
    lon0 = float(np.nanmean(course.lon)) if np.isfinite(course.lon).any() else 0.0
    t0_epoch = start.timestamp()

    cps = sorted(checkpoints, key=lambda c: c.km)
    stops = [np.maximum(rng.normal(c.stop_min, c.stop_sd_min, n), 0) * 60 for c in cps]
    arrivals = np.zeros((len(cps), n))
    elapsed = np.zeros(n)
    night_time, moving_time = np.zeros(n), np.zeros(n)
    ci = 0
    for i in range(len(course.dx)):
        seg_end_km = (course.x0[i] + course.dx[i]) / 1000
        is_night = solar_elevation_deg(lat0, lon0, t0_epoch + elapsed) < a.night_sun_deg
        v = (v_fresh[i] * (1 + elapsed / 3600) ** (-beta) * terrain * form * effort
             * block[int(course.x0[i] // (a.block_km * 1000))] * np.where(is_night, night, 1.0))
        dt = course.dx[i] / v
        elapsed += dt
        moving_time += dt
        night_time += np.where(is_night, dt, 0.0)
        while ci < len(cps) and seg_end_km >= cps[ci].km - 1e-6:
            arrivals[ci] = elapsed
            elapsed = elapsed + stops[ci]
            ci += 1
    while ci < len(cps):              # points au-delà du tracé
        arrivals[ci] = elapsed
        ci += 1

    def pct(x):
        return {f"p{q}": float(np.percentile(x, q)) / 3600 for q in (10, 50, 90)}

    rows = []
    for c, arr in zip(cps, arrivals):
        p = pct(arr)
        rows.append({"name": c.name, "km": c.km, **{k: round(v, 2) for k, v in p.items()},
                     "clock_p50": (start + timedelta(hours=p["p50"])).strftime("%a %H:%M")})
    return Prediction(rows, {k: round(v, 2) for k, v in pct(elapsed).items()}, n,
                      float(np.mean(night_time / moving_time)), elapsed / 3600)
