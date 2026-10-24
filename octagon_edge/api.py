"""HTTP API for the PWA (FastAPI). Run: uvicorn octagon_edge.api:app

Protect it with APP_TOKEN when exposed through a tunnel: the app sends it as a
Bearer token (or ?token= for the live event stream).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Iterator

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import services as S
from .config import ROOT
from .db import (AlertRow, Bet, Event, Fight, LiveEstimate, LiveRound, OddsSnapshot, Prediction, PushSubscription,
                 SocialPost, latest, put_setting, sessions)
from .engine.live import RoundInput
from .engine.odds_math import american_to_decimal, consensus_fair_prob, decimal_to_american

log = logging.getLogger(__name__)
app = FastAPI(title="Octagon Edge", version="0.2.0")
APP_TOKEN = os.environ.get("APP_TOKEN", "")
DIST = Path(os.environ.get("FRONTEND_DIST", ROOT / "frontend" / "dist"))

_models_lock = threading.Lock()
_models: dict = {}


def models():
    """History + pre-fight model + live model, loaded once on first use (takes a few seconds)."""
    with _models_lock:
        if not _models:
            from .pipeline import load_history, load_live_model, load_model

            hist = load_history()
            pipe = load_model(hist)
            _models.update(hist=hist, pipe=pipe, live=load_live_model(hist, pipe))
    return _models


def get_db() -> Iterator[Session]:
    db = sessions()()
    try:
        yield db
    finally:
        db.close()


def auth(request: Request) -> None:
    if not APP_TOKEN:
        return
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token", "")
    if not secrets.compare_digest(supplied, APP_TOKEN):
        raise HTTPException(401, "Missing or wrong app token (set it on the Settings screen)")


api = Depends(auth)


# ---------- helpers ----------

def _pm_latest(db: Session, f: Fight) -> dict:
    out = {}
    for src in ("Kalshi", "Polymarket"):
        row = db.scalars(select(OddsSnapshot).where(OddsSnapshot.fight_id == f.id, OddsSnapshot.source == src)
                         .order_by(OddsSnapshot.id.desc()).limit(1)).first()
        if row:
            out[src] = {"p_a": row.p_a, "ts": row.ts.isoformat()}
    return out


def _prices_ts(db: Session, f: Fight) -> str | None:
    ts = db.scalar(select(func.max(OddsSnapshot.ts)).where(OddsSnapshot.fight_id == f.id,
                                                           OddsSnapshot.source == "odds_api"))
    return ts.isoformat() if ts else None


def fight_summary(db: Session, f: Fight, s=None, targets: dict | None = None) -> dict:
    pred = latest(db, Prediction, f.id)
    est = latest(db, LiveEstimate, f.id)
    s = s or S.effective_settings(db)
    targets = S.db_targets(db) if targets is None else targets
    details = pred.details if pred else {}
    props = S.props_view(db, f, s)
    return {
        "sides": details.get("sides"), "pricing": details.get("pricing"),
        "prices_ts": _prices_ts(db, f),
        "targets": [targets.get(f.a), targets.get(f.b)],
        "prop_flags": props["flags"],
        "id": f.id, "a": f.a, "b": f.b, "weightclass": f.weightclass, "segment": f.segment, "rounds": f.rounds,
        "title": f.title, "status": f.status, "winner": f.winner, "order": f.order,
        "record_a": pred.details.get("record_a") if pred else None,
        "record_b": pred.details.get("record_b") if pred else None,
        "p_model": pred.p_model if pred else None, "p_market": pred.p_market if pred else None,
        "p_final": pred.p_final if pred else None, "market_source": pred.market_source if pred else "",
        "confidence": pred.confidence if pred else None,
        "pick": None if not pred or pred.pick_side is None else {
            "side": pred.pick_side, "book": pred.pick_book, "decimal": pred.pick_decimal,
            "edge": pred.pick_edge, "stake": pred.stake},
        "prediction_ts": pred.ts.isoformat() if pred else None,
        "prediction_markets": _pm_latest(db, f),
        "live": None if not est else {"p_live": est.p_live, "after_round": est.after_round,
                                      "p_rounds": est.p_rounds, "p_market": est.p_market},
    }


def _fight(db: Session, fight_id: int) -> Fight:
    f = db.get(Fight, fight_id)
    if not f:
        raise HTTPException(404, "Fight not found")
    return f


# ---------- events & fights ----------

@app.get("/api/health")
def health():
    return {"ok": True, "auth": bool(APP_TOKEN)}


@app.get("/api/events", dependencies=[api])
def events(db: Session = Depends(get_db)):
    rows = db.scalars(select(Event).order_by(Event.date.desc())).all()
    return [{"slug": e.slug, "name": e.name, "date": e.date, "venue": e.venue, "fights": len(e.fights)} for e in rows]


@app.get("/api/events/{slug}", dependencies=[api])
def event(slug: str, db: Session = Depends(get_db)):
    ev = db.scalars(select(Event).where(Event.slug == slug)).first()
    if not ev:
        raise HTTPException(404, "Event not found")
    preds = [latest(db, Prediction, f.id) for f in ev.fights]
    sources = next((p.details.get("sources") for p in preds if p and p.details.get("sources")), {})
    s, targets = S.effective_settings(db), S.db_targets(db)
    return {"slug": ev.slug, "name": ev.name, "date": ev.date, "venue": ev.venue, "note": ev.note,
            "sources": sources, "home_book": s.home_book,
            "fights": [fight_summary(db, f, s, targets) for f in ev.fights]}


@app.get("/api/events/{slug}/parlays", dependencies=[api])
def parlays(slug: str, legs: str = "2,3", limit: int = 15, db: Session = Depends(get_db)):
    from .engine.parlay import build_legs, generate_parlays

    ev = db.scalars(select(Event).where(Event.slug == slug)).first()
    if not ev:
        raise HTTPException(404, "Event not found")
    s = S.effective_settings(db)
    rows = []
    for f in ev.fights:
        if f.status == "finished":
            continue
        pred = latest(db, Prediction, f.id)
        if not pred:
            continue
        rows.append({"id": f.id, "a": f.a, "b": f.b, "p_final": pred.p_final, "sides": pred.details.get("sides"),
                     "pick_side": pred.pick_side})
    sizes = tuple(int(n) for n in legs.split(",") if n.strip())
    # Legs are only today's single-bet picks; a parlay multiplies any error in our estimate,
    # so its stake cap is a quarter of a single bet's.
    combos = generate_parlays(build_legs(rows, picks_only=True), sizes, s.bankroll, s.kelly_fraction,
                              s.max_stake_pct / 4, limit)
    return [{
        "decimal": c.decimal, "american": decimal_to_american(c.decimal),
        "p": c.p, "edge": c.edge, "ev": c.ev, "stake": c.stake, "stake_pct": c.stake_pct,
        "legs": [{"fight_id": l.fight_id, "fight": l.fight_key, "name": l.name, "side": l.side,
                  "p": l.p, "decimal": l.decimal, "book": l.book, "single_edge": l.single_edge} for l in c.legs],
    } for c in combos]


@app.post("/api/events/{slug}/refresh", dependencies=[api])
def refresh(slug: str, tasks: BackgroundTasks, news: bool = True, llm: bool = True, db: Session = Depends(get_db)):
    ev = S.sync_card(db, slug)

    def run():
        from .ingest.odds_api import OddsApiClient

        with sessions()() as s2:
            st = S.effective_settings(s2)
            client = OddsApiClient(st.odds_api_key, st.odds_api_regions, st.odds_api_budget) if st.odds_api_key else None
            m = models()
            S.refresh_analysis(s2, s2.get(Event, ev.id), m["hist"], m["pipe"], client, with_news=news, with_llm=llm)

    tasks.add_task(run)
    return {"queued": True, "event": slug}


@app.get("/api/fights/{fight_id}", dependencies=[api])
def fight(fight_id: int, db: Session = Depends(get_db)):
    f = _fight(db, fight_id)
    pred = latest(db, Prediction, f.id)
    return {**fight_summary(db, f), "event": f.event.slug, "details": pred.details if pred else {}}


@app.get("/api/fights/{fight_id}/props", dependencies=[api])
def props(fight_id: int, db: Session = Depends(get_db)):
    """Kalshi prop fair prices and sportsbook total-rounds line shopping."""
    return S.props_view(db, _fight(db, fight_id), S.effective_settings(db))


class TargetBody(BaseModel):
    side: int = Field(ge=0, le=1)
    american: float | None = None  # None clears the target


@app.put("/api/fights/{fight_id}/target", dependencies=[api])
def set_target(fight_id: int, body: TargetBody, db: Session = Depends(get_db)):
    """Alert when a tracked book offers this price (or better) on one fighter."""
    from .db import get_setting

    f = _fight(db, fight_id)
    name = f.a if body.side == 0 else f.b
    targets = dict(get_setting(db, "targets", {}) or {})
    if body.american is None:
        targets.pop(name, None)
    else:
        if abs(body.american) < 100:
            raise HTTPException(400, "US odds are +100 or more, or -100 or less")
        targets[name] = american_to_decimal(body.american)
    put_setting(db, "targets", targets)
    db.commit()
    return {"targets": [targets.get(f.a), targets.get(f.b)]}


@app.get("/api/books", dependencies=[api])
def books(db: Session = Depends(get_db)):
    """Sportsbooks seen in the price feed, and which ones you can bet at."""
    s = S.effective_settings(db)
    seen = sorted(set(db.scalars(select(OddsSnapshot.book).where(OddsSnapshot.source == "odds_api",
                                                               OddsSnapshot.book.is_not(None))).all()))
    return {"seen": seen, "usable": list(s.usable_books), "home": s.home_book,
            "home_in_feed": s.home_book in seen}


class BooksBody(BaseModel):
    home: str | None = Field(default=None, max_length=60)
    usable: list[str] | None = None  # [] = all books


@app.put("/api/books", dependencies=[api])
def put_books(body: BooksBody, db: Session = Depends(get_db)):
    from .db import get_setting

    current = dict(get_setting(db, "books", {}) or {})
    if body.home is not None:
        current["home"] = body.home.strip()
    if body.usable is not None:
        current["usable"] = [b for b in body.usable if b]
    put_setting(db, "books", current)
    db.commit()
    return books(db)


@app.get("/api/fights/{fight_id}/odds", dependencies=[api])
def odds_history(fight_id: int, db: Session = Depends(get_db)):
    """Time series of market probability for fighter A, per source."""
    f = _fight(db, fight_id)
    snaps = db.scalars(select(OddsSnapshot).where(OddsSnapshot.fight_id == f.id).order_by(OddsSnapshot.ts)).all()
    books: dict[str, dict] = {}
    series: dict[str, list] = {"Sportsbooks": [], "Kalshi": [], "Polymarket": []}
    by_ts: dict[str, dict] = {}
    for s in snaps:
        if s.source == "odds_api":
            by_ts.setdefault(s.ts.isoformat(), {})[s.book] = (s.dec_a, s.dec_b)
            books[s.book] = {"a": s.dec_a, "b": s.dec_b, "ts": s.ts.isoformat()}
        elif s.source in series and s.p_a is not None:
            series[s.source].append({"ts": s.ts.isoformat(), "p_a": s.p_a})
    for ts, bk in sorted(by_ts.items()):
        fair = consensus_fair_prob(bk)
        if fair is not None:
            series["Sportsbooks"].append({"ts": ts, "p_a": fair})
    return {"series": {k: v for k, v in series.items() if v}, "books": books}


@app.get("/api/fights/{fight_id}/social", dependencies=[api])
def social(fight_id: int, db: Session = Depends(get_db)):
    from .sentiment import aggregate

    f = _fight(db, fight_id)
    out = {}
    for side, name in (("a", f.a), ("b", f.b)):
        posts = db.scalars(select(SocialPost).where(SocialPost.fighter == name).order_by(SocialPost.id.desc())).all()
        scored = [p for p in posts if p.sentiment is not None]
        out[side] = {
            "name": name,
            "summary": aggregate([p.sentiment for p in scored], [p.source for p in scored]),
            "flags": sorted({fl for p in posts for fl in (p.flags or [])}),
            "posts": [{"source": p.source, "title": p.title, "link": p.link, "flags": p.flags,
                       "sentiment": p.sentiment, "ts": p.ts.isoformat()} for p in posts[:25]],
        }
    return out


# ---------- live mode ----------

class RoundBody(BaseModel):
    round: int = Field(ge=1, le=5)
    winner: int | None = Field(default=None, ge=0, le=1)
    kd_a: int = 0
    kd_b: int = 0
    td_a: int = 0
    td_b: int = 0
    sig_a: int | None = None
    sig_b: int | None = None
    ctrl_a: int = 0
    ctrl_b: int = 0


@app.get("/api/fights/{fight_id}/live", dependencies=[api])
def live_state(fight_id: int, db: Session = Depends(get_db)):
    f = _fight(db, fight_id)
    rounds = db.scalars(select(LiveRound).where(LiveRound.fight_id == f.id).order_by(LiveRound.round)).all()
    ests = db.scalars(select(LiveEstimate).where(LiveEstimate.fight_id == f.id).order_by(LiveEstimate.id)).all()
    return {
        "status": f.status, "winner": f.winner,
        "rounds": [{k: getattr(r, k) for k in RoundBody.model_fields} for r in rounds],
        "estimates": [{"ts": e.ts.isoformat(), "after_round": e.after_round, "p_live": e.p_live,
                       "p_rounds": e.p_rounds, "p_market": e.p_market} for e in ests[-200:]],
    }


@app.post("/api/fights/{fight_id}/rounds", dependencies=[api])
def add_round(fight_id: int, body: RoundBody, db: Session = Depends(get_db)):
    f = _fight(db, fight_id)
    if body.round > f.rounds:
        raise HTTPException(400, f"This is a {f.rounds}-round fight")
    data = RoundInput(**body.model_dump(exclude={"round"}))
    est = S.record_round(db, f, body.round, data, models()["live"])
    return {"p_live": est.p_live, "p_rounds": est.p_rounds, "p_market": est.p_market, "after_round": est.after_round}


@app.delete("/api/fights/{fight_id}/rounds/{rnd}", dependencies=[api])
def delete_round(fight_id: int, rnd: int, db: Session = Depends(get_db)):
    f = _fight(db, fight_id)
    row = db.scalars(select(LiveRound).where(LiveRound.fight_id == f.id, LiveRound.round == rnd)).first()
    if row:
        db.delete(row)
        db.commit()
    est = S.update_live_estimate(db, f, models()["live"])
    return {"p_live": est.p_live}


class StatusBody(BaseModel):
    status: str = Field(pattern="^(scheduled|live|finished)$")
    winner: int | None = Field(default=None, ge=0, le=1)


@app.post("/api/fights/{fight_id}/status", dependencies=[api])
def fight_status(fight_id: int, body: StatusBody, db: Session = Depends(get_db)):
    f = _fight(db, fight_id)
    S.set_status(db, f, body.status, body.winner)
    return fight_summary(db, f)


# ---------- alerts & live stream ----------

def _alert_json(a: AlertRow) -> dict:
    return {"id": a.id, "fight_id": a.fight_id, "ts": a.ts.isoformat(), "kind": a.kind, "title": a.title,
            "body": a.body, "urgent": a.urgent}


@app.get("/api/alerts", dependencies=[api])
def alerts(since: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    rows = db.scalars(select(AlertRow).where(AlertRow.id > since).order_by(AlertRow.id.desc()).limit(limit)).all()
    return [_alert_json(a) for a in rows]


@app.get("/api/stream", dependencies=[api])
async def stream(request: Request, since: int = 0):
    """Server-sent events: new alerts and live probability updates (polls the DB every 2s)."""
    async def gen():
        last_alert = since
        with sessions()() as db:
            if not since:  # fresh connection: only push alerts raised from now on
                last_alert = db.scalar(select(AlertRow.id).order_by(AlertRow.id.desc()).limit(1)) or 0
            last_est = db.scalar(select(LiveEstimate.id).order_by(LiveEstimate.id.desc()).limit(1)) or 0
        ticks = 0
        while not await request.is_disconnected():
            with sessions()() as db:
                for a in db.scalars(select(AlertRow).where(AlertRow.id > last_alert).order_by(AlertRow.id)):
                    last_alert = a.id
                    yield f"event: alert\ndata: {json.dumps(_alert_json(a))}\n\n"
                for e in db.scalars(select(LiveEstimate).where(LiveEstimate.id > last_est).order_by(LiveEstimate.id)):
                    last_est = e.id
                    yield ("event: live\ndata: " + json.dumps({"fight_id": e.fight_id, "p_live": e.p_live,
                           "after_round": e.after_round, "ts": e.ts.isoformat()}) + "\n\n")
            ticks += 1
            if ticks % 8 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- bets ----------

class BetBody(BaseModel):
    fight_id: int
    side: int = Field(ge=0, le=1)
    book: str = ""
    american: float | None = None
    decimal: float | None = Field(default=None, gt=1)
    stake: float = Field(gt=0)
    live: bool = False
    note: str = ""


def _bet_json(b: Bet, f: Fight) -> dict:
    return {"id": b.id, "fight_id": b.fight_id, "fight": f.key, "pick": f.a if b.side == 0 else f.b,
            "side": b.side, "book": b.book, "decimal": b.decimal, "stake": b.stake, "live": b.live,
            "placed_at": b.placed_at.isoformat(), "closing_decimal": b.closing_decimal, "clv": S.clv(b),
            "result": b.result, "pnl": b.pnl, "note": b.note}


@app.get("/api/bets", dependencies=[api])
def bets(db: Session = Depends(get_db)):
    rows = db.scalars(select(Bet).order_by(Bet.id.desc())).all()
    return {"summary": S.bet_summary(db), "bets": [_bet_json(b, db.get(Fight, b.fight_id)) for b in rows],
            "paper": S.paper_summary(db)}


@app.post("/api/bets", dependencies=[api])
def add_bet(body: BetBody, db: Session = Depends(get_db)):
    f = _fight(db, body.fight_id)
    dec = body.decimal or (american_to_decimal(body.american) if body.american else None)
    if not dec:
        raise HTTPException(400, "Give the price as american or decimal odds")
    b = Bet(fight_id=f.id, side=body.side, book=body.book, decimal=dec, stake=body.stake,
            live=body.live or f.status == "live", note=body.note)
    db.add(b)
    db.commit()
    return _bet_json(b, f)


class BetPatch(BaseModel):
    result: str | None = Field(default=None, pattern="^(open|won|lost|void)$")
    closing_decimal: float | None = Field(default=None, gt=1)


@app.patch("/api/bets/{bet_id}", dependencies=[api])
def patch_bet(bet_id: int, body: BetPatch, db: Session = Depends(get_db)):
    b = db.get(Bet, bet_id)
    if not b:
        raise HTTPException(404, "Bet not found")
    if body.closing_decimal:
        b.closing_decimal = body.closing_decimal
    if body.result:
        if body.result == "open":
            b.result, b.pnl = "open", None
        else:
            S.settle_bet(b, None if body.result == "void" else (b.side if body.result == "won" else 1 - b.side))
    db.commit()
    return _bet_json(b, db.get(Fight, b.fight_id))


@app.delete("/api/bets/{bet_id}", dependencies=[api])
def delete_bet(bet_id: int, db: Session = Depends(get_db)):
    b = db.get(Bet, bet_id)
    if b:
        db.delete(b)
        db.commit()
    return {"deleted": bool(b)}


# ---------- settings & push ----------

@app.get("/api/settings", dependencies=[api])
def get_settings(db: Session = Depends(get_db)):
    from .notify import Notifier
    from .webpush import ensure_keys

    s = S.effective_settings(db)
    return {"tuning": {k: getattr(s, k) for k in S.TUNABLE},
            "channels": Notifier(s).channels + (["webpush"] if db.scalars(select(PushSubscription)).first() else []),
            "llm_provider": s.llm_provider, "odds_api": bool(s.odds_api_key), "vapid_public_key": ensure_keys(),
            "home_book": s.home_book,
            "active_card": os.environ.get("ACTIVE_CARD", "ufc-333")}


class TuningBody(BaseModel):
    bankroll: float | None = Field(default=None, gt=0)
    kelly_fraction: float | None = Field(default=None, gt=0, le=1)
    max_stake_pct: float | None = Field(default=None, gt=0, le=0.25)
    edge_prefight: float | None = Field(default=None, ge=0, le=0.5)
    edge_live: float | None = Field(default=None, ge=0, le=0.5)
    model_weight: float | None = Field(default=None, ge=0, le=1)


@app.put("/api/settings", dependencies=[api])
def put_settings(body: TuningBody, db: Session = Depends(get_db)):
    from .db import get_setting

    current = get_setting(db, "tuning", {}) or {}
    current.update({k: v for k, v in body.model_dump().items() if v is not None})
    put_setting(db, "tuning", current)
    db.commit()
    return get_settings(db)


class PushBody(BaseModel):
    endpoint: str
    keys: dict[str, str]


@app.post("/api/push/subscribe", dependencies=[api])
def subscribe(body: PushBody, db: Session = Depends(get_db)):
    if not body.endpoint.startswith("https://") or not {"p256dh", "auth"} <= body.keys.keys():
        raise HTTPException(400, "Invalid push subscription")
    row = db.scalars(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)).first()
    if not row:
        db.add(PushSubscription(endpoint=body.endpoint, p256dh=body.keys["p256dh"], auth=body.keys["auth"]))
        db.commit()
    return {"subscribed": True}


@app.post("/api/push/test", dependencies=[api])
def push_test(db: Session = Depends(get_db)):
    from .notify import Notifier
    from .webpush import send_all

    s = S.effective_settings(db)
    n = Notifier(s, webpush=lambda t, b, p, u: send_all(db, t, b, p, u))
    return {"delivered": n.send("Octagon Edge test", "Alerts reach this device.", urgent=True, path="/settings")}


# ---------- PWA ----------

if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        file = (DIST / path).resolve()
        if path and file.is_file() and DIST.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(DIST / "index.html")
