from finops import NAIVE, OPTIMISED, unit_economics


def test_optimised_beats_naive_and_stays_profitable():
    o, n = unit_economics(OPTIMISED, 500), unit_economics(NAIVE, 500)
    assert o["llm"] < n["llm"] / 4
    assert o["gross_margin"] > 0.8
