"""Weekly tracking: freeze forecasts and the bet plan at the start of each fight, settle them
with the result, and score them, so the engine can be checked (and tuned) every event.

- At fight start: every forecast (moneyline, distance, method, round) and every plan bet.
- Results: from ufcstats via the daily data refresh (winner, method, round, time) or
  entered in the app (winner, optionally method and round).
- Scorecard: log loss / Brier for the engine vs the market vs the model per market, and
  profit for the plan's value, lean and check tiers.
"""
from __future__ import annotations

import math

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Event, Fight, FightResult, Forecast, PlanPick, Prediction, latest, utcnow
from .ingest.names import same_fighter


def _market_of(outcome: str) -> str:
    if outcome in ("distance", "finish"):
        return "distance"
    if outcome.startswith("ends_r") or outcome[1:3] == "_r":
        return "round"
    return "method"


def freeze(db: Session, f: Fight) -> None:
    """Record forecasts and the plan's bets for this fight as they stand at the start."""
    from . import services as S

    if db.scalars(select(Forecast).where(Forecast.fight_id == f.id)).first():
        return
    s = S.effective_settings(db)
    pred = latest(db, Prediction, f.id)
    if pred and pred.p_final is not None:
        db.add(Forecast(fight_id=f.id, market="moneyline", outcome="a", p_ours=pred.p_final, p_market=pred.p_market,
                        p_model=pred.p_model))
    for o in S.prop_outcomes(db, f, s, S.props_view(db, f, s)):
        db.add(Forecast(fight_id=f.id, market=_market_of(o["outcome"]), outcome=o["outcome"], p_ours=o["p"],
                        p_market=o["p_kalshi"], p_model=o["p_model"]))
    plan = S.build_plan(db, f.event, s)
    for b in plan["bets"] + plan["checks"]:
        if b["fight_id"] == f.id:
            db.add(PlanPick(fight_id=f.id, tier=b["tier"], market=b["market"], outcome=b["outcome"], label=b["label"],
                            p=b["p"], decimal=b["decimal"], book=b["book"], bet_at=b["bet_at"], stake=b["stake"]))


def outcome_won(market: str, outcome: str, r: FightResult, scheduled: int) -> bool | None:
    """Did this outcome happen? None when the result doesn't say (e.g. no method entered)."""
    if market == "moneyline":
        side = 0 if outcome in ("a", "0") else 1
        return None if r.winner is None else r.winner == side
    if outcome == "draw":
        return r.winner is None
    if r.method is None:
        return None
    went_distance = r.method == "DEC"
    if market == "total_rounds":
        side, point = outcome.split()
        point = float(point)
        if went_distance:
            lasted = scheduled * 300
        elif r.seconds is not None:
            lasted = r.seconds
        elif r.round is not None and r.round != math.ceil(point + 0.5):  # finish clearly before/after the line
            lasted = r.round * 300 if r.round > point else (r.round - 1) * 300
        else:
            return None
        over = lasted > point * 300
        return over if side == "over" else not over
    if outcome == "distance":
        return went_distance
    if outcome == "finish":
        return not went_distance
    if outcome.startswith("ends_r"):
        return None if r.round is None else (not went_distance and r.round == int(outcome[6:]))
    side = 0 if outcome[0] == "a" else 1
    if r.winner is None:
        return False
    if outcome[1:3] == "_r":
        return None if r.round is None else (r.winner == side and not went_distance and r.round == int(outcome[3:]))
    method = {"ko": "KO", "sub": "SUB", "dec": "DEC"}[outcome[2:]]
    return r.winner == side and r.method == method


def record_result(db: Session, f: Fight, winner: int | None, method: str | None = None, round_: int | None = None,
                  seconds: int | None = None, source: str = "app") -> None:
    row = db.get(FightResult, f.id) or FightResult(fight_id=f.id)
    row.winner, row.source, row.ts = winner, source, utcnow()
    row.method, row.round = method or row.method, round_ or row.round
    row.seconds = seconds if seconds is not None else row.seconds
    db.merge(row)
    db.flush()
    settle(db, f)


def settle(db: Session, f: Fight) -> None:
    r = db.get(FightResult, f.id)
    if not r:
        return
    for pick in db.scalars(select(PlanPick).where(PlanPick.fight_id == f.id)):
        won = outcome_won(pick.market, pick.outcome, r, f.rounds)
        pick.result = "open" if won is None else ("won" if won else "lost")
        if pick.market == "moneyline" and r.winner is None:
            pick.result = "void"


