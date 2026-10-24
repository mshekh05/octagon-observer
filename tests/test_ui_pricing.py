"""Price-first UI backend: bet-at prices, your books, price alerts, parlays from picks, props."""
import pytest
from fastapi.testclient import TestClient

from octagon_edge import api as api_mod
from octagon_edge import services as S
from octagon_edge.alerts import AlertEngine, FightState
from octagon_edge.db import OddsSnapshot, Prediction, sessions
from octagon_edge.engine.edge import bet_at_decimal
from octagon_edge.engine.odds_math import american_to_decimal as dec
from octagon_edge.engine.parlay import build_legs
from octagon_edge.ingest.odds_api import FightOdds, parse
from octagon_edge.ingest.prediction_markets import parse_kalshi_props


def test_bet_at_price_is_where_the_edge_meets_the_threshold():
    d = bet_at_decimal(0.40, 0.04)
    assert 0.40 - 1 / d == pytest.approx(0.04)
    assert bet_at_decimal(0.04, 0.04) is None


def test_odds_api_totals_are_parsed_per_book(card):
    events = [{"home_team": "Alexander Volkanovski", "away_team": "Movsar Evloev", "commence_time": "2026-10-24T20:00:00Z",
               "bookmakers": [{"key": "draftkings", "title": "DraftKings", "markets": [
                   {"key": "h2h", "outcomes": [{"name": "Alexander Volkanovski", "price": -115}, {"name": "Movsar Evloev", "price": -105}]},
                   {"key": "totals", "outcomes": [{"name": "Over", "price": -150, "point": 4.5}, {"name": "Under", "price": 120, "point": 4.5}]}]}]}]
    fo = parse(events, card)["Alexander Volkanovski vs Movsar Evloev"]
    assert fo.totals["DraftKings"] == (4.5, dec(-150), dec(120))


def test_kalshi_props_link_to_fights_by_event_code(card):
    fights = [{"event_ticker": "KXUFCFIGHT-26OCT24YANDVA", "yes_sub_title": "Petr Yan"}]
    m = lambda t, who, bid: {"event_ticker": t, "yes_sub_title": who, "yes_bid_dollars": f"{bid:.2f}",  # noqa: E731
                             "yes_ask_dollars": f"{bid + 0.01:.2f}"}
    props = {"distance": [m("KXUFCDISTANCE-26OCT24YANDVA", "Fight goes the distance", 0.70)],
             "method": [m("KXUFCMOV-26OCT24YANDVA", w, p) for w, p in (
                 ("Petr Yan by Submission", 0.02), ("Petr Yan by KO/TKO/DQ", 0.17), ("Petr Yan by Decision", 0.38),
                 ("Merab Dvalishvili by Submission", 0.04), ("Merab Dvalishvili by KO/TKO/DQ", 0.06),
                 ("Merab Dvalishvili by Decision", 0.30), ("Draw/No Contest", 0.00))]}
    out = parse_kalshi_props(fights, props, card)["Petr Yan vs Merab Dvalishvili"]
    assert out["distance"] == pytest.approx(0.705)
    assert set(out["method"]) == {"a_sub", "a_ko", "a_dec", "b_sub", "b_ko", "b_dec", "draw"}
    assert sum(out["method"].values()) == pytest.approx(1.0)
    assert out["method"]["a_dec"] > out["method"]["b_dec"]


def test_parlay_legs_are_only_single_bet_picks():
    rows = [{"id": i, "a": f"A{i}", "b": f"B{i}", "p_final": 0.5, "pick_side": pick,
             "sides": [{"book": "X", "decimal": 2.2, "edge": 0.05}, {"book": "X", "decimal": 2.1, "edge": 0.01}]}
            for i, pick in ((1, 0), (2, None))]
    legs = build_legs(rows, picks_only=True)
    assert [(leg.fight_id, leg.side) for leg in legs] == [(1, 0)]


def test_alerts_only_price_from_your_books_and_quote_your_home_book():
    e = AlertEngine(1000, 0.25, 0.03, edge_pre=0.04, edge_live=0.06, model_weight=0.0,
                    usable_books=("Book", "Book2"), home_book="Fanatics")
    books = {"Book": (dec(120), dec(-140)), "Book2": (dec(120), dec(-140)), "Offshore": (dec(200), dec(-260))}
    st = FightState(p_model=0.5, a="A", b="B")
    assert not [a for a in e.evaluate("A vs B", st, books, None, False, {}, now=1) if a.kind == "pre"]
    e2 = AlertEngine(1000, 0.25, 0.03, edge_pre=0.04, edge_live=0.06, model_weight=0.0, home_book="Fanatics")
    alerts = [a for a in e2.evaluate("A vs B", FightState(p_model=0.5, a="A", b="B"), books, None, False, {}, now=1)
              if a.kind == "pre"]
    assert alerts and alerts[0].book == "Offshore" and "At Fanatics: worth it at" in alerts[0].body


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'ui.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr("octagon_edge.webpush.DATA_DIR", tmp_path)
    monkeypatch.setattr("octagon_edge.webpush.VAPID_FILE", tmp_path / "vapid.json")
    monkeypatch.setattr("octagon_edge.webpush.PRIVATE_PEM", tmp_path / "vapid.pem")
    monkeypatch.setattr(api_mod, "APP_TOKEN", "")
    with sessions(url)() as db:
        ev = S.sync_card(db, "ufc-333")
        f = ev.fights[1]
        db.add(Prediction(fight_id=f.id, p_model=0.47, p_market=0.6, p_final=0.6, market_source="test", details={}))
        db.add(OddsSnapshot(fight_id=f.id, source="odds_api", book="DraftKings", dec_a=1.6, dec_b=2.4))
        totals = {b: (4.5, o, u) for b, o, u in (("DraftKings", 1.8, 2.0), ("FanDuel", 1.8, 2.0), ("BetMGM", 1.8, 2.0),
                                                ("Caesars", 2.3, 1.6))}
        S._store_totals(db, ev, {f.key: FightOdds(totals=totals)})
        db.commit()
        fid = f.id
    return TestClient(api_mod.app), fid


def test_price_alert_set_and_clear(client):
    c, fid = client
    assert c.put(f"/api/fights/{fid}/target", json={"side": 1, "american": 150}).json()["targets"] == [None, pytest.approx(2.5)]
    assert c.get(f"/api/fights/{fid}").json()["targets"] == [None, pytest.approx(2.5)]
    assert c.put(f"/api/fights/{fid}/target", json={"side": 1, "american": 50}).status_code == 400
    assert c.put(f"/api/fights/{fid}/target", json={"side": 1, "american": None}).json()["targets"] == [None, None]


def test_books_settings_and_home_book(client):
    c, _ = client
    b = c.get("/api/books").json()
    assert b["home"] == "Fanatics" and b["seen"] == ["DraftKings"] and not b["home_in_feed"]
    b = c.put("/api/books", json={"usable": ["DraftKings"]}).json()
    assert b["usable"] == ["DraftKings"]


def test_total_rounds_line_shopping_flags_the_soft_book(client):
    c, fid = client
    props = c.get(f"/api/fights/{fid}/props").json()
    line = props["totals"][0]
    assert line["point"] == 4.5 and line["books"] == 4
    assert line["over"]["book"] == "Caesars"
    assert props["flags"] == [pytest.approx({"point": 4.5, "side": "over", "book": "Caesars", "decimal": 2.3,
                                             "edge": line["over"]["edge"]})]
    assert c.get(f"/api/fights/{fid}").json()["prop_flags"][0]["book"] == "Caesars"
