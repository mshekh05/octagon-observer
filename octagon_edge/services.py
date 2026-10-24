"""Server-side orchestration shared by the API and the worker.

Wraps the Phase-0 engine (analysis, alerts, live model) with persistence.
"""
from __future__ import annotations

import dataclasses
import logging
import math
from datetime import datetime, timezone

import httpx
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .alerts import AlertEngine, FightState
from .analysis import FightAnalysis, analyze_card
from .config import Settings
from .db import (AlertRow, Bet, Event, Fight, LiveEstimate, LiveRound, OddsSnapshot, Prediction, SocialPost,
                 get_setting, latest, utcnow)
from .engine import live as L
from .engine import model as M
from .engine.odds_math import best_price
from .ingest import card as card_mod
from .ingest import news as news_mod
from .ingest import prediction_markets as pm
from .ingest.odds_api import BudgetExceeded, OddsApiClient, parse as parse_odds

log = logging.getLogger(__name__)

TUNABLE = ("bankroll", "kelly_fraction", "max_stake_pct", "edge_prefight", "edge_live", "model_weight")


def effective_settings(db: Session) -> Settings:
    """Environment settings with any overrides saved from the app's Settings screen."""
    base = Settings.from_env()
    overrides = get_setting(db, "tuning", {}) or {}
    return dataclasses.replace(base, **{k: float(v) for k, v in overrides.items() if k in TUNABLE})


# ---------- cards ----------

def sync_card(db: Session, slug: str) -> Event:
    """Create/update an event and its fights from octagon_edge/data/cards/<slug>.json."""
    c = card_mod.load_card(slug)
    ev = db.scalars(select(Event).where(Event.slug == slug)).first()
    if not ev:
        ev = Event(slug=slug, name=c.event, date=c.date)
        db.add(ev)
    ev.name, ev.date, ev.venue, ev.note = c.event, c.date, c.venue, c.source_note
    db.flush()
    existing = {(f.a, f.b): f for f in ev.fights}
    for i, f in enumerate(c.fights):
        row = existing.get((f.a, f.b))
        if not row:
            row = Fight(event_id=ev.id, a=f.a, b=f.b)
            db.add(row)
        row.order, row.weightclass, row.segment, row.rounds, row.title = i, f.weightclass, f.segment, f.rounds, f.title
    db.commit()
    db.refresh(ev)
    return ev


def card_of(ev: Event) -> card_mod.Card:
    ref = {(f.a, f.b): f.ref_odds for f in card_mod.load_card(ev.slug).fights}
    fights = [card_mod.Fight(f.a, f.b, f.weightclass, f.segment, f.title, f.rounds, ref.get((f.a, f.b)))
              for f in ev.fights]
    return card_mod.Card(ev.name, ev.date, fights, ev.venue, ev.note)


def fight_by_key(ev: Event) -> dict[str, Fight]:
    return {f.key: f for f in ev.fights}


# ---------- pre-fight analysis ----------

def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def _details(fa: FightAnalysis) -> dict:
    return {
        "record_a": fa.record_a, "record_b": fa.record_b,
        "snap_a": {k: _clean(v) for k, v in fa.snap_a.items()},
        "snap_b": {k: _clean(v) for k, v in fa.snap_b.items()},
        "notes": fa.notes, "llm": fa.llm,
        "books": {k: list(v) for k, v in fa.books.items()},
        "prediction_markets": [{"source": m.source, "p_a": m.p_a} for m in fa.prediction_markets],
        "sides": [dataclasses.asdict(s) for s in fa.sides],
        "tone_a": fa.tone_a, "tone_b": fa.tone_b,
    }


