"""Kalshi and Polymarket public market data: free, near-live fight probabilities.

Both are read-only public endpoints with no API key. Their exact UFC market
layout is not documented for this use, so parsing is defensive and every
match is checked against both fighters' names.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

from ..config import DATA_DIR
from .card import Card
from .names import mentions

log = logging.getLogger(__name__)

KALSHI_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
KALSHI_EVENTS_URL = "https://api.elections.kalshi.com/trade-api/v2/events"
KALSHI_SERIES = ("KXUFCFIGHT",)
SERIES_CACHE = DATA_DIR / "kalshi_series.json"
POLY_URL = "https://gamma-api.polymarket.com/events"


@dataclass
class MarketProb:
    source: str
    p_a: float  # probability fighter A wins
    detail: str = ""


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _kalshi_price(m: dict) -> float | None:
    """Mid of yes bid/ask in 0..1; falls back to last trade."""
    bid = _num(m.get("yes_bid_dollars")) if m.get("yes_bid_dollars") is not None else None
    ask = _num(m.get("yes_ask_dollars")) if m.get("yes_ask_dollars") is not None else None
    if bid is None and m.get("yes_bid") is not None:
        bid = _num(m["yes_bid"]) / 100
    if ask is None and m.get("yes_ask") is not None:
        ask = _num(m["yes_ask"]) / 100
    if bid and ask and 0 < bid <= ask < 1:
        return (bid + ask) / 2
    last = _num(m.get("last_price_dollars"))
    if last is None and m.get("last_price") is not None:
        last = _num(m["last_price"]) / 100
    return last if last and 0 < last < 1 else None


def parse_kalshi(markets: list[dict], card: Card) -> dict[str, MarketProb]:
    """Kalshi lists one yes/no market per fighter ("Will X win?")."""
    per_fight: dict[str, dict[int, float]] = {}
    for m in markets:
        text = " ".join(str(m.get(k, "")) for k in ("title", "subtitle", "yes_sub_title"))
        who = m.get("yes_sub_title") or ""
        price = _kalshi_price(m)
        if price is None:
            continue
        for f in card.fights:
            if not (mentions(f.a, text) or mentions(f.b, text)):
                continue
            side = f.side_of(who) if who else None
            if side is None:
                hits = [s for s, n in ((0, f.a), (1, f.b)) if mentions(n, text)]
                # A title naming both fighters is ambiguous without yes_sub_title.
                side = hits[0] if len(hits) == 1 else None
            if side is not None:
                per_fight.setdefault(f.key, {})[side] = price
            break
    out = {}
    for key, sides in per_fight.items():
        if 0 in sides and 1 in sides:
            total = sides[0] + sides[1]
            out[key] = MarketProb("Kalshi", sides[0] / total, f"{sides[0]:.2f}/{sides[1]:.2f}")
        elif 0 in sides:
            out[key] = MarketProb("Kalshi", sides[0], f"{sides[0]:.2f} (A only)")
        else:
            out[key] = MarketProb("Kalshi", 1 - sides[1], f"{sides[1]:.2f} (B only)")
    return out


def parse_polymarket(events: list[dict], card: Card) -> dict[str, MarketProb]:
    out = {}
    for ev in events:
        for m in ev.get("markets", []):
            try:
                outcomes = json.loads(m.get("outcomes") or "[]")
                prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
            except (ValueError, TypeError):
                continue
            if len(outcomes) != 2 or len(prices) != 2 or m.get("closed"):
                continue
            found = card.find(outcomes[0], outcomes[1])
            if not found:
                continue
            fight, first_is_a = found
            p0, p1 = prices
            if p0 + p1 <= 0:
                continue
            p_first = p0 / (p0 + p1)
            out[fight.key] = MarketProb("Polymarket", p_first if first_is_a else 1 - p_first,
                                        f"{p0:.2f}/{p1:.2f}")
    return out


def _paged(http: httpx.Client, url: str, params: dict, key: str, pages: int = 5) -> list[dict]:
    items, cursor = [], None
    for _ in range(pages):
        resp = http.get(url, params={**params, **({"cursor": cursor} if cursor else {})})
        resp.raise_for_status()
        body = resp.json()
        items += body.get(key, [])
        cursor = body.get("cursor")
        if not cursor:
            break
    return items


def discover_kalshi_ufc(http: httpx.Client) -> tuple[list[dict], set[str]]:
    """Open events whose title or series mentions UFC/MMA; returns (markets, series tickers)."""
    events = _paged(http, KALSHI_EVENTS_URL, {"status": "open", "limit": 200,
                                              "with_nested_markets": "true"}, "events", pages=10)
    markets, series = [], set()
    for ev in events:
        label = f"{ev.get('title', '')} {ev.get('series_ticker', '')} {ev.get('event_ticker', '')}".upper()
        if "UFC" in label or "MMA" in label:
            series.add(ev.get("series_ticker", ""))
            markets += ev.get("markets", [])
    return markets, series - {""}


def fetch_kalshi(card: Card, http: httpx.Client) -> dict[str, MarketProb]:
    series = list(dict.fromkeys(KALSHI_SERIES + _cached_series()))
    markets: list[dict] = []
    for s in series:
        markets += _paged(http, KALSHI_URL, {"series_ticker": s, "status": "open", "limit": 1000}, "markets")
    found = parse_kalshi(markets, card)
    if not found:
        log.info("Kalshi: no fights in %s; searching open events for UFC markets", series)
        markets, discovered = discover_kalshi_ufc(http)
        if discovered:
            _cache_series(discovered)
            log.info("Kalshi: found UFC series %s", sorted(discovered))
        found = parse_kalshi(markets, card)
    return found


def _cached_series() -> tuple[str, ...]:
    try:
        return tuple(json.loads(SERIES_CACHE.read_text()))
    except (FileNotFoundError, ValueError):
        return ()


def _cache_series(series: set[str]) -> None:
    SERIES_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SERIES_CACHE.write_text(json.dumps(sorted(set(_cached_series()) | series)))


# ---------- props (Kalshi) ----------

KALSHI_PROP_SERIES = {"distance": "KXUFCDISTANCE", "method": "KXUFCMOV"}
METHODS = (("KO", "ko"), ("SUB", "sub"), ("DEC", "dec"))  # keyword in yes_sub_title -> outcome code


def _event_code(event_ticker: str) -> str:
    """'KXUFCMOV-26OCT24YANDVA' -> '26OCT24YANDVA': shared by a fight's winner, method and distance markets."""
    return event_ticker.split("-", 1)[-1]


