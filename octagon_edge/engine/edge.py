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


EXPERT_SHARE_CAP = 0.75


def pick_share_prob(share: float, cap: float = EXPERT_SHARE_CAP) -> float:
    """Turn a share of handicappers picking A into a bounded probability-like signal.

    A pick share is not a win probability: 10/10 handicappers backing a -300
    favourite says "they agree with the market", not "100%". Clamping keeps a
    unanimous panel from swamping the blend in log-odds space.
    """
    return min(max(share, 1 - cap), cap)


def youth_adjust(p: float, age_gap_years: float | None, per_5y: float) -> float:
    """Shift toward the younger fighter: bookmakers under-price youth.

    `age_gap_years` is A's age minus B's. The backtest against historical prices fits
    about -0.07 log-odds per 5 years older on top of the market-and-model blend
    (consistent across 2019/2021/2023 holdouts; Miller & Nichols 2026 report the same bias).
    """
    if not age_gap_years or not per_5y:
        return p
    return sigmoid(logit(p) - per_5y * age_gap_years / 5)


def final_prob(p_model: float | None, p_market: float | None, model_weight: float,
               llm_adjust: float = 0.0, p_experts: float | None = None,
               expert_weight: float = 0.0, max_llm_adjust: float = 0.03,
               age_gap_years: float | None = None, youth_per_5y: float = 0.0) -> float | None:
    """The engine's one blend, shared by the analysis and the alert engine."""
    p = blend(p_model, p_market, model_weight, llm_adjust=llm_adjust, max_llm_adjust=max_llm_adjust)
    if p is not None and p_market is not None:  # the youth bias is a market correction
        p = youth_adjust(p, age_gap_years, youth_per_5y)
    if p is not None and p_experts is not None and expert_weight > 0:
        p = blend(p, pick_share_prob(p_experts), 1 - expert_weight)
    return p


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


def disagreement_shrink(p_model: float | None, p_market: float | None, scale: float = 0.10) -> float:
    """Stake multiplier for model/market disagreement (after Baker & McHale 2013).

    Kelly assumes our probability is right. The further the stats model sits from the
    market, the more likely it is the model that's wrong, so the stake shrinks:
    1.0 when they agree, 0.5 at a 10-point gap, 0.2 at 20 points.
    """
    if p_model is None or p_market is None:
        return 1.0
    return 1 / (1 + ((p_model - p_market) / scale) ** 2)


def stake(p: float, decimal: float, bankroll: float, kelly_mult: float = 0.25,
          max_pct: float = 0.03, shrink: float = 1.0) -> Stake:
    frac = min(kelly_fraction(p, decimal) * kelly_mult * shrink, max_pct)
    return Stake(fraction=frac, amount=round(bankroll * frac, 2))
