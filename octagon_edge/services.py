"""Server-side orchestration shared by the API and the worker.

Wraps the Phase-0 engine (analysis, alerts, live model) with persistence.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import time
from datetime import datetime, timezone

import httpx
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .alerts import AlertEngine, FightState
from .analysis import FightAnalysis, analyze_card
from .config import Settings
from . import tracking
from .db import (AlertRow, Bet, Event, Fight, FightResult, LiveEstimate, LiveRound, OddsSnapshot, PaperPick,
                 Prediction, PropSnapshot, SocialPost, get_setting, latest, utcnow)
from .engine import live as L
from .engine import model as M
from .engine.edge import bet_at_decimal
from .engine.odds_math import best_price, consensus_fair_prob
from .ingest import card as card_mod
from .ingest import news as news_mod
from .ingest import prediction_markets as pm
from .ingest.odds_api import BudgetExceeded, OddsApiClient, parse as parse_odds

log = logging.getLogger(__name__)

TUNABLE = ("bankroll", "kelly_fraction", "max_stake_pct", "edge_prefight", "edge_live", "model_weight",
           "card_budget", "lean_edge")


def effective_settings(db: Session) -> Settings:
    """Environment settings with any overrides saved from the app's Settings screen."""
    base = Settings.from_env()
    overrides = get_setting(db, "tuning", {}) or {}
    s = dataclasses.replace(base, **{k: float(v) for k, v in overrides.items() if k in TUNABLE})
    books = get_setting(db, "books", {}) or {}
    if books.get("home"):
        s = dataclasses.replace(s, home_book=str(books["home"]))
    if "usable" in books:
        s = dataclasses.replace(s, usable_books=tuple(books["usable"] or ()))
    return s


def usable(books: dict[str, tuple[float, float]], s: Settings) -> dict[str, tuple[float, float]]:
    """Only the books you can bet at (all of them when none are chosen)."""
    return {k: v for k, v in books.items() if not s.usable_books or k in s.usable_books}


def db_targets(db: Session) -> dict[str, float]:
    """Price targets set from the app: fighter name -> decimal price."""
    return {k: float(v) for k, v in (get_setting(db, "targets", {}) or {}).items()}


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
        "p_experts": fa.p_experts, "experts_source": fa.experts_source,
        "blend_inputs": fa.blend_inputs, "pricing": fa.pricing, "prop_model": fa.prop_model,
    }


_PROP_MODEL: list = []


def _prop_model():
    """The trained prop model if one has been saved (pipeline.load_prop_model trains it); else None."""
    from .engine import props_model as PM

    if not _PROP_MODEL and PM.PROPS_MODEL_PATH.exists():
        _PROP_MODEL.append(PM.load())
    return _PROP_MODEL[0] if _PROP_MODEL else None


