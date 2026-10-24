from datetime import datetime, timezone

import pytest

from octagon_edge import services as S
from octagon_edge.alerts import AlertEngine, FightState
from octagon_edge.db import OddsSnapshot, PaperPick, sessions, utcnow
from octagon_edge.engine.odds_math import american_to_decimal as dec
from octagon_edge.worker import odds_interval


@pytest.fixture
def db(tmp_path):
    with sessions(f"sqlite:///{tmp_path / 'paper.db'}")() as session:
        yield session


def test_paper_pick_is_scored_against_the_devigged_close_and_settled(db):
    ev = S.sync_card(db, "ufc-333")
    f = ev.fights[0]
    assert S.record_paper_pick(db, f, "pick", 0, "BookA", 2.2, 0.50, 0.45, "line shopping")
    # the same price seen again on the next refresh is not a new pick
    assert S.record_paper_pick(db, f, "pick", 0, "BookA", 2.2, 0.51, 0.45, "line shopping") is None
    now = utcnow()
    db.add(OddsSnapshot(fight_id=f.id, ts=now, source="odds_api", book="BookA", dec_a=1.95, dec_b=1.95))
    db.add(OddsSnapshot(fight_id=f.id, ts=now, source="odds_api", book="BookB", dec_a=1.90, dec_b=2.00))
    db.commit()
    S.set_status(db, f, "live")
    pp = db.query(PaperPick).one()
    assert 0.48 < pp.close_fair < 0.52
    assert S.paper_clv(pp) == pytest.approx(2.2 * pp.close_fair - 1)  # took 2.2 on a ~50% close: big CLV
    S.set_status(db, f, "finished", winner=0)
    summary = S.paper_summary(db)
    assert summary == [{"source": "line shopping", "picks": 1, "clv_n": 1, "avg_clv": pytest.approx(S.paper_clv(pp), abs=1e-4),
                        "beat_close": 1.0, "settled": 1, "flat_roi": pytest.approx(1.2)}]


def test_value_alert_carries_what_a_paper_pick_needs():
    e = AlertEngine(1000, 0.25, 0.03, edge_pre=0.04, edge_live=0.06, model_weight=0.35)
    st = FightState(p_model=0.70, a="A", b="B")
    alerts = [a for a in e.evaluate("A vs B", st, {"Book": (dec(120), dec(-140))}, None, False, {}, now=1)
              if a.kind == "pre"]
    a = alerts[0]
    assert a.side == 0 and a.book == "Book" and a.decimal == dec(120)
    assert a.p > a.p_market and a.source == "model"  # the market alone says no value here


def test_fight_week_polls_odds_more_often():
    assert odds_interval(datetime(2026, 10, 24, 15, tzinfo=timezone.utc), "2026-10-24", True) == 240
    assert odds_interval(datetime(2026, 10, 20, tzinfo=timezone.utc), "2026-10-24", False) == 3 * 3600
    assert odds_interval(datetime(2026, 10, 1, tzinfo=timezone.utc), "2026-10-24", False) == 12 * 3600
