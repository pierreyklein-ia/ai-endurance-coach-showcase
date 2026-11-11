"""Race Pacer V0 en ligne de commande.

python -m coach.pacer.cli \\
    --activities ~/Suunto/exports/          # .fit (et/ou .gpx horodatés)
    --course diag2026.gpx \\
    --checkpoints checkpoints.csv            # name,km,stop_min,stop_sd_min
    --start 2026-10-15T22:00:00+04:00 \\
    --hr-cap 150 --out prediction_diag2026.json
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from .freeze import freeze
from .model import Checkpoint, RaceAssumptions, fit_athlete, simulate
from .tracks import elevation_gain, load_fit, load_gpx, segment


def load_activities(folder: Path):
    segs, skipped = [], []
    for p in sorted(folder.iterdir()):
        try:
            if p.suffix.lower() == ".fit":
                tr = load_fit(p)
            elif p.suffix.lower() == ".gpx":
                tr = load_gpx(p)
                if tr.t is None:
                    raise ValueError("GPX sans horodatage")
            else:
                continue
            segs.append(segment(tr))
        except Exception as e:           # un fichier corrompu ne doit pas tout bloquer
            skipped.append(f"{p.name}: {e}")
    return segs, skipped


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--activities", type=Path, required=True)
    ap.add_argument("--course", type=Path, required=True)
    ap.add_argument("--checkpoints", type=Path, required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--hr-cap", type=float, default=None)
    ap.add_argument("--beta-prior", type=float, nargs=2, default=(0.10, 0.05))
    ap.add_argument("--night", type=float, nargs=2, default=(0.92, 0.04))
    ap.add_argument("--terrain", type=float, nargs=2, default=(1.00, 0.08))
    ap.add_argument("--sims", type=int, default=3000)
    ap.add_argument("--out", type=Path, default=Path("prediction.json"))
    a = ap.parse_args(argv)

    segs, skipped = load_activities(a.activities)
    model = fit_athlete(segs, hr_cap=a.hr_cap, beta_prior=tuple(a.beta_prior))
    course = segment(load_gpx(a.course))
    with open(a.checkpoints) as f:
        cps = [Checkpoint(r["name"], float(r["km"]), float(r.get("stop_min") or 0),
                          float(r.get("stop_sd_min") or 0)) for r in csv.DictReader(f)]
    assumptions = RaceAssumptions(night_factor=tuple(a.night), terrain_factor=tuple(a.terrain))
    pred = simulate(course, model, datetime.fromisoformat(a.start), cps, assumptions, n=a.sims)
    frozen = freeze(pred, model, assumptions, course, a.course.stem, a.out)

    print(f"{len(segs)} activités chargées, {len(skipped)} ignorées")
    for s in skipped:
        print("  -", s)
    print(f"Parcours : {course.x0[-1]/1000:.1f} km, D+ {elevation_gain(course):.0f} m")
    print(f"Modèle : v_plat {model.v_flat*3.6:.2f} km/h, fatigue beta {model.beta:.3f} ± {model.beta_sd:.3f}")
    for n in model.notes:
        print("  ⚠", n)
    print(f"\n{'Point':<22}{'km':>6}{'P10':>8}{'P50':>8}{'P90':>8}  heure P50")
    for c in pred.checkpoints:
        print(f"{c['name']:<22}{c['km']:>6.1f}{c['p10']:>7.1f}h{c['p50']:>7.1f}h{c['p90']:>7.1f}h  {c['clock_p50']}")
    print(f"\nPrévision gelée -> {a.out}  (sha256 {frozen['sha256'][:16]}…)")


if __name__ == "__main__":
    main()
