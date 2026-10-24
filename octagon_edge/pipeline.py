"""Shared setup: load history, (re)train the model, fetch every source once."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .config import Settings
from .engine import model as M
from .engine.features import History
from .ingest import greco, news as news_mod, prediction_markets as pm
from .ingest.card import Card
from .ingest.odds_api import BudgetExceeded, OddsApiClient, parse as parse_odds

log = logging.getLogger(__name__)


def load_history(refresh: bool = False) -> History:
    src = greco.download(force=refresh)
    return History(greco.load_fights(src), greco.load_fight_totals(src), greco.load_fighters(src))


def load_live_model(hist: History, pipe, retrain: bool = False):
    from .engine import live

    if not retrain and live.LIVE_MODEL_PATH.exists():
        return live.load()
    return live.train(live_dataset(hist, pipe))


def live_dataset(hist: History, pipe):
    from .engine import live
    from .engine.features import FEATURES, diff_frame

    src = greco.download()
    rows = hist.rows
    p = pipe.predict_proba(diff_frame(rows)[[f"d_{k}" for k in FEATURES]])[:, 1]
    p_pre = dict(zip(rows["url"], p))
    return live.build_dataset(greco.load_fights(src), greco.load_round_stats(src), p_pre)


def load_model(hist: History, retrain: bool = False):
    if retrain or not M.MODEL_PATH.exists():
        log.info("Training model on %d fights", len(hist.rows))
        return M.train(hist.rows)
    return M.load()


def fetch_sources(card: Card, s: Settings, odds_client: OddsApiClient | None,
                  with_news: bool = True) -> tuple[dict, dict, list, dict[str, str]]:
    """Returns (odds by fight, prediction markets by fight, news items, status per source)."""
    status: dict[str, str] = {}
    http = httpx.Client(timeout=20, follow_redirects=True)
    odds = {}
    if odds_client is None:
        status["The Odds API (US books)"] = "skipped: ODDS_API_KEY not set"
    else:
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            odds = parse_odds(odds_client.fetch(), card, now)
            status["The Odds API (US books)"] = (f"ok: {len(odds)}/{len(card.fights)} fights, "
                                                 f"{odds_client.remaining} credits left")
        except (httpx.HTTPError, BudgetExceeded, RuntimeError) as exc:
            status["The Odds API (US books)"] = f"failed: {exc}"
    pms, failed = pm.fetch_all(card, http)
    if len(failed) == 2:
        status["Kalshi / Polymarket"] = "failed: both unreachable"
    else:
        status["Kalshi / Polymarket"] = (f"ok: {len(pms)}/{len(card.fights)} fights matched"
                                         + (f" ({failed[0]} failed)" if failed else ""))
    items: list = []
    if with_news:
        items = news_mod.fetch_rss(http)
        status["MMA news RSS"] = f"ok: {len(items)} items" if items else "failed or empty"
        names = [n for f in card.fights for n in (f.a, f.b)][:10]  # main card + top prelims
        reddit = news_mod.fetch_reddit(names, http)
        status["Reddit"] = f"ok: {len(reddit)} posts" if reddit else "failed or empty (rate-limited?)"
        items += reddit
    return odds, pms, items, status