def refresh_analysis(db: Session, ev: Event, hist, pipe, odds_client: OddsApiClient | None,
                     with_news: bool = True, with_llm: bool = True, sources: tuple | None = None) -> list[Prediction]:
    """Run the full analysis and store it. `sources` = (odds, pms, items, experts, status) skips fetching (demo/tests)."""
    from .llm.provider import Analyst
    from .pipeline import fetch_sources

    s = effective_settings(db)
    card = card_of(ev)
    if sources is None:
        odds, pms, items, experts, status = fetch_sources(card, s, odds_client, with_news=with_news)
        if not odds:
            odds = stored_odds(db, ev)
            if odds:
                status["The Odds API (US books)"] = f"ok: {len(odds)} fights from the latest stored prices"
        if with_news:
            items += collect_social(card)
    else:
        odds, pms, items, experts, status = sources
    analyst = Analyst(s) if with_llm else None
    analyses = analyze_card(card, hist, pipe, s, odds, pms, items, analyst, experts, _prop_model())
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
        if sv and f.status == "scheduled":
            p_side = fa.p_final if fa.pick == 0 else 1 - fa.p_final
            m_side = None if fa.p_market is None else fa.p_market if fa.pick == 0 else 1 - fa.p_market
            record_paper_pick(db, f, "pick", fa.pick, sv.book, sv.decimal, p_side, m_side, sv.source)
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
    from sqlalchemy.exc import IntegrityError

    from .sentiment import score

    for name, items in ((f.a, fa.news_a), (f.b, fa.news_b)):
        seen = set(db.scalars(select(SocialPost.link).where(SocialPost.fighter == name)).all())
        new = [i for i in items if i.link and i.link not in seen]
        for item, sc in zip(new, score([i.text for i in new])):
            if item.link in seen:
                continue  # the same link can appear twice in one fetch (e.g. RSS and a mirror)
            seen.add(item.link)
            try:
                with db.begin_nested():
                    db.add(SocialPost(fighter=name, source=item.source, title=item.title[:500],
                                      link=item.link[:500], text=item.text[:4000], flags=item.flags,
                                      sentiment=sc))
                    db.flush()
            except IntegrityError:
                # Already stored under this fighter (race with another cycle, or a stale `seen`
                # snapshot) - skip it rather than losing this whole analysis commit, predictions
                # for every other fight included.
                log.debug("Skipping duplicate social post for %s: %s", name, item.link)


# ---------- props ----------

def _store_totals(db: Session, ev: Event, odds: dict) -> None:
    now = utcnow()
    for f in ev.fights:
        fo = odds.get(f.key)
        for book, (point, over, under) in (fo.totals.items() if fo else ()):
            db.add(PropSnapshot(fight_id=f.id, ts=now, source="odds_api", market="total_rounds", book=book,
                                point=point, outcome="over", decimal=over))
            db.add(PropSnapshot(fight_id=f.id, ts=now, source="odds_api", market="total_rounds", book=book,
                                point=point, outcome="under", decimal=under))


def _store_kalshi_props(db: Session, ev: Event, props: dict[str, dict]) -> None:
    now = utcnow()
    for f in ev.fights:
        fp = props.get(f.key) or {}
        if fp.get("distance") is not None:
            db.add(PropSnapshot(fight_id=f.id, ts=now, source="Kalshi", market="distance", outcome="yes",
                                p=fp["distance"]))
        for outcome, p in (fp.get("method") or {}).items():
            db.add(PropSnapshot(fight_id=f.id, ts=now, source="Kalshi", market="method", outcome=outcome, p=p))


def _latest_props(db: Session, f: Fight, source: str, market: str) -> list[PropSnapshot]:
    ts = db.scalar(select(func.max(PropSnapshot.ts)).where(PropSnapshot.fight_id == f.id,
                                                           PropSnapshot.source == source, PropSnapshot.market == market))
    if ts is None:
        return []
    return list(db.scalars(select(PropSnapshot).where(PropSnapshot.fight_id == f.id, PropSnapshot.source == source,
                                                      PropSnapshot.market == market, PropSnapshot.ts == ts)))