def parse_kalshi_props(fight_markets: list[dict], prop_markets: dict[str, list[dict]],
                       card: Card) -> dict[str, dict]:
    """fight key -> {"distance": P(goes the distance), "method": {"a_ko": p, ..., "draw": p}}.

    Method prices are normalised to sum to 1 across the seven outcomes (each side's
    KO/TKO, submission and decision, plus draw/no contest), so they read as fair chances.
    """
    code_to_fight = {}
    for m in fight_markets:
        who = m.get("yes_sub_title") or ""
        for f in card.fights:
            if who and f.side_of(who) is not None:
                code_to_fight[_event_code(m["event_ticker"])] = f
                break
    out: dict[str, dict] = {}
    for m in prop_markets.get("distance", []):
        f = code_to_fight.get(_event_code(m["event_ticker"]))
        price = _kalshi_price(m)
        if f and price is not None:
            out.setdefault(f.key, {})["distance"] = price
    raw: dict[str, dict[str, float]] = {}
    for m in prop_markets.get("method", []):
        f = code_to_fight.get(_event_code(m["event_ticker"]))
        price = _kalshi_price(m) or 0.0
        label = m.get("yes_sub_title") or ""
        if not f:
            continue
        if "draw" in label.lower():
            raw.setdefault(f.key, {})["draw"] = price
            continue
        name, _, method = label.rpartition(" by ")
        side = f.side_of(name)
        code = next((c for kw, c in METHODS if kw in method.upper()), None)
        if side is not None and code:
            raw.setdefault(f.key, {})[f"{'ab'[side]}_{code}"] = price
    for key, outcomes in raw.items():
        total = sum(outcomes.values())
        if total > 0 and len([k for k in outcomes if k != "draw"]) == 6:
            out.setdefault(key, {})["method"] = {k: v / total for k, v in outcomes.items()}
    return out


def fetch_kalshi_props(card: Card, http: httpx.Client) -> dict[str, dict]:
    fights = _paged(http, KALSHI_URL, {"series_ticker": KALSHI_SERIES[0], "status": "open", "limit": 1000}, "markets")
    props = {name: _paged(http, KALSHI_URL, {"series_ticker": s, "status": "open", "limit": 1000}, "markets")
             for name, s in KALSHI_PROP_SERIES.items()}
    return parse_kalshi_props(fights, props, card)


def fetch_polymarket(card: Card, http: httpx.Client) -> dict[str, MarketProb]:
    resp = http.get(POLY_URL, params={"tag_slug": "ufc", "closed": "false", "limit": 200})
    resp.raise_for_status()
    return parse_polymarket(resp.json(), card)


def fetch_all(card: Card, http: httpx.Client) -> tuple[dict[str, list[MarketProb]], list[str]]:
    """Prices by fight, plus the names of sources that failed."""
    out: dict[str, list[MarketProb]] = {}
    failed = []
    for name, fn in (("Kalshi", fetch_kalshi), ("Polymarket", fetch_polymarket)):
        try:
            for key, mp in fn(card, http).items():
                out.setdefault(key, []).append(mp)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("%s unavailable: %s", name, exc)
            failed.append(name)
    return out, failed
