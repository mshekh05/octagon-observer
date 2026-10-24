"""Pre-fight Kalshi prices for settled UFC fights, from the public market-data API.

Kalshi lists one yes/no market per fighter ("X wins"); `close_time` is when trading
stopped at the start of the fight. The price used is the bid/ask mid of the last
hourly candle that ended at least 10 minutes before that: a closing-line price.
Results are cached, since settled markets don't change.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

from ..config import CACHE_DIR
from .card import Fight
from .prediction_markets import KALSHI_SERIES, KALSHI_URL, _num, _paged

log = logging.getLogger(__name__)
API = "https://api.elections.kalshi.com/trade-api/v2"
CANDLES_URL = API + "/series/{series}/markets/{ticker}/candlesticks"
HISTORICAL_URL = API + "/historical/markets"
HISTORICAL_CANDLES_URL = API + "/historical/markets/{ticker}/candlesticks"
CACHE = CACHE_DIR / "kalshi_history.json"


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def settled_markets(http: httpx.Client, since: str) -> list[dict]:
    """Every fight market in the UFC series settled yes/no since `since` (YYYY-MM-DD).

    Recent markets come from /markets; older ones are moved to /historical/markets
    (each market is tagged with `_historical` so its candles come from the right place).
    Cancelled bouts settle as something other than yes/no and are dropped.
    """
    out = []
    for series in KALSHI_SERIES:
        out += _paged(http, KALSHI_URL, {"series_ticker": series, "status": "settled", "limit": 1000,
                                         "min_close_ts": _ts(f"{since}T00:00:00Z")}, "markets", pages=30)
        recent = {m["ticker"] for m in out}
        for m in _paged(http, HISTORICAL_URL, {"series_ticker": series, "limit": 1000}, "markets", pages=30):
            if m["ticker"] not in recent and m.get("close_time", "") >= since:
                out.append({**m, "_historical": True})
    return [m for m in out if m.get("result") in ("yes", "no")]


def _cutoff(market: dict) -> int:
    """Last moment the price is still pre-fight: trading close or scheduled time, whichever is first."""
    times = [_ts(market[k]) for k in ("close_time", "occurrence_datetime") if market.get(k)]
    return min(times) - 600


def _close(part: dict | None) -> float | None:
    part = part or {}
    # live candles say "close_dollars", archived ones just "close"
    return _num(part.get("close_dollars", part.get("close")))


def candle_mid(candle: dict) -> float | None:
    bid, ask = _close(candle.get("yes_bid")), _close(candle.get("yes_ask"))
    if bid is not None and ask is not None and 0 < bid <= ask < 1:
        return (bid + ask) / 2
    last = _close(candle.get("price"))
    return last if last is not None and 0 < last < 1 else None


def _get(http: httpx.Client, url: str, params: dict, tries: int = 6) -> httpx.Response:
    """GET with backoff on 429: the public API rate-limits bursts of candle requests."""
    for attempt in range(tries):
        resp = http.get(url, params=params)
        if resp.status_code != 429:
            break
        wait = float(resp.headers.get("Retry-After") or 2 ** attempt)
        time.sleep(min(wait, 30))
    resp.raise_for_status()
    return resp


def closing_price(http: httpx.Client, market: dict, series: str = KALSHI_SERIES[0]) -> float | None:
    end = _cutoff(market)
    url = (HISTORICAL_CANDLES_URL.format(ticker=market["ticker"]) if market.get("_historical")
           else CANDLES_URL.format(series=series, ticker=market["ticker"]))
    resp = _get(http, url, {"start_ts": end - 3 * 24 * 3600, "end_ts": end, "period_interval": 60})
    candles = [c for c in resp.json().get("candlesticks", []) if c.get("end_period_ts", 0) <= end]
    for c in reversed(candles):
        if (p := candle_mid(c)) is not None:
            return p
    return None


def load(fights: pd.DataFrame, http: httpx.Client, since: str, cache: Path = CACHE) -> pd.DataFrame:
    """url, p_kalshi (de-vigged P(fighter_a wins) at the close) for bouts with both fighters' markets."""
    prices: dict[str, float | None] = json.loads(cache.read_text()) if cache.exists() else {}
    markets = settled_markets(http, since)
    for m in markets:
        if m["ticker"] not in prices:
            try:
                prices[m["ticker"]] = closing_price(http, m)
            except httpx.HTTPError as exc:
                log.warning("Kalshi candles for %s unavailable: %s", m["ticker"], exc)
                continue
            time.sleep(0.25)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(prices))
    return match(markets, prices, fights)


def match(markets: list[dict], prices: dict[str, float | None], fights: pd.DataFrame) -> pd.DataFrame:
    by_event: dict[str, list[dict]] = {}
    for m in markets:
        by_event.setdefault(m["event_ticker"], []).append(m)
    rows = []
    for ms in by_event.values():
        named = [(m.get("yes_sub_title") or "", prices.get(m["ticker"])) for m in ms]
        named = [(n, p) for n, p in named if n and p is not None]
        if len(named) != 2:
            continue
        day = datetime.fromtimestamp(_cutoff(ms[0]) + 600, tz=timezone.utc).date()
        cands = fights[(fights["date"].dt.date >= day - timedelta(days=1)) & (fights["date"].dt.date <= day)]
        for r in cands.itertuples():
            f = Fight(r.fighter_a, r.fighter_b, "")
            sides = [f.side_of(n) for n, _ in named]
            if sorted(s for s in sides if s is not None) == [0, 1]:
                p = {s: price for s, (_, price) in zip(sides, named)}
                rows.append({"url": r.url, "p_kalshi": p[0] / (p[0] + p[1])})
                break
    return pd.DataFrame(rows, columns=["url", "p_kalshi"]).drop_duplicates("url")
