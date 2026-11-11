"""Profil athlète calibré, sérialisé en JSON (lisible, versionnable, sans pickle).

Contient uniquement des PARAMÈTRES dérivés (courbe pente-vitesse, fatigue,
effet de la FC), pas les données brutes : c'est ce qui voyage entre le
calibrage (données de l'athlète) et la prévision.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .calibration import EffortModel, HrDurationCurve, fit_hr_duration
from .model import AthleteModel


@dataclass
class AthleteProfile:
    model: AthleteModel
    effort: EffortModel
    hr_rest: float
    hr_max: float
    race_efforts: list[tuple[float, float]]           # [(durée h, FC moyenne)] de courses à fond
    default_stop_min: float = 5.0
    backtest_bias: float = 0.0                        # >0 : le modèle était trop rapide
    meta: dict = field(default_factory=dict)

    @property
    def hr_curve(self) -> HrDurationCurve:
        return fit_hr_duration(self.race_efforts, self.hr_rest, self.hr_max)

    def save(self, path: str | Path) -> None:
        m = asdict(self.model)
        m["bin_speed"] = [round(float(x), 4) for x in self.model.bin_speed]
        m["bin_n"] = [int(x) for x in self.model.bin_n]
        doc = {"meta": self.meta, "hr_rest": self.hr_rest, "hr_max": self.hr_max,
               "race_efforts": [list(e) for e in self.race_efforts],
               "default_stop_min": self.default_stop_min, "backtest_bias": self.backtest_bias,
               "model": m, "effort": asdict(self.effort)}
        Path(path).write_text(json.dumps(doc, indent=1, ensure_ascii=False))

    @classmethod
    def load(cls, path: str | Path) -> AthleteProfile:
        d = json.loads(Path(path).read_text())
        m = d["model"]
        m["bin_speed"], m["bin_n"] = np.array(m["bin_speed"]), np.array(m["bin_n"])
        return cls(AthleteModel(**m), EffortModel(**d["effort"]), d["hr_rest"], d["hr_max"],
                   [tuple(e) for e in d["race_efforts"]], d.get("default_stop_min", 5.0),
                   d.get("backtest_bias", 0.0), d.get("meta", {}))