def refresh_analysis(db: Session, ev: Event, hist, pipe, odds_client: OddsApiClient | None,
                     with_news: bool = True, with_llm: bool = True, sources: tuple | None = None) -> list[Prediction]:
    """Run the full analysis and store it. `sources` = (odds, pms, items, status) skips fetching (demo/tests)."""
    from .llm.provider import Analyst
    from .pipeline import fetch_sources

    s = effective_settings(db)
    card = card_of(ev)
    if sources is None:
        odds, pms, items, status = fetch_sources(card, s, odds_client, with_news=with_news)
        if not odds:
            odds = stored_odds(db, ev)
            if odds:
                status["The Odds API (US books)"] = f"ok: {len(odds)} fights from the latest stored prices"
        if with_news:
            items += collect_social(card)
    else:
        odds, pms, items, status = sources
    analyst = Analyst(s) if with_llm else None
    analyses = analyze_card(card, hist, pipe, s, odds, pms, items, analyst)
    by_key = fight_by_key(ev)
    out = []
    for fa in analyses:
        f = by_key[fa.fight.key]
        sv = fa.sides[fa.pick] if fa.pick is not None else None
        pred = Prediction(fight_id=f.id, p_model=fa.p_model, p_market=fa.p_market, p_final=fa.p_final,
                          market_source=fa.market_source, confidence=fa.confidence, pick_side=fa.pick,
                          pick_book=sv.book if sv else None, pick_decimal=sv.decimal if sv else None,
                          pick_edge=sv.edge if sv else None, stake=sv.stake if sv else None,
                          details={**_details(fa), "sources": status})
        db.add(pred)
        out.append(pred)
        _store_snapshots(db, f, odds.get(f.key), pms.get(f.key, []))
        _store_posts(db, f, fa)
    db.commit()
    return out


def stored_odds(db: Session, ev: Event, max_age_h: float = 12) -> dict:
    """Latest sportsbook prices per fight saved by the price job (saves Odds API credits)."""
    from .ingest.odds_api import FightOdds

    out = {}
    for f in ev.fights:
        ts = db.scalar(select(func.max(OddsSnapshot.ts)).where(OddsSnapshot.fight_id == f.id,
                                                                OddsSnapshot.source == "odds_api"))
        if ts is None or (utcnow() - _aware(ts)).total_seconds() > max_age_h * 3600:
            continue
        snaps = db.scalars(select(OddsSnapshot).where(OddsSnapshot.fight_id == f.id, OddsSnapshot.source == "odds_api",
                                                      OddsSnapshot.ts == ts)).all()
        out[f.key] = FightOdds(books={x.book: (x.dec_a, x.dec_b) for x in snaps}, live=f.status == "live")
    return out


def collect_social(card: card_mod.Card) -> list[news_mod.NewsItem]:
    from .ingest import social

    http = httpx.Client(timeout=20, follow_redirects=True)
    main = [f for f in card.fights if f.segment == "main"]
    items = social.fetch_bluesky([n for f in main for n in (f.a, f.b)], http)
    items += social.fetch_youtube([(f.a, f.b) for f in main], http)
    token = social.reddit_token(http)
    if token:  # authenticated Reddit is far more reliable than the public JSON in fetch_sources
        items += news_mod.fetch_reddit([n for f in main for n in (f.a, f.b)], http, token=token)
    return items


def _store_snapshots(db: Session, f: Fight, fo, pms) -> None:
    now = utcnow()
    if fo:
        for book, (da, dbb) in fo.books.items():
            db.add(OddsSnapshot(fight_id=f.id, ts=now, source="odds_api", book=book, dec_a=da, dec_b=dbb))
    for m in pms:
        db.add(OddsSnapshot(fight_id=f.id, ts=now, source=m.source, p_a=m.p_a))


def _store_posts(db: Session, f: Fight, fa: FightAnalysis) -> None:
    from .sentiment import score

    for name, items in ((f.a, fa.news_a), (f.b, fa.news_b)):
        seen = set(db.scalars(select(SocialPost.link).where(SocialPost.fighter == name)).all())
        new = [i for i in items if i.link and i.link not in seen]
        for item, sc in zip(new, score([i.text for i in new])):
            seen.add(item.link)
            db.add(SocialPost(fighter=name, source=item.source, title=item.title[:500], link=item.link[:500],
                              text=item.text[:4000], flags=item.flags, sentiment=sc))


