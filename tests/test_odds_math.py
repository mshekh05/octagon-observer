import pytest

from octagon_edge.engine.odds_math import (american_to_decimal, consensus_fair_prob, decimal_to_american,
                                           devig_multiplicative, devig_power, overround)


def test_american_decimal_roundtrip():
    assert american_to_decimal(-200) == pytest.approx(1.5)
    assert american_to_decimal(150) == pytest.approx(2.5)
    assert american_to_decimal(100) == pytest.approx(2.0)
    for a in (-400, -150, -110, 100, 120, 350):
        assert decimal_to_american(american_to_decimal(a)) == a


def test_devig_sums_to_one_and_removes_margin():
    d = [american_to_decimal(-110), american_to_decimal(-110)]
    assert overround(d) == pytest.approx(0.0476, abs=1e-3)
    assert devig_multiplicative(d) == pytest.approx([0.5, 0.5])
    assert devig_power(d) == pytest.approx([0.5, 0.5])


def test_power_devig_shades_longshot_more():
    d = [american_to_decimal(-400), american_to_decimal(300)]
    mult, power = devig_multiplicative(d), devig_power(d)
    assert sum(power) == pytest.approx(1)
    assert power[1] < mult[1]  # longshot gets less probability under the power method
    assert power[0] == pytest.approx(0.785, abs=0.01)


def test_consensus_is_median_of_books():
    books = {"x": (1.8, 2.1), "y": (1.9, 2.0), "z": (2.5, 1.6)}
    assert consensus_fair_prob(books) == pytest.approx(devig_power([1.9, 2.0])[0])
    assert consensus_fair_prob({}) is None
