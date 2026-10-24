"""Odds conversions and bookmaker-margin removal (de-vig)."""
from __future__ import annotations

from statistics import median


def american_to_decimal(american: float) -> float:
    if american == 0:
        raise ValueError("American odds cannot be 0")
    if american > 0:
        return 1 + american / 100
    return 1 + 100 / -american


def decimal_to_american(decimal: float) -> int:
    if decimal <= 1:
        raise ValueError("Decimal odds must be > 1")
    if decimal >= 2:
        return round((decimal - 1) * 100)
    return round(-100 / (decimal - 1))


def implied_prob(decimal: float) -> float:
    return 1 / decimal


def prob_to_decimal(p: float) -> float:
    return 1 / p


def overround(decimals: list[float]) -> float:
    """Sum of implied probabilities minus 1 (the bookmaker margin)."""
    return sum(1 / d for d in decimals) - 1


def devig_multiplicative(decimals: list[float]) -> list[float]:
    raw = [1 / d for d in decimals]
    total = sum(raw)
    return [r / total for r in raw]


def devig_power(decimals: list[float], tol: float = 1e-10) -> list[float]:
    """Power method: find k so that sum(p_i ** k) == 1.

    Shifts more of the margin onto longshots than the multiplicative method,
    which matches the favourite-longshot bias seen in fight markets.
    """
    raw = [1 / d for d in decimals]
    if abs(sum(raw) - 1) < tol:
        return raw
    lo, hi = 0.5, 3.0
    for _ in range(200):
        k = (lo + hi) / 2
        s = sum(r ** k for r in raw)
        if abs(s - 1) < tol:
            break
        # sum(p^k) decreases as k grows (p < 1)
        if s > 1:
            lo = k
        else:
            hi = k
    probs = [r ** k for r in raw]
    total = sum(probs)
    return [p / total for p in probs]


def consensus_fair_prob(book_prices: dict[str, tuple[float, float]]) -> float | None:
    """Median de-vigged probability for side A across books.

    book_prices maps book -> (decimal_a, decimal_b).
    """
    fair = [devig_power([a, b])[0] for a, b in book_prices.values() if a > 1 and b > 1]
    return median(fair) if fair else None


def best_price(book_prices: dict[str, tuple[float, float]], side: int) -> tuple[str, float] | None:
    """Book offering the highest decimal price for side 0 (A) or 1 (B)."""
    if not book_prices:
        return None
    book, prices = max(book_prices.items(), key=lambda kv: kv[1][side])
    return book, prices[side]