def props_view(db: Session, f: Fight, s: Settings) -> dict:
    """Prop fair prices (Kalshi) and total-rounds line shopping (sportsbooks) for one fight.

    Totals are judged like the moneyline: each book's over/under is de-vigged, the median
    across books is the fair chance, and a book's price is flagged when it beats that fair
    chance by EDGE_PREFIGHT. No model is involved.
    """
    out: dict = {"distance": None, "method": [], "totals": [], "threshold": s.edge_prefight, "flags": []}
    dist = _latest_props(db, f, "Kalshi", "distance")
    if dist:
        out["distance"] = {"p": dist[0].p, "ts": dist[0].ts.isoformat()}
    names = {"a": f.a, "b": f.b}
    labels = {"ko": "KO/TKO", "sub": "submission", "dec": "decision"}
    for r in sorted(_latest_props(db, f, "Kalshi", "method"), key=lambda r: r.outcome):
        label = "Draw / no contest" if r.outcome == "draw" else f"{names[r.outcome[0]]} by {labels[r.outcome[2:]]}"
        out["method"].append({"outcome": r.outcome, "label": label, "p": r.p})

    rows = _latest_props(db, f, "odds_api", "total_rounds")
    by_point: dict[float, dict[str, dict[str, float]]] = {}
    for r in rows:
        by_point.setdefault(r.point, {}).setdefault(r.book, {})[r.outcome] = r.decimal
    for point, books in sorted(by_point.items()):
        pairs = {b: (o["over"], o["under"]) for b, o in books.items() if "over" in o and "under" in o}
        if not pairs:
            continue
        fair_over = consensus_fair_prob(pairs)
        shop = usable(pairs, s)
        line = {"point": point, "books": len(pairs), "fair_over": fair_over, "ts": rows[0].ts.isoformat(),
                "prices": {b: list(v) for b, v in pairs.items()}}
        for side, name in ((0, "over"), (1, "under")):
            bp = best_price(shop, side) if shop else None
            p_side = fair_over if side == 0 else 1 - fair_over
            line[name] = None if not bp else {"book": bp[0], "decimal": bp[1], "edge": p_side - 1 / bp[1]}
            # one book can't be judged against itself: flags need at least 3 books for a consensus
            if bp and len(pairs) >= 3 and p_side - 1 / bp[1] >= s.edge_prefight:
                out["flags"].append({"point": point, "side": name, "book": bp[0], "decimal": bp[1],
                                     "edge": p_side - 1 / bp[1]})
        out["totals"].append(line)
    out["outcomes"] = prop_outcomes(db, f, s, out)
    return out


def prop_label(outcome: str, f: Fight) -> str:
    names = {"a": f.a, "b": f.b}
    methods = {"ko": "KO/TKO", "sub": "submission", "dec": "decision"}
    if outcome == "distance":
        return "Goes the distance"
    if outcome == "finish":
        return "Doesn't go the distance"
    if outcome == "draw":
        return "Draw / no contest"
    if outcome.startswith("ends_r"):
        return f"Fight ends in round {outcome[6:]}"
    side, rest = outcome.split("_", 1)
    return f"{names[side]} in round {rest[1:]}" if rest.startswith("r") else f"{names[side]} by {methods[rest]}"


def prop_outcomes(db: Session, f: Fight, s: Settings, view: dict | None = None) -> list[dict]:
    """Every prop outcome with the engine's chance (Kalshi first, model fallback), both sources, and the
    worst price still worth taking at your book."""
    from .engine import props_model as PM

    view = view or {}
    kalshi = {"distance": (view.get("distance") or {}).get("p"),
              "method": {m["outcome"]: m["p"] for m in view.get("method", [])} or None}
    pred = latest(db, Prediction, f.id)
    model = (pred.details or {}).get("prop_model") if pred else None
    combined = PM.combine(kalshi, model, f.rounds)
    if "distance" in combined:
        p, src = combined["distance"]
        combined["finish"] = (1 - p, src)
    rows = []
    for outcome, (p, src) in combined.items():
        p_model = None
        if model:
            if outcome in model and not isinstance(model[outcome], (list, dict)):
                p_model = model[outcome]
            elif outcome == "finish":
                p_model = 1 - model["distance"]
            elif outcome.startswith("ends_r"):
                p_model = model["ends_in_round"][int(outcome[6:]) - 1]
            elif outcome[1:3] == "_r":
                p_model = model[f"{outcome[0]}_round"][int(outcome[3:]) - 1]
        rows.append({"outcome": outcome, "label": prop_label(outcome, f), "p": p, "source": src,
                     "p_kalshi": kalshi["distance"] if outcome == "distance" else (kalshi["method"] or {}).get(outcome),
                     "p_model": p_model, "fair_dec": 1 / p if p > 0.005 else None,
                     "bet_at_dec": bet_at_decimal(p, s.edge_prefight)})
    return rows


# ---------- bet plan ----------

