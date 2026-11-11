"""Prévision de course, prête à geler — ce que ferait le coach sans rien demander.

python -m coach.pacer.predict --race "diagonale des fous" --year 2026 \\
       --athlete data/athletes/pilote.json            [--target-hr 140] [--stop-min 5]

- parcours + ravitos : catalogue de courses (validé contre les chiffres officiels)
- FC tenable : courbe FC-durée de l'athlète, résolue en point fixe avec la durée prévue
- arrêt moyen : valeur du profil (mesurée) sauf si l'athlète en donne une
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .athlete_store import AthleteProfile
from .course import CatalogSource, resolve
from .freeze import freeze
from .model import RaceAssumptions, simulate
from .tracks import elevation_gain, segment

ROOT = Path(__file__).resolve().parents[2]


def predict(course, profile: AthleteProfile, start: datetime, hr: float, stop_min: float,
            n: int = 3000, seed: int = 0):
    f, fsd = profile.effort.factor(hr)
    a = RaceAssumptions(night_factor=(0.92, 0.04), terrain_factor=(1.0, 0.05), day_form_sd=0.04,
                        effort_factor=(f / (1 + profile.backtest_bias), fsd))
    seg = segment(course.track, seg_len=50)
    return simulate(seg, profile.model, start, course.checkpoints(stop_min), a, n=n, seed=seed), a, seg


def solve_hr(course, profile, start, stop_min, t0: float = 36.0, iters: int = 6) -> tuple[float, list]:
    """Point fixe : FC tenable(durée) <-> durée(FC)."""
    curve, T, hist = profile.hr_curve, t0, []
    for _ in range(iters):
        hr = curve.hr(T)
        p, _, _ = predict(course, profile, start, hr, stop_min, n=600, seed=1)
        T = p.finish_hours["p50"]
        hist.append((round(hr, 1), round(T, 2)))
    return hr, hist


def main(argv=None, root: Path | None = None) -> int:
    """`root` : racine où chercher data/races et data/athletes (dépôt par défaut ; dossier temporaire en test)."""
    root = root or ROOT
    ap = argparse.ArgumentParser(description="Prévision de course (Race Pacer)")
    ap.add_argument("--race", default="diagonale des fous")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--athlete", type=Path, default=root / "data/athletes/pilote.json")
    ap.add_argument("--target-hr", type=float, default=None)
    ap.add_argument("--stop-min", type=float, default=None)
    ap.add_argument("--sims", type=int, default=3000)
    ap.add_argument("--out", type=Path, default=Path("prediction.json"))
    a = ap.parse_args(argv)

    catalog = root / "data/races"
    refs = CatalogSource(catalog).find(a.race, a.year)
    if not refs:
        print(f"⚠ '{a.race} {a.year}' absente du catalogue ({catalog}).")
        return 1
    meta = json.loads(Path(refs[0]).read_text())
    course, log = resolve(a.race, a.year, [CatalogSource(catalog)], meta["official_km"],
                          meta["official_dplus"], commercial=False)
    print("\n".join(log))
    if course is None:
        return 1
    profile = AthleteProfile.load(a.athlete)
    start = datetime.fromisoformat(meta["start_local"])
    stop = a.stop_min if a.stop_min is not None else profile.default_stop_min
    if a.target_hr is None:
        hr, hist = solve_hr(course, profile, start, stop)
        print("FC tenable (point fixe FC <-> durée) :", hist[-1][0], "bpm")
    else:
        hr = a.target_hr
    pred, assumptions, seg = predict(course, profile, start, hr, stop, n=a.sims)
    frozen = freeze(pred, profile.model, assumptions, seg, f"{meta['race']} {meta['year']}", a.out)
    n_aid = sum(p.is_aid for p in course.points)
    print(f"\n{meta['race']} {meta['year']} : {course.report.km:.1f} km, D+ {elevation_gain(seg):.0f} m | "
          f"{n_aid} ravitos x {stop:g} min | FC {hr:.0f} bpm | part de nuit {pred.moving_share_night:.0%}")
    scale = meta["official_km"] / course.report.km
    print(f"{'Point':<30}{'km':>6}{'P10':>8}{'P50':>8}{'P90':>8}   passage P50")
    for c in pred.checkpoints:
        pt = next(p for p in course.points if p.name == c["name"])
        if pt.is_aid or c is pred.checkpoints[-1]:
            print(f"{c['name']:<30}{c['km']*scale:>6.1f}{c['p10']:>7.1f}h{c['p50']:>7.1f}h{c['p90']:>7.1f}h   {c['clock_p50']}")
    print(f"\nPrévision gelée -> {a.out} | sha256 {frozen['sha256'][:16]}…")
    print(f"Vérifier plus tard : python -m coach.pacer.freeze {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
