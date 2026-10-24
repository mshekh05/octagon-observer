"""Multi-leg parlay combinations built from single-fight edges.

Each leg is one side of one fight that already shows a positive edge on its own
(see engine.edge). A parlay multiplies legs' probabilities and decimal odds
across *different* fights; it is ranked the same way a single bet is: by edge
over the combined price's implied probability.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

from . import edge as E


@dataclass(frozen=True)
class Leg:
    fight_id: int
    fight_key: str
    name: str
    side: int
    p: float
    decimal: float
    book: str
    single_edge: float


@dataclass(frozen=True)
class Parlay:
    legs: tuple[Leg, ...]
    decimal: float
    p: float
    edge: float
    ev: float
    stake: float
    stake_pct: float


def build_legs(fights: list[dict], min_edge: float = 0.0) -> list[Leg]:
    """fights: dicts with id, key (a vs b), a, b, p_final and a 2-item 'sides'
    list (book/decimal/edge per side), as stored in Prediction.details."""
    legs: list[Leg] = []
    for f in fights:
        p_final = f.get("p_final")
        sides = f.get("sides") or []
        if p_final is None or len(sides) != 2:
            continue
        for side, sv in enumerate(sides):
            if sv.get("decimal") is None or sv.get("edge") is None or sv["edge"] <= min_edge:
                continue
            p = p_final if side == 0 else 1 - p_final
            name = f["a"] if side == 0 else f["b"]
            legs.append(Leg(f["id"], f"{f['a']} vs {f['b']}", name, side, p, sv["decimal"], sv["book"], sv["edge"]))
    return legs


def generate_parlays(legs: list[Leg], sizes: tuple[int, ...] = (2, 3), bankroll: float = 0,
                     kelly_fraction: float = 0.25, max_stake_pct: float = 0.03,
                     top_n: int = 15) -> list[Parlay]:
    """Every combo of `sizes` legs, one leg per distinct fight, ranked by EV."""
    by_fight: dict[int, list[Leg]] = {}
    for leg in legs:
        by_fight.setdefault(leg.fight_id, []).append(leg)
    fight_ids = list(by_fight)

    out: list[Parlay] = []
    for size in sizes:
        if size > len(fight_ids):
            continue
        for combo_fights in itertools.combinations(fight_ids, size):
            for combo_legs in itertools.product(*(by_fight[fid] for fid in combo_fights)):
                p = 1.0
                dec = 1.0
                for leg in combo_legs:
                    p *= leg.p
                    dec *= leg.decimal
                ev = E.expected_value(p, dec)
                if ev <= 0:
                    continue
                st = E.stake(p, dec, bankroll, kelly_fraction, max_stake_pct)
                out.append(Parlay(combo_legs, dec, p, E.edge(p, dec), ev, st.amount, st.fraction))
    out.sort(key=lambda pl: pl.ev, reverse=True)
    return out[:top_n]