def plan_options(db: Session, f: Fight, s: Settings) -> list:
    """Everything bettable on one fight, with the engine's chance and (when a feed book has it) a price."""
    from .engine.planner import Option

    pred = latest(db, Prediction, f.id)
    if not pred or pred.p_final is None:
        return []
    d = pred.details or {}
    sides, pricing = d.get("sides") or [], d.get("pricing") or {}
    out = []
    for i, name in enumerate((f.a, f.b)):
        sv = sides[i] if i < len(sides) else {}
        out.append(Option(f.id, f.key, "moneyline", str(i), f"{name} to win", pred.p_final if i == 0 else 1 - pred.p_final,
                          sv.get("decimal"), sv.get("book"), (pricing.get("bet_at_dec") or [None, None])[i],
                          sv.get("source") or ""))
    view = props_view(db, f, s)
    for line in view["totals"]:
        for side, p in (("over", line["fair_over"]), ("under", 1 - line["fair_over"])):
            best = line[side]
            out.append(Option(f.id, f.key, "total_rounds", f"{side} {line['point']}", f"{side.title()} {line['point']} rounds",
                              p, best["decimal"] if best else None, best["book"] if best else None,
                              bet_at_decimal(p, s.edge_prefight), "line shopping"))
    # props to check at your own book: the likelier of distance / finish, and the likeliest method
    outcomes = {o["outcome"]: o for o in view["outcomes"]}
    picks = []
    if "distance" in outcomes:
        picks.append(max((outcomes["distance"], outcomes["finish"]), key=lambda o: o["p"]))
    methods = [o for k, o in outcomes.items() if k[:2] in ("a_", "b_") and k[2:] in ("ko", "sub", "dec")]
    if methods:
        picks.append(max(methods, key=lambda o: o["p"]))
    for o in picks:
        out.append(Option(f.id, f.key, "prop", o["outcome"], o["label"], o["p"], None, None, o["bet_at_dec"], o["source"]))
    return out


def build_plan(db: Session, ev: Event, s: Settings, budget: float | None = None) -> dict:
    from .engine import planner

    options = [o for f in ev.fights if f.status == "scheduled" for o in plan_options(db, f, s)]
    plan = planner.build(options, budget if budget is not None else s.card_budget, s.edge_prefight, s.lean_edge)
    favourites = []
    for f in ev.fights:
        if f.status != "scheduled":
            continue
        ml = [o for o in options if o.fight_id == f.id and o.market == "moneyline"]
        if len(ml) == 2:
            fav = max(ml, key=lambda o: o.p)
            favourites.append({"fight_id": f.id, "fight": f.key, "label": fav.label, "p": fav.p, "decimal": fav.decimal,
                               "book": fav.book, "edge": None if not fav.decimal else fav.p - 1 / fav.decimal,
                               "fair_dec": 1 / fav.p})
    favourites.sort(key=lambda x: -x["p"])
    return {**plan.to_dict(), "favourites": favourites, "threshold": s.edge_prefight, "lean_edge": s.lean_edge,
            "home_book": s.home_book}