# ---------- live polling & alerts ----------

class PricePoller:
    """Keeps alert-engine state between polls (one instance per worker process)."""

    def __init__(self, notifier, odds_client: OddsApiClient | None):
        self.notifier = notifier
        self.odds_client = odds_client
        self.sent: dict = {}  # alert de-dup memory, kept across polls
        self.states: dict[int, FightState] = {}
        self.odds: dict = {}
        self.http = httpx.Client(timeout=15)

    def poll(self, db: Session, ev: Event, hist, pipe, with_odds: bool, targets: dict[str, float]) -> list[AlertRow]:
        s = effective_settings(db)  # re-read so changes from the Settings screen apply at once
        engine = AlertEngine(s.bankroll, s.kelly_fraction, s.max_stake_pct, s.edge_prefight,
                             s.edge_live, s.model_weight)
        engine.sent = self.sent
        card = card_of(ev)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if with_odds and self.odds_client:
            try:
                self.odds = parse_odds(self.odds_client.fetch(), card, now_iso)
            except BudgetExceeded as exc:
                log.warning("%s - prediction markets only from now on", exc)
                self.odds_client = None
            except httpx.HTTPError as exc:
                log.warning("Odds API error: %s", exc)
        pms, _ = pm.fetch_all(card, self.http)
        on = pd.Timestamp(ev.date)
        rows: list[AlertRow] = []
        for f in ev.fights:
            if f.id not in self.states:
                self.states[f.id] = FightState(M.predict(pipe, hist.current(f.a, on), hist.current(f.b, on)), f.a, f.b)
            fo = self.odds.get(f.key) if with_odds else None
            lst = pms.get(f.key, [])
            _store_snapshots(db, f, fo, lst)
            if fo and fo.live and f.status == "scheduled":
                set_status(db, f, "live")
            pm_p = sum(m.p_a for m in lst) / len(lst) if lst else None
            books = (self.odds.get(f.key).books if self.odds.get(f.key) else {})
            for a in engine.evaluate(f.key, self.states[f.id], books, pm_p, f.status == "live", targets):
                row = AlertRow(fight_id=f.id, kind=a.kind, title=a.title, body=a.body, urgent=a.urgent)
                db.add(row)
                rows.append(row)
                self.notifier.send(a.title, a.body, urgent=a.urgent,
                                   tags="rotating_light" if a.urgent else "moneybag", path=f"/fight/{f.id}")
            if f.status == "live" and pm_p is not None:
                update_live_estimate(db, f, None, pm_p)
        db.commit()
        return rows


# ---------- live mode ----------

def _round_inputs(db: Session, f: Fight) -> list[L.RoundInput]:
    rows = db.scalars(select(LiveRound).where(LiveRound.fight_id == f.id).order_by(LiveRound.round)).all()
    return [L.RoundInput(r.winner, r.kd_a, r.kd_b, r.td_a, r.td_b, r.sig_a, r.sig_b, r.ctrl_a, r.ctrl_b) for r in rows]


