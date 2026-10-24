"""Background jobs (APScheduler). Run: python -m octagon_edge.worker

- prices: Kalshi/Polymarket every PM_EVERY seconds on event day, Odds API on a
  budget (every ODDS_EVERY seconds during the event window, every 3h in fight week,
  every 12h before that)
- analysis: model + market + news + LLM refresh every 30 min (LLM at most every 6h)
- stats: refresh ufcstats data and retrain both models daily
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

from . import services as S
from .config import DATA_DIR
from .db import Event, sessions
from .ingest.odds_api import OddsApiClient
from .notify import Notifier
from .pipeline import load_history, load_live_model, load_model
from .watch import load_targets

log = logging.getLogger("octagon_edge.worker")


def odds_interval(now: datetime, event_date: str, in_window: bool) -> int:
    """Seconds between Odds API polls.

    Most line movement happens 1-7 days out and favourites tend to shorten toward the
    close, so fight week is polled more often (FIGHT_WEEK_ODDS_EVERY, default 3h) to catch
    value early; before that, twice a day saves credits.
    """
    if in_window:
        return int(os.environ.get("ODDS_EVERY", 240))
    start = datetime.fromisoformat(event_date).replace(tzinfo=timezone.utc)
    if start - now <= timedelta(days=7):
        return int(os.environ.get("FIGHT_WEEK_ODDS_EVERY", 3 * 3600))
    return 12 * 3600


class Worker:
    def __init__(self, slug: str):
        self.slug = slug
        self.Session = sessions()
        self.hist = load_history()
        self.pipe = load_model(self.hist)
        self.live = load_live_model(self.hist, self.pipe)
        with self.Session() as db:
            s = S.effective_settings(db)
            S.sync_card(db, slug)
        self.odds_client = OddsApiClient(s.odds_api_key, s.odds_api_regions, s.odds_api_budget) if s.odds_api_key else None
        notifier = Notifier(s, webpush=self._webpush)
        self.poller = S.PricePoller(notifier, self.odds_client)
        self.last_odds = 0.0
        self.last_llm = 0.0
        self.targets_path = Path(os.environ.get("TARGETS_FILE", DATA_DIR / "targets.json"))

    def _webpush(self, title: str, body: str, path: str, urgent: bool) -> int:
        from .webpush import send_all

        with self.Session() as db:
            return send_all(db, title, body, path, urgent)

    def _event(self, db) -> Event:
        return db.scalars(select(Event).where(Event.slug == self.slug)).one()

    def event_window(self, ev: Event) -> bool:
        """Event day: from EVENT_WINDOW_START_UTC (default 11:00 UTC) on the event date for 16 hours.

        Covers Abu Dhabi cards (prelims ~14:00 UTC) and US cards (prelims ~22:00 UTC).
        """
        start_h = int(os.environ.get("EVENT_WINDOW_START_UTC", 11))
        start = datetime.fromisoformat(ev.date).replace(tzinfo=timezone.utc) + timedelta(hours=start_h)
        now = datetime.now(timezone.utc)
        return start <= now <= start + timedelta(hours=16)

    def prices(self) -> None:
        with self.Session() as db:
            ev = self._event(db)
            in_window = self.event_window(ev)
            every = odds_interval(datetime.now(timezone.utc), ev.date, in_window)
            with_odds = time.time() - self.last_odds >= every
            if with_odds:
                self.last_odds = time.time()
            if not in_window and not with_odds:
                return  # outside event day, prediction markets move slowly - poll with the odds cadence
            targets = {**load_targets(self.targets_path), **S.db_targets(db)}  # the app's targets win
            rows = self.poller.poll(db, ev, self.hist, self.pipe, with_odds, targets)
            for r in rows:
                log.info("ALERT %s", r.title)

    def analysis(self) -> None:
        with_llm = time.time() - self.last_llm >= 6 * 3600
        with self.Session() as db:
            ev = S.sync_card(db, self.slug)
            # Odds are fetched by the price job; analysis reuses the latest prices it stored.
            S.refresh_analysis(db, ev, self.hist, self.pipe, None, with_news=True, with_llm=with_llm)
        if with_llm:
            self.last_llm = time.time()
        log.info("Analysis refreshed (llm=%s)", with_llm)

    def stats(self) -> None:
        self.hist = load_history(refresh=True)
        self.pipe = load_model(self.hist, retrain=True)
        self.live = load_live_model(self.hist, self.pipe, retrain=True)
        log.info("Stats refreshed and models retrained")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    w = Worker(os.environ.get("ACTIVE_CARD", "ufc-333"))
    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(w.prices, "interval", seconds=int(os.environ.get("PM_EVERY", 20)), max_instances=1,
                  coalesce=True, next_run_time=datetime.now(timezone.utc) + timedelta(seconds=5))
    sched.add_job(w.analysis, "interval", minutes=30, max_instances=1, coalesce=True,
                  next_run_time=datetime.now(timezone.utc))
    sched.add_job(w.stats, "cron", hour=9, minute=17, max_instances=1)
    log.info("Worker started for %s; notifications: %s", w.slug, json.dumps(w.poller.notifier.channels))
    sched.start()


if __name__ == "__main__":
    main()