def prop_alerts(engine: AlertEngine, f: Fight, flags: list[dict]) -> list:
    """Entry alerts for total-rounds prices that beat the other books' consensus (deduped like value alerts)."""
    from .alerts import Alert, _am

    out = []
    for fl in flags:
        a = Alert("prop", f.key, f"VALUE PROP: {fl['side']} {fl['point']} rounds {_am(fl['decimal'])}",
                  f"{f.key}: {fl['side']} {fl['point']} rounds {_am(fl['decimal'])} at {fl['book']}\n"
                  f"Edge {fl['edge'] * 100:+.1f} pts over the other books' consensus (line shopping).",
                  False, key=f"prop|{f.key}|{fl['point']}|{fl['side']}", score=fl["edge"])
        if engine._should_send(a, time.time()):
            out.append(a)
    return out


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
        self.last_totals = 0.0

    def poll(self, db: Session, ev: Event, hist, pipe, with_odds: bool, targets: dict[str, float]) -> list[AlertRow]:
        s = effective_settings(db)  # re-read so changes from the Settings screen apply at once
        engine = AlertEngine(s.bankroll, s.kelly_fraction, s.max_stake_pct, s.edge_prefight,
                             s.edge_live, s.model_weight, usable_books=s.usable_books, home_book=s.home_book)
        engine.sent = self.sent
        card = card_of(ev)
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if with_odds and self.odds_client:
            # total rounds cost an extra credit per call, so they're fetched less often on event day
            with_totals = time.time() - self.last_totals >= s.totals_every
            try:
                self.odds = parse_odds(self.odds_client.fetch("h2h,totals" if with_totals else "h2h"), card, now_iso)
                if with_totals:
                    self.last_totals = time.time()
                    _store_totals(db, ev, self.odds)
            except BudgetExceeded as exc:
                log.warning("%s - prediction markets only from now on", exc)
                self.odds_client = None
            except httpx.HTTPError as exc:
                log.warning("Odds API error: %s", exc)
        if with_odds:
            try:
                _store_kalshi_props(db, ev, pm.fetch_kalshi_props(card, self.http))
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Kalshi props unavailable: %s", exc)
        if any(f.status == "live" for f in ev.fights):
            self.finish_settled(db, ev, card)
        pms, _ = pm.fetch_all(card, self.http)
        on = pd.Timestamp(ev.date)
        rows: list[AlertRow] = []
        for f in ev.fights:
            if f.status == "finished":
                continue
            pred = latest(db, Prediction, f.id)
            if f.id not in self.states:
                p_model = pred.p_model if pred and pred.p_model is not None else \
                    M.predict(pipe, hist.current(f.a, on), hist.current(f.b, on))
                self.states[f.id] = FightState(p_model, f.a, f.b)
            if pred and pred.details:
                self.states[f.id].apply_blend_inputs(pred.details.get("blend_inputs"))
            fo = self.odds.get(f.key) if with_odds else None
            lst = pms.get(f.key, [])
            _store_snapshots(db, f, fo, lst)
            if fo and fo.live and f.status == "scheduled":
                set_status(db, f, "live")
            pm_p = sum(m.p_a for m in lst) / len(lst) if lst else None
            books = (self.odds.get(f.key).books if self.odds.get(f.key) else {})
            alerts = engine.evaluate(f.key, self.states[f.id], books, pm_p, f.status == "live", targets)
            if with_odds and f.status == "scheduled":
                alerts += prop_alerts(engine, f, props_view(db, f, s)["flags"])
            for a in alerts:
                row = AlertRow(fight_id=f.id, kind=a.kind, title=a.title, body=a.body, urgent=a.urgent)
                db.add(row)
                rows.append(row)
                if a.kind in ("pre", "live") and a.decimal is not None:
                    record_paper_pick(db, f, f"alert-{a.kind}", a.side, a.book, a.decimal, a.p, a.p_market, a.source)
                self.notifier.send(a.title, a.body, urgent=a.urgent,
                                   tags="rotating_light" if a.urgent else "moneybag", path=f"/fight/{f.id}")
            if f.status == "live" and pm_p is not None:
                update_live_estimate(db, f, None, pm_p)
        db.commit()
        return rows

    def finish_settled(self, db: Session, ev: Event, card) -> None:
        """Mark live fights finished (winner and method) as soon as Kalshi settles them."""
        since = int(datetime.fromisoformat(ev.date).replace(tzinfo=timezone.utc).timestamp())
        try:
            results = pm.fetch_kalshi_results(card, self.http, since)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Kalshi results unavailable: %s", exc)
            return
        for f in ev.fights:
            r = results.get(f.key)
            if f.status != "live" or r is None:
                continue
            tracking.record_result(db, f, r["winner"], r["method"], source="kalshi")
            set_status(db, f, "finished", r["winner"])
            log.info("Finished from Kalshi: %s vs %s, winner %s by %s", f.a, f.b, r["winner"], r["method"])


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
    # p_pre is the market-blended p_final, so add round evidence to it rather than re-weighting it
    p_rounds = live_model.predict_from_prior(p_pre, rounds, f.rounds) if live_model else p_pre
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

