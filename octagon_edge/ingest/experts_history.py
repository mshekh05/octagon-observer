"""Past SplitPicks Kings Tables, matched to ufcstats bouts, for backtesting the expert consensus.

Every event linked from https://splitpicks.com/kings that has finished (so it is in
the ufcstats history) is parsed with the same parser the app uses live. Pages are
cached; a finished event's picks don't change.

Caveat: each handicapper's weight comes from the "YTD" record printed on the page,
which may have been updated after the event, so it can carry a little hindsight.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx
import pandas as pd

from ..config import CACHE_DIR
from . import experts
from .card import Card, Fight

log = logging.getLogger(__name__)
CACHE = CACHE_DIR / "splitpicks"


def _page(url: str, http: httpx.Client, cache: Path) -> str:
    path = cache / (url.rsplit("/", 1)[-1] + ".html")
    if path.exists():
        return path.read_text(encoding="utf-8")
    html = experts.fetch_html(url, http)
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    time.sleep(0.5)  # be polite: one small site, fetched once per page
    return html


def load(fights: pd.DataFrame, http: httpx.Client, since: str = "2026-03-01", cache: Path = CACHE) -> pd.DataFrame:
    """One row per bout with picks: url, event, share_a (weighted share picking fighter_a), n.

    Only events from `since` are considered, so an old event with the same main-event
    pairing (a rematch) can't be matched to a newer page.
    """
    pages = experts.parse_index(experts.fetch_html(experts.KINGS_INDEX, http))
    rows = []
    for event, grp in fights[fights["date"] >= since].groupby("event", sort=False):
        # ufcstats lists the main event first, which find_event_page falls back on
        card = Card(event, str(grp["date"].iloc[0].date()),
                    [Fight(r.fighter_a, r.fighter_b, r.weightclass) for r in grp.itertuples()])
        url = experts.find_event_page(pages, card)
        if url is None:
            continue
        urls = {f"{r.fighter_a} vs {r.fighter_b}": r.url for r in grp.itertuples()}
        try:
            consensus = experts.parse(_page(url, http, cache), card)
        except httpx.HTTPError as exc:
            log.warning("SplitPicks %s unavailable: %s", url, exc)
            continue
        for key, c in consensus.items():
            if c.n and c.p_a is not None:
                rows.append({"url": urls[key], "event": card.event, "share_a": c.p_a, "n": c.n})
    return pd.DataFrame(rows, columns=["url", "event", "share_a", "n"])
