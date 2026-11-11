from pathlib import Path

from coach.classifier import RuleClassifier
from eval.triage_eval import evaluate


def test_no_signal_is_ever_classified_as_noise():
    res = evaluate(RuleClassifier(), Path("eval/triage_dataset.jsonl"))
    assert res["missed_signal_rate"] == 0.0
    assert res["noise_capture"] >= 0.9
