import pytest

from octagon_edge.engine.edge import blend, edge, expected_value, kelly_fraction, stake


def test_edge_and_ev():
    assert edge(0.55, 2.0) == pytest.approx(0.05)
    assert expected_value(0.55, 2.0) == pytest.approx(0.10)


def test_kelly_and_caps():
    assert kelly_fraction(0.55, 2.0) == pytest.approx(0.10)
    assert kelly_fraction(0.40, 2.0) == 0.0
    st = stake(0.55, 2.0, bankroll=1000, kelly_mult=0.25, max_pct=0.03)
    assert st.fraction == pytest.approx(0.025) and st.amount == 25.0
    capped = stake(0.80, 2.0, bankroll=1000, kelly_mult=0.25, max_pct=0.03)
    assert capped.fraction == 0.03


def test_blend_leans_to_market_and_clamps_llm():
    p = blend(0.30, 0.60, model_weight=0.35)
    assert 0.45 < p < 0.60
    assert blend(None, 0.6, 0.35) == pytest.approx(0.6)
    assert blend(0.4, None, 0.35) == pytest.approx(0.4)
    assert blend(0.5, 0.5, 0.35, llm_adjust=0.5) == pytest.approx(0.53)
    assert blend(0.5, 0.5, 0.35, llm_adjust=-0.5) == pytest.approx(0.47)