def settle_from_history(db: Session, ev: Event, fights: pd.DataFrame | None = None) -> int:
    """Fill in results for this card's fights from the ufcstats data (refreshed daily)."""
    from . import services as S
    from .ingest import greco

    if fights is None:
        fights = greco.load_fights(greco.download())
    day = pd.Timestamp(ev.date)
    rows = fights[(fights["date"] >= day - pd.Timedelta(days=1)) & (fights["date"] <= day + pd.Timedelta(days=1))]
    count = 0
    for f in ev.fights:
        for r in rows.itertuples():
            a_is_a = same_fighter(f.a, r.fighter_a) and same_fighter(f.b, r.fighter_b)
            a_is_b = same_fighter(f.a, r.fighter_b) and same_fighter(f.b, r.fighter_a)
            if not (a_is_a or a_is_b):
                continue
            winner = None if r.result in ("D", "NC") else (0 if (r.result == "A") == a_is_a else 1)
            method = r.method if r.method in ("KO", "SUB", "DEC") else None
            rnd = int(math.ceil(r.duration_sec / 300)) if r.duration_sec else None
            existing = db.get(FightResult, f.id)
            if existing and existing.source == "ufcstats":
                break
            record_result(db, f, winner, method, rnd, int(r.duration_sec), source="ufcstats")
            if f.status != "finished":
                S.set_status(db, f, "finished", winner)
            count += 1
            break
    db.commit()
    return count


def _ll(p: float, y: bool) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -math.log(p if y else 1 - p)


def scorecard(db: Session) -> dict:
    """How the engine's forecasts and plan did, overall and per event."""
    results = {r.fight_id: r for r in db.scalars(select(FightResult))}
    fights = {f.id: f for f in db.scalars(select(Fight).where(Fight.id.in_(results)))} if results else {}
    markets: dict[str, dict] = {}
    for fc in db.scalars(select(Forecast).where(Forecast.fight_id.in_(results))) if results else []:
        f = fights[fc.fight_id]
        won = outcome_won("moneyline" if fc.market == "moneyline" else "prop", fc.outcome, results[fc.fight_id], f.rounds)
        if won is None:
            continue
        m = markets.setdefault(fc.market, {"n": 0, "ours": 0.0, "market": [0.0, 0], "model": [0.0, 0], "brier": 0.0})
        m["n"] += 1
        m["ours"] += _ll(fc.p_ours, won)
        m["brier"] += (fc.p_ours - won) ** 2
        for key, p in (("market", fc.p_market), ("model", fc.p_model)):
            if p is not None:
                m[key][0] += _ll(p, won)
                m[key][1] += 1
    market_rows = [{"market": k, "n": v["n"], "log_loss": v["ours"] / v["n"], "brier": v["brier"] / v["n"],
                    "market_log_loss": v["market"][0] / v["market"][1] if v["market"][1] else None,
                    "model_log_loss": v["model"][0] / v["model"][1] if v["model"][1] else None}
                   for k, v in sorted(markets.items())]

    tiers: dict[str, dict] = {}
    events: dict[str, dict] = {}
    for pick in db.scalars(select(PlanPick)):
        t = tiers.setdefault(pick.tier, {"picks": 0, "settled": 0, "won": 0, "staked": 0.0, "profit": 0.0})
        t["picks"] += 1
        if pick.result not in ("won", "lost"):
            continue
        price = pick.decimal or pick.bet_at  # checks are scored at the worst price worth taking
        if not price:
            continue
        t["settled"] += 1
        t["won"] += pick.result == "won"
        t["staked"] += pick.stake
        gain = pick.stake * (price - 1) if pick.result == "won" else -pick.stake
        t["profit"] += gain
        ev_name = db.get(Fight, pick.fight_id).event.name
        e = events.setdefault(ev_name, {"event": ev_name, "settled": 0, "staked": 0.0, "profit": 0.0})
        e["settled"] += 1
        e["staked"] += pick.stake
        e["profit"] += gain
    for t in tiers.values():
        t["roi"] = t["profit"] / t["staked"] if t["staked"] else None
    return {"markets": market_rows, "tiers": tiers, "events": list(events.values()),
            "settled_fights": len(results)}
