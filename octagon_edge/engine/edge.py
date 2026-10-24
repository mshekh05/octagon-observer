"""Edge, expected value, stake sizing, and blending model with market."""
from __future__ import annotations

import math
from dataclasses import dataclass


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def blend(p_model: float | None, p_market: float | None, model_weight: float,
          llm_adjust: float = 0.0, max_llm_adjust: float = 0.03) -> float | None:
    """Blend in log-odds space; the LLM nudge is clamped to +/- max_llm_adjust."""
    if p_market is None and p_model is None:
        return None
    if p_market is None:
        p = p_model
    elif p_model is None:
        p = p_market
    else:
        p = sigmoid(model_weight * logit(p_model) + (1 - model_weight) * logit(p_market))
    adj = max(-max_llm_adjust, min(max_llm_adjust, llm_adjust))
    return min(max(p + adj, 0.01), 0.99)


def scaled_weight(weight: float, n: int, full_at: int = 3) -> float:
    """A source's blend weight, scaled down when its sample size `n` is thin.

    One picker on an undercard fight shouldn't move a blend as hard as a full
    panel does; weight ramps linearly up to `weight` once n reaches `full_at`.
    """
    return weight * min(1.0, n / full_at) if full_at > 0 else weight


def edge(p: float, decimal: float) -> float:
    """Probability edge over the price's implied probability."""
    return p - 1 / decimal


def expected_value(p: float, decimal: float) -> float:
    """EV per 1 unit staked."""
    return p * decimal - 1


def kelly_fraction(p: float, decimal: float) -> float:
    b = decimal - 1
    if b <= 0:
        return 0.0
    f = (b * p - (1 - p)) / b
    return max(f, 0.0)


@dataclass(frozen=True)
class Stake:
    fraction: float  # of bankroll
    amount: float


def stake(p: float, decimal: float, bankroll: float, kelly_mult: float = 0.25,
          max_pct: float = 0.03) -> Stake:
    frac = min(kelly_fraction(p, decimal) * kelly_mult, max_pct)
    return Stake(fraction=frac, amount=round(bankroll * frac, 2))
