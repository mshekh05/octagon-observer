"""Fill a separate demo database with SIMULATED prices, rounds and bets so the app can be explored offline.

    DATABASE_URL=sqlite:///data/demo.db python scripts/seed_demo.py
    DATABASE_URL=sqlite:///data/demo.db uvicorn octagon_edge.api:app

Never point this at your real database: every price it writes is made up.
"""
from __future__ import annotations

import os
import random
from datetime import timedelta

from octagon_edge import services as S
from octagon_edge.db import AlertRow, Bet, OddsSnapshot, sessions, utcnow
from octagon_edge.engine.live import RoundInput
from octagon_edge.engine.odds_math import devig_power
from octagon_edge.ingest.news import NewsItem
from octagon_edge.ingest.odds_api import FightOdds
from octagon_edge.ingest.prediction_markets import MarketProb
from octagon_edge.pipeline import load_history, load_live_model, load_model

BOOKS = ("DraftKings", "FanDuel", "BetMGM", "Caesars")


def price_pair(p: float, rng: random.Random, margin: float = 0.045) -> tuple[float, float]:
    pa = min(max(p + rng.uniform(-0.03, 0.03), 0.05), 0.95)
    return 1 / (pa * (1 + margin)), 1 / ((1 - pa) * (1 + margin))


def main() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if "demo" not in url:
        raise SystemExit("Refusing to seed: set DATABASE_URL to a database whose name contains 'demo'.")
    rng = random.Random(333)
    hist = load_history()
    pipe = load_model(hist)
    live_model = load_live_model(hist, pipe)
    with sessions()() as db:
        ev = S.sync_card(db, "ufc-333")
        card = S.card_of(ev)
        now = utcnow()
        odds, pms = {}, {}
        for f, fight in zip(card.fights, ev.fights):
            from octagon_edge.engine import model as M
            import pandas as pd
            p_model = M.predict(pipe, hist.current(f.a, pd.Timestamp(card.date)), hist.current(f.b, pd.Timestamp(card.date)))
            if f.ref_odds:
                from octagon_edge.engine.odds_math import american_to_decimal
                base = devig_power([american_to_decimal(x) for x in f.ref_odds])[0]
            else:
                base = min(max(0.5 + (p_model - 0.5) * 1.3 + rng.uniform(-0.12, 0.12), 0.12), 0.88)
            # 24h of simulated line history, drifting towards today's price
            start = base + rng.uniform(-0.06, 0.06)
            for h in range(24, -1, -3):
                p = base + (start - base) * h / 24
                ts = now - timedelta(hours=h)
                for book in BOOKS[: 2 + rng.randint(0, 2)]:
                    da, dbb = price_pair(p, rng)
                    db.add(OddsSnapshot(fight_id=fight.id, ts=ts, source="odds_api", book=book, dec_a=da, dec_b=dbb))
                db.add(OddsSnapshot(fight_id=fight.id, ts=ts, source="Kalshi", p_a=min(max(p + rng.uniform(-0.02, 0.02), 0.03), 0.97)))
            books = {b: price_pair(base, rng) for b in BOOKS}
            if fight.order == 1:  # make one clear value spot so the UI has something to show
                da, dbb = books["BetMGM"]
                books["BetMGM"] = (da, dbb * 1.12)
            odds[f.key] = FightOdds(books=books)
            pms[f.key] = [MarketProb("Kalshi", base)]
        items = [
            NewsItem("r/MMA", "Evloev looked sharp at open workouts, cardio questions answered", "https://example.com/demo-1",
                     "Evloev looked sharp at open workouts", []),
            NewsItem("MMA Junkie", "Volkanovski camp: minor hand injury healed, 'never felt better'", "https://example.com/demo-2",
                     "Volkanovski minor hand injury healed", ["injury"]),
            NewsItem("Bluesky", "Merab's pace is going to break Yan again, easy work", "https://example.com/demo-3",
                     "Merab Dvalishvili pace easy work", []),
        ]
        status = {"The Odds API (US books)": "ok: SIMULATED demo prices", "Kalshi / Polymarket": "ok: SIMULATED demo prices",
                  "News": "ok: 3 SIMULATED demo items"}
        S.refresh_analysis(db, ev, hist, pipe, None, with_news=False, with_llm=False, sources=(odds, pms, items, {}, status))

        # main event is live: two rounds scored
        main_fight = ev.fights[0]
        S.record_round(db, main_fight, 1, RoundInput(winner=1, td_b=2), live_model)
        S.record_round(db, main_fight, 2, RoundInput(winner=0, kd_a=1), live_model)
        db.add(AlertRow(fight_id=ev.fights[1].id, kind="pre", urgent=False, title="VALUE: Merab Dvalishvili +168 (SIMULATED)",
                        body="Merab Dvalishvili +168 at BetMGM\nOur prob 45% vs price 37% → edge +7.9 pts\nDemo data."))
        db.add(Bet(fight_id=ev.fights[1].id, side=1, book="BetMGM", decimal=2.68, stake=24, note="demo"))
        db.add(Bet(fight_id=ev.fights[5].id, side=0, book="FanDuel", decimal=1.91, stake=15, closing_decimal=1.83,
                   result="won", pnl=13.65, note="demo"))
        db.commit()
    print("Demo database seeded with SIMULATED data.")


if __name__ == "__main__":
    main()