def latest_books(db: Session, f: Fight) -> dict[str, tuple[float, float]]:
    """Sportsbook prices from the most recent Odds API poll for this fight."""
    latest_ts = db.scalar(select(func.max(OddsSnapshot.ts)).where(OddsSnapshot.fight_id == f.id,
                                                                 OddsSnapshot.source == "odds_api"))
    if latest_ts is None:
        return {}
    snaps = db.scalars(select(OddsSnapshot).where(OddsSnapshot.fight_id == f.id, OddsSnapshot.source == "odds_api",
                                                  OddsSnapshot.ts == latest_ts)).all()
    return {s.book: (s.dec_a, s.dec_b) for s in snaps}


def best_current_price(db: Session, f: Fight, side: int, book: str | None = None) -> float | None:
    books = latest_books(db, f)
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
        books = latest_books(db, f)
        fair = consensus_fair_prob(books) if books else None
        if fair is not None:
            for pp in db.scalars(select(PaperPick).where(PaperPick.fight_id == f.id, PaperPick.close_fair.is_(None),
                                                         PaperPick.kind != "alert-live")):
                pp.close_fair = fair if pp.side == 0 else 1 - fair
        tracking.freeze(db, f)
    f.status = status
    if status == "finished":
        if f.started_at is None:  # finished without being marked live: freeze what the engine said
            tracking.freeze(db, f)
        f.winner = winner
        for b in db.scalars(select(Bet).where(Bet.fight_id == f.id, Bet.result == "open")):
            settle_bet(b, winner)
        for pp in db.scalars(select(PaperPick).where(PaperPick.fight_id == f.id, PaperPick.result == "open")):
            pp.result = "void" if winner is None else "won" if winner == pp.side else "lost"
        if db.get(FightResult, f.id) is None:
            tracking.record_result(db, f, winner, source="app")
    db.commit()


def record_paper_pick(db: Session, f: Fight, kind: str, side: int, book: str, decimal: float, p_ours: float,
                      p_market: float | None, source: str) -> PaperPick | None:
    """Store a pick or value alert once per price; the same price seen again on a later poll is skipped."""
    for pp in db.scalars(select(PaperPick).where(PaperPick.fight_id == f.id, PaperPick.kind == kind,
                                                 PaperPick.side == side, PaperPick.book == (book or ""))):
        if abs(pp.decimal - decimal) < 0.01:
            return None
    pp = PaperPick(fight_id=f.id, kind=kind, side=side, book=book or "", decimal=decimal, p_ours=p_ours,
                   p_market=p_market, source=source)
    db.add(pp)
    return pp


def paper_clv(pp: PaperPick) -> float | None:
    """Expected return of the pick's price at the de-vigged closing probability (>0 = beat the close)."""
    return None if pp.close_fair is None else pp.decimal * pp.close_fair - 1


def paper_summary(db: Session) -> list[dict]:
    """Closing-line value and flat-stake results of every paper pick, by where the edge came from."""
    rows = db.scalars(select(PaperPick)).all()
    out = []
    for source in sorted({r.source for r in rows}):
        group = [r for r in rows if r.source == source]
        clvs = [c for c in (paper_clv(r) for r in group) if c is not None]
        settled = [r for r in group if r.result in ("won", "lost")]
        profit = sum(r.decimal - 1 if r.result == "won" else -1.0 for r in settled)
        out.append({"source": source, "picks": len(group), "clv_n": len(clvs),
                    "avg_clv": round(sum(clvs) / len(clvs), 4) if clvs else None,
                    "beat_close": round(sum(c > 0 for c in clvs) / len(clvs), 3) if clvs else None,
                    "settled": len(settled), "flat_roi": round(profit / len(settled), 4) if settled else None})
    return out


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
