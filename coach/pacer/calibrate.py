"""Calibrer un profil athlète à partir de ses activités (FIT, GPX horodatés, flux Strava JSON).

python -m coach.pacer.calibrate --activities mes_sorties_reunion/ --out data/athletes/moi.json \\
       --hr-rest 40 --hr-max 199 --race-effort 3.3:176.8 --race-effort 13.1:155.8 --backtest

Conseil : ne mettre dans le dossier QUE des sorties représentatives du terrain de
la course visée (sur tes données : un modèle appris en Loire se trompait de 23 %
sur l'UTOI).
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import numpy as np

from .athlete_store import AthleteProfile
from .calibration import fit_effort, loo_backtest
from .model import fit_athlete
from .strava_io import load_strava_json
from .tracks import _still_mask, load_fit, load_gpx, segment


def load_activity(p: Path):
    ext = p.suffix.lower()
    if ext == ".fit":
        return load_fit(p)
    if ext == ".gpx":
        tr = load_gpx(p)
        if tr.t is None:
            raise ValueError("GPX sans horodatage (tracé de course ?)")
        return tr
    if ext == ".json":
        return load_strava_json(p)
    raise ValueError("format non géré")


def mean_stop_minutes(tracks, min_stop_s: float = 120) -> float | None:
    """Durée moyenne des arrêts >= 2 min observés dans les activités."""
    stops = []
    for tr in tracks:
        still = _still_mask(tr.t, np.maximum.accumulate(tr.dist), 0.15, 30)
        dt = np.diff(tr.t, prepend=tr.t[0])
        cur = 0.0
        for s, d in zip(still, dt):
            if s:
                cur += d
            elif cur:
                stops.append(cur)
                cur = 0.0
    big = [s for s in stops if s >= min_stop_s]
    return float(np.mean(big) / 60) if big else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Calibrage du profil athlète (Race Pacer)")
    ap.add_argument("--activities", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--hr-rest", type=float, required=True)
    ap.add_argument("--hr-max", type=float, required=True)
    ap.add_argument("--race-effort", action="append", default=[],
                    help="durée_h:FC_moyenne d'une course menée à fond (au moins 2)")
    ap.add_argument("--backtest", action="store_true", help="leave-one-out (plus lent)")
    a = ap.parse_args(argv)

    tracks, skipped = [], []
    for p in sorted(a.activities.iterdir()):
        try:
            tracks.append(load_activity(p))
        except Exception as e:
            skipped.append(f"{p.name}: {e}")
    segs = {tr.name: segment(tr) for tr in tracks}
    print(f"{len(tracks)} activités chargées, {len(skipped)} ignorées")
    for s in skipped:
        print("  -", s)

    model = fit_athlete(list(segs.values()))
    with_hr = [s for s in segs.values() if s.hr is not None]
    effort = fit_effort(with_hr, model) if len(with_hr) >= 3 else None
    if effort is None:
        from .calibration import EffortModel
        effort = EffortModel(0.0, 140.0, 0.05)
        print("⚠ moins de 3 activités avec FC : effet de l'intensité non estimé")
    bias, bt_txt = 0.0, "non fait"
    if a.backtest and len(segs) >= 4:
        res = loo_backtest({k: v for k, v in segs.items() if v.hr is not None})
        errs = np.array([r["err"] for r in res])
        bias = float(-errs.mean())
        bt_txt = (f"leave-one-out {len(res)} sorties : erreur médiane {np.median(np.abs(errs)):.0%}, "
                  f"biais {errs.mean():+.0%}, couverture {sum(r['covered'] for r in res)}/{len(res)}")
        print("backtest :", bt_txt)
    efforts = [tuple(map(float, e.split(":"))) for e in a.race_effort]
    if len(efforts) < 2:
        print("⚠ moins de 2 courses de référence : la FC tenable ne pourra pas être estimée (utiliser --target-hr)")
    stop = mean_stop_minutes(tracks) or 5.0
    prof = AthleteProfile(model, effort, a.hr_rest, a.hr_max, efforts, round(stop, 1), round(bias, 3),
                          meta={"calibrated": str(date.today()), "activities": len(tracks), "backtest": bt_txt})
    prof.save(a.out)
    print(f"profil -> {a.out} | v_plat {model.v_flat*3.6:.1f} km/h | fatigue {model.beta:.3f} | "
          f"vitesse ∝ FC^{effort.gamma:.2f} | arrêt moyen {stop:.1f} min | recalage {bias:+.0%}")
    for n in model.notes:
        print("  ⚠", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
