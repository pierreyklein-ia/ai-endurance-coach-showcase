from coach.classifier import RuleClassifier, ThresholdRouter, rule_consent
from coach.schemas import Route


class Flaky:
    labels = ("noise", "coaching", "red_flag")
    def predict_proba(self, text):
        raise TimeoutError


class Overconfident:
    labels = ("noise", "coaching", "red_flag")
    def predict_proba(self, text):
        return {"noise": 0.90, "coaching": 0.02, "red_flag": 0.08}


def test_router_falls_back_to_rules_when_provider_down():
    r = ThresholdRouter(Flaky(), fallback=RuleClassifier())
    assert r("ok merci") == Route.NOISE and r("douleur au genou") == Route.RED_FLAG


def test_noise_needs_near_certainty():
    assert ThresholdRouter(Overconfident())("ok") == Route.COACHING   # 0,90 < 0,97


def test_consent():
    assert rule_consent("Ouais vas-y") == "yes"
    assert rule_consent("👍") == "yes"
    assert rule_consent("non merci") == "no"
    assert rule_consent("oui mais pas samedi") == "unclear"
    assert rule_consent("bof") == "unclear"
