"""Event-day watcher: polls prices, logs snapshots, pushes alerts to your phone."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from .alerts import AlertEngine, FightState
from .analysis import confidence_gate, threshold_mult
from .config import DATA_DIR, Settings
from .engine import model as M
from .engine.odds_math import american_to_decimal
from .ingest import prediction_markets as pm
from .ingest.card import Card
from .ingest.odds_api import BudgetExceeded, OddsApiClient, parse as parse_odds
from .notify import Notifier

log = logging.getLogger(__name__)
DB_PATH = DATA_DIR / "snapshots.db"


def open_db(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS snapshot(
        ts TEXT, fight TEXT, source TEXT, book TEXT, p_a REAL, dec_a REAL, dec_b REAL)""")
    db.execute("CREATE TABLE IF NOT EXISTS alert(ts TEXT, fight TEXT, kind TEXT, title TEXT, body TEXT)")
    return db


def load_targets(path: Path | None) -> dict[str, float]:
    """JSON like {"Movsar Evloev": "+120"} -> {name: decimal}."""
    if not path or not path.exists():
        return {}
    return {name: american_to_decimal(float(str(v).replace("+", ""))) for name, v in json.loads(path.read_text()).items()}


def run(card: Card, hist, pipe, s: Settings, pm_every: int = 20, odds_every: int = 240,
        hours: float = 12, targets_path: Path | None = None, once: bool = False) -> None:
    notifier = Notifier(s)
    engine = AlertEngine(s.bankroll, s.kelly_fraction, s.max_stake_pct, s.edge_prefight,
                         s.edge_live, s.model_weight)
    odds_client = OddsApiClient(s.odds_api_key, s.odds_api_regions, s.odds_api_budget) if s.odds_api_key else None
    on = pd.Timestamp(card.date)
    states = {}
    for f in card.fights:
        snap_a, snap_b = hist.current(f.a, on), hist.current(f.b, on)
        _, confidence, weight = confidence_gate(f, snap_a, snap_b, s.model_weight)
        age_gap = snap_a["age"] - snap_b["age"] if pd.notna(snap_a["age"]) and pd.notna(snap_b["age"]) else None
        states[f.key] = FightState(M.predict(pipe, snap_a, snap_b), f.a, f.b, model_weight=weight,
                                   threshold_mult=threshold_mult(confidence), age_gap=age_gap,
                                   youth_adj=s.youth_adj)
    targets = load_targets(targets_path)
    db = open_db()
    http = httpx.Client(timeout=15)
    odds: dict = {}
    last_odds = 0.0
    end = time.time() + hours * 3600
    log.info("Watching %s: %d fights, alerts via %s, Odds API %s", card.event, len(card.fights),
             ", ".join(notifier.channels) or "console only", "on" if odds_client else "off")
    notifier.send(f"Watching {card.event}", f"{len(card.fights)} fights. Alert channels: "
                  f"{', '.join(notifier.channels) or 'console'}.", tags="eyes")

    while time.time() < end:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if odds_client and time.time() - last_odds >= odds_every:
            last_odds = time.time()
            try:
                odds = parse_odds(odds_client.fetch(), card, now_iso)
                for key, fo in odds.items():
                    db.executemany("INSERT INTO snapshot VALUES (?,?,?,?,?,?,?)",
                                   [(now_iso, key, "odds_api", b, None, da, dbb) for b, (da, dbb) in fo.books.items()])
            except BudgetExceeded as exc:
                log.warning("%s - continuing with prediction markets only", exc)
                odds_client = None
            except httpx.HTTPError as exc:
                log.warning("Odds API error: %s", exc)
        pms, _ = pm.fetch_all(card, http)
        for key, lst in pms.items():
            db.executemany("INSERT INTO snapshot VALUES (?,?,?,?,?,?,?)",
                           [(now_iso, key, m.source, None, m.p_a, None, None) for m in lst])

        for f in card.fights:
            fo = odds.get(f.key)
            lst = pms.get(f.key, [])
            pm_p = sum(m.p_a for m in lst) / len(lst) if lst else None
            alerts = engine.evaluate(f.key, states[f.key], fo.books if fo else {}, pm_p,
                                     bool(fo and fo.live), targets)
            for a in alerts:
                log.info("ALERT %s | %s", a.title, a.body.replace("\n", " | "))
                notifier.send(a.title, a.body, urgent=a.urgent,
                              tags="rotating_light" if a.urgent else "moneybag")
                db.execute("INSERT INTO alert VALUES (?,?,?,?,?)", (now_iso, f.key, a.kind, a.title, a.body))
        db.commit()
        if once:
            break
        time.sleep(pm_every)