def update_live_estimate(db: Session, f: Fight, live_model: L.LiveModel | None, p_market: float | None = None) -> LiveEstimate:
    """Recompute the live probability from recorded rounds + latest prediction-market price."""
    pred = latest(db, Prediction, f.id)
    p_pre = (pred.p_final if pred and pred.p_final is not None else 0.5)
    rounds = _round_inputs(db, f)
    if live_model is None and L.LIVE_MODEL_PATH.exists():
        live_model = L.load()
    p_rounds = live_model.predict(p_pre, rounds, f.rounds) if live_model else p_pre
    if p_market is None:
        recent = db.scalars(select(OddsSnapshot).where(OddsSnapshot.fight_id == f.id, OddsSnapshot.p_a.is_not(None))
                            .order_by(OddsSnapshot.id.desc()).limit(1)).first()
        if recent and (utcnow() - _aware(recent.ts)).total_seconds() < 180:
            p_market = recent.p_a
    est = LiveEstimate(fight_id=f.id, after_round=len(rounds), p_rounds=p_rounds, p_market=p_market,
                       p_live=L.blend_with_market(p_rounds, p_market))
    db.add(est)
    db.commit()
    return est


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def record_round(db: Session, f: Fight, rnd: int, data: L.RoundInput, live_model=None, source: str = "manual") -> LiveEstimate:
    row = db.scalars(select(LiveRound).where(LiveRound.fight_id == f.id, LiveRound.round == rnd)).first()
    if not row:
        row = LiveRound(fight_id=f.id, round=rnd)
        db.add(row)
    for k, v in dataclasses.asdict(data).items():
        setattr(row, k, v)
    row.source = source
    if f.status == "scheduled":
        set_status(db, f, "live")
    db.commit()
    return update_live_estimate(db, f, live_model)


# ---------- fight status, closing line, bets ----------

def best_current_price(db: Session, f: Fight, side: int, book: str | None = None) -> float | None:
    latest_ts = db.scalar(select(func.max(OddsSnapshot.ts)).where(OddsSnapshot.fight_id == f.id,
                                                                 OddsSnapshot.source == "odds_api"))
    if latest_ts is None:
        return None
    snaps = db.scalars(select(OddsSnapshot).where(OddsSnapshot.fight_id == f.id, OddsSnapshot.source == "odds_api",
                                                  OddsSnapshot.ts == latest_ts)).all()
    books = {s.book: (s.dec_a, s.dec_b) for s in snaps}
    if book and book in books:
        return books[book][side]
    bp = best_price(books, side)
    return bp[1] if bp else None


def set_status(db: Session, f: Fight, status: str, winner: int | None = None) -> None:
    if status == "live" and f.status == "scheduled":
        f.started_at = utcnow()
        # closing line = last pre-fight price, used for CLV
        for b in db.scalars(select(Bet).where(Bet.fight_id == f.id, Bet.closing_decimal.is_(None), Bet.live.is_(False))):
            b.closing_decimal = best_current_price(db, f, b.side, b.book)
    f.status = status
    if status == "finished":
        f.winner = winner
        for b in db.scalars(select(Bet).where(Bet.fight_id == f.id, Bet.result == "open")):
            settle_bet(b, winner)
    db.commit()


def settle_bet(b: Bet, winner: int | None) -> None:
    if winner is None:
        b.result, b.pnl = "void", 0.0
    elif winner == b.side:
        b.result, b.pnl = "won", round(b.stake * (b.decimal - 1), 2)
    else:
        b.result, b.pnl = "lost", -b.stake


def clv(b: Bet) -> float | None:
    """Closing line value: how much better our price was than the closing price."""
    if not b.closing_decimal:
        return None
    return b.decimal / b.closing_decimal - 1


def bet_summary(db: Session) -> dict:
    bets = db.scalars(select(Bet)).all()
    settled = [b for b in bets if b.result in ("won", "lost")]
    staked = sum(b.stake for b in settled)
    pnl = sum(b.pnl or 0 for b in settled)
    clvs = [c for c in (clv(b) for b in bets) if c is not None]
    return {
        "bets": len(bets), "open": sum(b.result == "open" for b in bets), "settled": len(settled),
        "won": sum(b.result == "won" for b in settled), "staked": round(staked, 2), "pnl": round(pnl, 2),
        "roi": round(pnl / staked, 4) if staked else None,
        "avg_clv": round(sum(clvs) / len(clvs), 4) if clvs else None, "clv_n": len(clvs),
    }
