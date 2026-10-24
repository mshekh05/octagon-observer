"""Command line: octagon-edge {backtest,report,watch,test-alert}."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import REPORTS_DIR, Settings


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="octagon-edge", description="UFC betting analytics")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    bt = sub.add_parser("backtest", help="Evaluate the stats model on fights it never saw")
    bt.add_argument("--split", default="2023-01-01")

    rp = sub.add_parser("report", help="Build the HTML fight report for a card")
    rp.add_argument("--card", default="ufc-333")
    rp.add_argument("--out", type=Path)
    rp.add_argument("--no-news", action="store_true")
    rp.add_argument("--retrain", action="store_true")
    rp.add_argument("--refresh-data", action="store_true")

    w = sub.add_parser("watch", help="Poll prices during the event and push alerts")
    w.add_argument("--card", default="ufc-333")
    w.add_argument("--pm-every", type=int, default=20, help="seconds between Kalshi/Polymarket polls")
    w.add_argument("--odds-every", type=int, default=240, help="seconds between Odds API calls")
    w.add_argument("--hours", type=float, default=12)
    w.add_argument("--targets", type=Path, help='JSON of price targets, e.g. {"Movsar Evloev": "+120"}')
    w.add_argument("--once", action="store_true", help="single polling pass (for testing)")

    bm = sub.add_parser("backtest-market",
                        help="Compare the stats model with historical betting prices and suggest MODEL_WEIGHT")
    bm.add_argument("--start-year", type=int, default=2014)
    bm.add_argument("--holdout-from", default="2021-01-01")

    be = sub.add_parser("backtest-experts",
                        help="Test whether SplitPicks expert picks add anything to Kalshi prices and the model")
    be.add_argument("--since", default="2026-03-01")

    sub.add_parser("test-alert", help="Send a test notification to every configured channel")

    dr = sub.add_parser("doctor", help="Check every key and data source and explain fixes")
    dr.add_argument("--card", default="ufc-333")
    dr.add_argument("--no-send", action="store_true", help="don't send a test alert")

    ct = sub.add_parser("capture-tracker", help="Save UFC.com Fight Tracker JSON from a HAR file or URL")
    ct.add_argument("source", help="path to a .har export, or a JSON URL")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING if args.cmd == "doctor" else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    s = Settings.from_env()

    if args.cmd == "test-alert":
        from .notify import Notifier
        n = Notifier(s)
        ok = n.send("Octagon Edge test", "If you can read this on your phone, alerts work.", urgent=True)
        print(f"Configured: {n.channels or 'none'}; delivered: {ok or 'none'}")
        return

    from .ingest.card import load_card

    if args.cmd == "doctor":
        from .doctor import run as doctor
        raise SystemExit(doctor(load_card(args.card), s, send=not args.no_send))

    if args.cmd == "capture-tracker":
        from . import tracker_capture as tc
        src = Path(args.source)
        saved = tc.from_har(src) if src.exists() else tc.from_url(args.source)
        print(f"Saved {len(saved)} JSON response(s) to {tc.TRACKER_DIR}")
        for p in saved[:20]:
            print(f"  {p.name}")
        return

    from .engine import model as M
    from .pipeline import load_history, load_model

    if args.cmd == "backtest":
        hist = load_history()
        print(M.backtest(hist.rows, args.split).summary())
        return

    if args.cmd == "backtest-market":
        from .engine import market_backtest as MB
        from .pipeline import load_odds_history

        hist = load_history()
        print(MB.run(hist.rows, load_odds_history(hist), s.model_weight, args.start_year,
                     args.holdout_from, youth_adj=s.youth_adj).summary())
        return

    if args.cmd == "backtest-experts":
        from .engine import expert_backtest as EB
        from .pipeline import load_expert_history

        hist = load_history()
        experts, kalshi = load_expert_history(args.since)
        print(EB.run(hist.rows, experts, kalshi, s.model_weight, s.youth_adj, s.expert_weight).summary())
        return

    card = load_card(args.card)
    hist = load_history(refresh=getattr(args, "refresh_data", False))
    pipe = load_model(hist, retrain=getattr(args, "retrain", False))

    if args.cmd == "report":
        from .analysis import analyze_card
        from .ingest.odds_api import OddsApiClient
        from .llm.provider import Analyst
        from .pipeline import fetch_sources
        from .report import render

        client = OddsApiClient(s.odds_api_key, s.odds_api_regions, s.odds_api_budget) if s.odds_api_key else None
        odds, pms, items, experts, status = fetch_sources(card, s, client, with_news=not args.no_news)
        analyst = Analyst(s)
        status["LLM analyst"] = f"ok: {s.llm_provider}" if analyst.enabled else "skipped: LLM_PROVIDER=none"
        analyses = analyze_card(card, hist, pipe, s, odds, pms, items, analyst, experts)
        html = render(card, analyses, s.bankroll, M.backtest(hist.rows).summary(), status)
        out = args.out or REPORTS_DIR / f"{args.card}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html)
        for k, v in status.items():
            print(f"  {k}: {v}")
        picks = [fa for fa in analyses if fa.pick is not None]
        print(f"\n{len(picks)} value spot(s). Report: {out}")
        for fa in analyses:
            pa = fa.p_final
            print(f"  {fa.fight.key:<45} model {fa.p_model:5.1%}  market "
                  f"{'  –  ' if fa.p_market is None else f'{fa.p_market:5.1%}'}  blend {pa:5.1%}"
                  + (f"  -> VALUE {fa.name(fa.pick)}" if fa.pick is not None else ""))
        return

    if args.cmd == "watch":
        from .watch import run
        run(card, hist, pipe, s, args.pm_every, args.odds_every, args.hours, args.targets, args.once)


if __name__ == "__main__":
    main()
