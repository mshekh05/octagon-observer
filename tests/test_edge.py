import math

import pytest

from octagon_edge.engine.edge import (blend, disagreement_shrink, edge, expected_value, final_prob, kelly_fraction,
                                      pick_share_prob, scaled_weight, stake)


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


def test_scaled_weight_ramps_up_with_sample_size():
    assert scaled_weight(0.15, 0) == 0.0
    assert scaled_weight(0.15, 1) == pytest.approx(0.05)
    assert scaled_weight(0.15, 3) == pytest.approx(0.15)
    assert scaled_weight(0.15, 10) == pytest.approx(0.15)  # never exceeds the configured weight


def test_unanimous_expert_panel_cannot_swamp_the_blend():
    # 10/10 handicappers on a 75% favourite: agreement with the market, not "100%".
    assert final_prob(0.75, 0.75, 0.35, p_experts=1.0, expert_weight=0.15) == pytest.approx(0.75)
    # On a coin-flip fight the same panel is only a nudge.
    p = final_prob(0.5, 0.5, 0.35, p_experts=1.0, expert_weight=0.15)
    assert 0.5 < p < 0.56
    assert pick_share_prob(0.0) == pytest.approx(0.25) and pick_share_prob(0.6) == 0.6


def test_youth_adjustment_favours_the_younger_fighter_only_with_a_market():
    older_a = final_prob(0.5, 0.5, 0.17, age_gap_years=5, youth_per_5y=0.07)
    assert older_a == pytest.approx(1 / (1 + math.exp(0.07)))
    assert final_prob(0.5, 0.5, 0.17, age_gap_years=-5, youth_per_5y=0.07) == pytest.approx(1 - older_a)
    assert final_prob(0.5, 0.5, 0.17, age_gap_years=None, youth_per_5y=0.07) == 0.5
    assert final_prob(0.5, None, 0.17, age_gap_years=5, youth_per_5y=0.07) == 0.5  # model alone already has age


def test_stake_shrinks_when_model_and_market_disagree():
    assert disagreement_shrink(0.6, 0.6) == 1.0
    assert disagreement_shrink(0.7, 0.6) == pytest.approx(0.5)
    assert disagreement_shrink(0.4, 0.6) == pytest.approx(0.2)
    assert disagreement_shrink(None, 0.6) == 1.0
    full = stake(0.55, 2.0, bankroll=1000, kelly_mult=0.25, max_pct=0.03)
    half = stake(0.55, 2.0, bankroll=1000, kelly_mult=0.25, max_pct=0.03, shrink=0.5)
    assert half.fraction == pytest.approx(full.fraction / 2)


def test_blend_leans_to_market_and_clamps_llm():
    p = blend(0.30, 0.60, model_weight=0.35)
    assert 0.45 < p < 0.60
    assert blend(None, 0.6, 0.35) == pytest.approx(0.6)
    assert blend(0.4, None, 0.35) == pytest.approx(0.4)
    assert blend(0.5, 0.5, 0.35, llm_adjust=0.5) == pytest.approx(0.53)
    assert blend(0.5, 0.5, 0.35, llm_adjust=-0.5) == pytest.approx(0.47)
