"""Calibration sur données réelles : effet de l'intensité (FC) + backtest.

Constat sur les sorties Réunion de l'athlète pilote : la même courbe
pente-vitesse sous-estime la course (UTOI, FC 156) et surestime les sorties
"balade" (FC 120) -> il faut une variable d'effort. On l'estime au niveau
SORTIE (FC moyenne), pas au niveau segment, pour éviter le retard de la FC
sur l'effort dans les côtes.

    vitesse ∝ (FC_moyenne / FC_ref) ** gamma
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from .model import AthleteModel, Checkpoint, RaceAssumptions, fit_athlete, simulate
from .tracks import Segments

_DAY = RaceAssumptions(night_factor=(1.0, 0.0), terrain_factor=(1.0, 0.0), day_form_sd=0.0)
_T0 = datetime(2026, 6, 1, 8, 0, tzinfo=timezone(timedelta(hours=4)))


def mean_hr(s: Segments) -> float:
    m = s.moving
    return float(np.average(s.hr[m], weights=s.dt[m]))


def moving_hours(s: Segments) -> float:
    return float(s.dt[s.moving].sum() / 3600)


def as_course(s: Segments, lat: float = -21.0, lon: float = 55.4) -> Segments:
    return Segments(s.x0, s.dx, s.dz, s.grade, np.full(len(s.dx), lat), np.full(len(s.dx), lon))


def predict_hours(model: AthleteModel, s: Segments, assumptions: RaceAssumptions = _DAY,
                  n: int = 200, seed: int = 1) -> dict[str, float]:
    p = simulate(as_course(s), model, _T0, [Checkpoint("fin", s.x0[-1] / 1000 + 0.1)],
                 assumptions, n=n, seed=seed)
    return p.finish_hours


@dataclass
class EffortModel:
    gamma: float
    hr_ref: float
    resid_sd: float           # dispersion entre sorties non expliquée par la FC

    def factor(self, target_hr: float) -> tuple[float, float]:
        f = (target_hr / self.hr_ref) ** self.gamma
        return f, f * self.resid_sd


def fit_effort(segs: list[Segments], model: AthleteModel) -> EffortModel:
    x = np.log([mean_hr(s) for s in segs])
    y = np.log([predict_hours(model, s, n=100)["p50"] / moving_hours(s) for s in segs])
    X = np.column_stack([np.ones_like(x), x - x.mean()])
    c = np.linalg.lstsq(X, y, rcond=None)[0]
    dof = max(len(segs) - 2, 1)
    return EffortModel(float(c[1]), float(np.exp(x.mean())),
                       float(np.sqrt(np.sum((y - X @ c) ** 2) / dof)))


def loo_backtest(segs: dict[str, Segments]) -> list[dict]:
    """Leave-one-out : on prédit chaque sortie avec un modèle appris sur les autres,
    à la FC moyenne réellement tenue (comme on le fera avec la FC visée en course)."""
    out = []
    for key, s in segs.items():
        others = [v for k, v in segs.items() if k != key]
        m = fit_athlete(others)
        e = fit_effort(others, m)
        f, fsd = e.factor(mean_hr(s))
        p = predict_hours(m, s, RaceAssumptions(night_factor=(1.0, 0.0), terrain_factor=(1.0, 0.0),
                                                day_form_sd=0.0, effort_factor=(f, fsd)))
        a = moving_hours(s)
        out.append({"activity": key, "hr": mean_hr(s), "actual_h": a, **p,
                    "err": p["p50"] / a - 1, "covered": p["p10"] <= a <= p["p90"]})
    return out


# --------------------------------------------------------------------------- #
# FC tenable selon la durée (efforts de course maximaux de l'athlète)
# --------------------------------------------------------------------------- #
@dataclass
class HrDurationCurve:
    """FC moyenne tenable = a - b * ln(durée en h), ajustée sur les COURSES de
    l'athlète (efforts maximaux), exprimée aussi en % de FC de réserve (Karvonen)."""
    a: float
    b: float
    hr_rest: float
    hr_max: float

    def hr(self, hours: float) -> float:
        return float(self.a - self.b * np.log(hours))

    def hrr_pct(self, hours: float) -> float:
        return (self.hr(hours) - self.hr_rest) / (self.hr_max - self.hr_rest)


def fit_hr_duration(efforts: list[tuple[float, float]], hr_rest: float, hr_max: float) -> HrDurationCurve:
    """`efforts` = [(durée h, FC moyenne)] de courses menées à fond (>= 2 points)."""
    h = np.log([e[0] for e in efforts])
    y = np.array([e[1] for e in efforts], float)
    slope, intercept = np.polyfit(h, y, 1)
    return HrDurationCurve(float(intercept), float(-slope), hr_rest, hr_max)
