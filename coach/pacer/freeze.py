"""Pré-enregistrement d'une prévision : on fige, on horodate, on hache.

Devant un jury, une prévision publiée APRÈS la course ne prouve rien. Le
fichier gelé (et son empreinte SHA-256, envoyée par mail ou commitée avant
le départ) rend le backtest honnête.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from .model import AthleteModel, Prediction, RaceAssumptions
from .tracks import Segments


def course_fingerprint(course: Segments) -> str:
    arr = np.round(np.column_stack([course.dx, course.dz]), 1).tobytes()
    return hashlib.sha256(arr).hexdigest()[:16]


def freeze(pred: Prediction, model: AthleteModel, assumptions: RaceAssumptions,
           course: Segments, race: str, out: Path) -> dict:
    payload = {
        "race": race,
        "frozen_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "course_fingerprint": course_fingerprint(course),
        "model": {"v_flat_ms": round(model.v_flat, 3), "beta": round(model.beta, 4),
                  "beta_sd": round(model.beta_sd, 4), "resid_sd": round(model.resid_sd, 4),
                  "hr_cap": model.hr_cap, "down_cap": model.down_cap, "notes": model.notes},
        "assumptions": asdict(assumptions),
        "prediction": {"checkpoints": pred.checkpoints, "finish_hours": pred.finish_hours,
                       "n_sims": pred.n_sims, "night_share": round(pred.moving_share_night, 3)},
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    payload["sha256"] = hashlib.sha256(blob.encode()).hexdigest()
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return payload


def backtest(frozen: dict, actual_hours: dict[str, float]) -> dict:
    """Après course : erreur relative par point et couverture de l'intervalle P10-P90."""
    rows, inside = [], 0
    for cp in frozen["prediction"]["checkpoints"]:
        if cp["name"] not in actual_hours:
            continue
        act = actual_hours[cp["name"]]
        ok = cp["p10"] <= act <= cp["p90"]
        inside += ok
        rows.append({"name": cp["name"], "pred_p50": cp["p50"], "actual": act,
                     "err_pct": round(100 * (cp["p50"] - act) / act, 1), "in_p10_p90": ok})
    return {"rows": rows, "coverage_p10_p90": inside / max(len(rows), 1),
            "mape": float(np.mean([abs(r["err_pct"]) for r in rows])) if rows else float("nan")}


def verify(path: str | Path) -> tuple[bool, str]:
    """Recalcule l'empreinte d'une prévision gelée : toute retouche la change."""
    d = json.loads(Path(path).read_text())
    claimed = d.pop("sha256", None)
    actual = hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return claimed == actual, actual


if __name__ == "__main__":
    import sys
    ok, h = verify(sys.argv[-1])
    print(("✅ intacte" if ok else "❌ MODIFIÉE") + f" — sha256 recalculée : {h}")
    sys.exit(0 if ok else 1)
