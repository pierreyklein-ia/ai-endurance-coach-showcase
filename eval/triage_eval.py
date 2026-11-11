"""Banc d'évaluation du triage — la "matrice de confusion en santé sportive" de la spec.

Usage : python -m eval.triage_eval            (baseline règles)
        -> brancher JevClassifier dès l'accès early access et comparer.

Métriques, par ordre d'importance métier :
1. missed_signal_rate : message porteur de signal classé NOISE (doit valoir 0)
2. noise_capture      : part du bruit réellement filtrée (= économie)
3. red_flag_recall    : informatif (red_flag et coaching suivent le même chemin
                        d'extraction ; la sécurité se joue dans les garde-fous)
4. coût pondéré       : matrice de coût asymétrique
5. ECE                : calibration de P(noise) — n'a de sens que pour un
                        classifieur probabiliste (JEV), pas pour les règles.
"""
from __future__ import annotations

import json
from pathlib import Path

from coach.classifier import CalibratedClassifier, RuleClassifier, ThresholdRouter

LABELS = ("noise", "coaching", "red_flag")
# coût[vrai][prédit] — un signal classé bruit coûte 100x un appel LLM gaspillé
COST = {
    "noise": {"noise": 0, "coaching": 1, "red_flag": 1},
    "coaching": {"noise": 100, "coaching": 0, "red_flag": 0.5},
    "red_flag": {"noise": 100, "coaching": 0.5, "red_flag": 0},
}
COST_PER_EXCHANGE_EUR = 0.0208   # finops.py, archi proposée


def ece(probs: list[float], truth: list[int], bins: int = 10) -> float:
    total, err = len(probs), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probs) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if idx:
            conf = sum(probs[i] for i in idx) / len(idx)
            acc = sum(truth[i] for i in idx) / len(idx)
            err += len(idx) / total * abs(conf - acc)
    return err


def evaluate(clf: CalibratedClassifier, path: Path, **router_kw) -> dict:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    router = ThresholdRouter(clf, **router_kw)
    cm = {t: {p: 0 for p in LABELS} for t in LABELS}
    probs, truth, errors = [], [], []
    for r in rows:
        pred = router(r["text"]).value
        cm[r["label"]][pred] += 1
        probs.append(clf.predict_proba(r["text"]).get("noise", 0.0))
        truth.append(int(r["label"] == "noise"))
        if pred != r["label"]:
            errors.append((r["text"], r["label"], pred))
    n_signal = sum(sum(cm[t].values()) for t in ("coaching", "red_flag"))
    n_noise = sum(cm["noise"].values())
    return {
        "n": len(rows),
        "confusion": cm,
        "missed_signal_rate": (cm["coaching"]["noise"] + cm["red_flag"]["noise"]) / n_signal,
        "noise_capture": cm["noise"]["noise"] / n_noise,
        "red_flag_recall": cm["red_flag"]["red_flag"] / sum(cm["red_flag"].values()),
        "weighted_cost": sum(COST[t][p] * cm[t][p] for t in LABELS for p in LABELS),
        "ece_noise": ece(probs, truth),
        "errors": errors,
    }


def report(name: str, res: dict) -> None:
    print(f"== {name} (n={res['n']})")
    print("   vrai \\ prédit   " + "  ".join(f"{p:>9}" for p in LABELS))
    for t in LABELS:
        print(f"   {t:<15}" + "  ".join(f"{res['confusion'][t][p]:>9}" for p in LABELS))
    print(f"   signal classé bruit : {res['missed_signal_rate']:.1%}   (cible : 0 %)")
    print(f"   bruit capturé       : {res['noise_capture']:.1%}")
    print(f"   rappel red_flag     : {res['red_flag_recall']:.1%}")
    print(f"   coût pondéré        : {res['weighted_cost']:.1f}")
    print(f"   ECE P(noise)        : {res['ece_noise']:.3f}")
    for text, t, p in res["errors"]:
        print(f"     ✗ [{t} -> {p}] {text}")


if __name__ == "__main__":
    report("Règles (baseline)", evaluate(RuleClassifier(),
                                         Path(__file__).parent / "triage_dataset.jsonl"))
