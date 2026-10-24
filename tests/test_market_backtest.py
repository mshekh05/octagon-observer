import numpy as np
import pandas as pd
import pytest

from octagon_edge.engine import market_backtest as MB
from octagon_edge.ingest.odds_history import match


def _rows():
    return pd.DataFrame([
        {"url": "u1", "date": pd.Timestamp("2024-03-02"), "fighter_a": "Movsar Evloev", "fighter_b": "Arnold Allen"},
        {"url": "u2", "date": pd.Timestamp("2024-03-02"), "fighter_a": "Jiri Prochazka", "fighter_b": "Aleksandar Rakic"},
    ])


def _odds(**over):
    base = {"date": pd.Timestamp("2024-03-03"), "gender": "MALE", "country": "USA",
            "r_ko_dec": None, "b_ko_dec": None, "r_sub_dec": None, "b_sub_dec": None,
            "r_decision_dec": None, "b_decision_dec": None}
    return pd.DataFrame([{**base, **over}])


def test_match_orients_prices_to_fighter_a_even_when_red_is_b():
    # Red corner is fighter_b here, and the date is a day off (time zones).
    odds = _odds(r="Arnold Allen", b="Movsar Evloev", r_dec=3.0, b_dec=1.4)
    m = match(_rows(), odds)
    assert list(m["url"]) == ["u1"]
    assert m.iloc[0]["dec_a"] == 1.4 and m.iloc[0]["dec_b"] == 3.0
    assert m.iloc[0]["p_market"] > 0.65


def test_match_uses_surnames_for_spelling_variants():
    odds = _odds(r="Jiří Procházka", b="Aleksandar Rakić", r_dec=1.8, b_dec=2.1)
    m = match(_rows(), odds)
    assert list(m["url"]) == ["u2"] and m.iloc[0]["dec_a"] == 1.8


def test_blend_coefs_recover_a_market_only_world():
    rng = np.random.default_rng(1)
    p_market = rng.uniform(0.2, 0.8, 4000)
    y = (rng.uniform(size=4000) < p_market).astype(int)
    p_model = rng.uniform(0.2, 0.8, 4000)  # pure noise
    a, b = MB.blend_coefs(p_model, p_market, y)
    assert abs(a) < 0.15 and b == pytest.approx(1.0, abs=0.2)


def test_simulate_flat_bets_and_gap_filter():
    p = np.array([0.6, 0.5])
    dec_a = np.array([2.0, 1.9])
    dec_b = np.array([2.0, 1.9])
    y = np.array([1, 0])
    sim = MB.simulate(p, dec_a, dec_b, y, "t", threshold=0.04)
    assert sim.n_bets == 1 and sim.roi == pytest.approx(1.0)  # one bet on A at 2.0, won
    gated = MB.simulate(p, dec_a, dec_b, y, "t", threshold=0.04, max_gap=0.05, p_market=np.array([0.5, 0.5]))
    assert gated.n_bets == 0
