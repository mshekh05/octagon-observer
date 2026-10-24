import pytest

from octagon_edge.engine.parlay import build_legs, generate_parlays


def _fight(fid, a, b, p_final, edge_a, edge_b, dec_a=2.0, dec_b=2.0):
    return {
        "id": fid, "a": a, "b": b, "p_final": p_final,
        "sides": [
            {"book": "BookA", "decimal": dec_a, "edge": edge_a, "ev": None, "stake": 0, "stake_pct": 0},
            {"book": "BookB", "decimal": dec_b, "edge": edge_b, "ev": None, "stake": 0, "stake_pct": 0},
        ],
    }


def test_build_legs_filters_by_edge():
    fights = [
        _fight(1, "A", "B", 0.6, edge_a=0.08, edge_b=-0.3),
        _fight(2, "C", "D", 0.4, edge_a=-0.2, edge_b=0.05),
        _fight(3, "E", "F", 0.5, edge_a=-0.1, edge_b=-0.1),  # no leg qualifies
    ]
    legs = build_legs(fights, min_edge=0.0)
    names = {leg.name for leg in legs}
    assert names == {"A", "D"}


def test_generate_parlays_combines_distinct_fights_and_ranks_by_ev():
    fights = [
        _fight(1, "A", "B", 0.6, edge_a=0.08, edge_b=-0.3, dec_a=1.9),
        _fight(2, "C", "D", 0.6, edge_a=0.08, edge_b=-0.3, dec_a=1.9),
    ]
    legs = build_legs(fights)
    combos = generate_parlays(legs, sizes=(2,), bankroll=1000)
    assert len(combos) == 1
    combo = combos[0]
    assert {leg.fight_id for leg in combo.legs} == {1, 2}
    assert combo.decimal == pytest.approx(1.9 * 1.9)
    assert combo.p == pytest.approx(0.6 * 0.6)
    assert combo.ev > 0


def test_generate_parlays_never_combines_two_legs_of_the_same_fight():
    # Fight 1 has two qualifying legs (both sides positive edge); fight 2 has one.
    # No combo should ever use both of fight 1's legs together.
    fights = [
        _fight(1, "A", "B", 0.55, edge_a=0.05, edge_b=0.01, dec_a=2.0, dec_b=3.0),
        _fight(2, "C", "D", 0.6, edge_a=0.08, edge_b=-0.3, dec_a=1.9),
    ]
    legs = build_legs(fights, min_edge=0.0)
    combos = generate_parlays(legs, sizes=(2,), bankroll=1000)
    for combo in combos:
        assert len({leg.fight_id for leg in combo.legs}) == len(combo.legs)
