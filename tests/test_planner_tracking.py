"""Bet plan, prop pricing and weekly tracking."""
import pytest

from octagon_edge import services as S
from octagon_edge import tracking
from octagon_edge.db import FightResult, Forecast, OddsSnapshot, PlanPick, Prediction, sessions
from octagon_edge.engine import planner
from octagon_edge.engine.props_model import combine
from octagon_edge.engine.planner import Option


def opt(fid, market, outcome, p, dec, book="Book", bet_at=None):
    return Option(fid, f"F{fid}", market, outcome, f"{market} {outcome}", p, dec, book, bet_at)


def test_plan_splits_budget_by_kelly_with_caps_and_never_stakes_negative_ev():
    options = [
        opt(1, "moneyline", "0", 0.55, 2.10),  # value: edge 7.4 pts
        opt(1, "moneyline", "1", 0.45, 1.80),  # other side of the same line: never both
        opt(2, "moneyline", "0", 0.52, 2.00),  # lean: edge 2 pts
        opt(3, "moneyline", "0", 0.50, 1.90),  # negative EV: never staked
        opt(4, "prop", "distance", 0.6, None, None, bet_at=1.8),  # check at your book
    ]
    plan = planner.build(options, budget=100, threshold=0.04, lean_edge=0.015)
    tiers = {(b.option.fight_id, b.tier) for b in plan.bets}
    assert tiers == {(1, "value"), (2, "lean")}
    value = next(b for b in plan.bets if b.tier == "value")
    assert value.stake == 30  # capped at 30% of the budget
    lean = next(b for b in plan.bets if b.tier == "lean")
    assert 1 <= lean.stake <= 15  # half weight, half cap
    assert plan.spent <= 100 and plan.to_dict()["unspent"] == 100 - plan.spent
    assert [c.option.outcome for c in plan.checks] == ["distance"] and plan.checks[0].stake > 0


def test_quiet_card_spends_nothing():
    plan = planner.build([opt(1, "moneyline", "0", 0.5, 1.9), opt(1, "moneyline", "1", 0.5, 1.9)], 100, 0.04, 0.015)
    assert plan.bets == [] and plan.spent == 0


def test_props_use_kalshi_first_and_model_round_shares():
    kalshi = {"distance": 0.6, "method": {"a_ko": 0.15, "a_sub": 0.05, "a_dec": 0.35, "b_ko": 0.1, "b_sub": 0.1,
                                          "b_dec": 0.24, "draw": 0.01}}
    model = {"a_ko": 0.3, "a_sub": 0.1, "a_dec": 0.2, "b_ko": 0.2, "b_sub": 0.1, "b_dec": 0.1, "distance": 0.3,
             "round_shares": {"KO": [0.5, 0.3, 0.2], "SUB": [0.4, 0.4, 0.2]}}
    out = combine(kalshi, model, 3)
    assert out["a_ko"] == (0.15, "Kalshi") and out["distance"] == (0.6, "Kalshi")
    assert out["a_r1"][0] == pytest.approx(0.15 * 0.5 + 0.05 * 0.4)
    assert sum(out[f"ends_r{r}"][0] for r in (1, 2, 3)) == pytest.approx(0.15 + 0.05 + 0.1 + 0.1)
    assert combine(None, model, 3)["distance"] == (0.3, "model")


def test_fighter_photo_needs_an_mma_item_and_is_cached(tmp_path):
    import httpx

    from octagon_edge.ingest import images

    calls = []

    def handler(request):
        if request.url.host == "www.ufc.com":  # not on the UFC roster: falls back to Wikidata
            return httpx.Response(200, text="<title>Search results | UFC</title>")
        calls.append(request.url.params["action"])
        if request.url.params["action"] == "wbsearchentities":
            return httpx.Response(200, json={"search": [
                {"id": "Q1", "description": "Puerto Rican boxer"},
                {"id": "Q2", "description": "American mixed martial artist"}]})
        return httpx.Response(200, json={"entities": {"Q2": {"claims": {"P18": [
            {"mainsnak": {"datavalue": {"value": "Daniel Santos 2024.jpg"}}}]}}}})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cache = tmp_path / "img.json"
    first = images.lookup("Daniel Santos", http, cache)
    assert first["url"].endswith("Special:FilePath/Daniel%20Santos%202024.jpg?width=320")
    assert images.lookup("Daniel Santos", http, cache) == first and len(calls) == 2  # second time from the cache


def test_outcomes_settle_from_the_result():
    r = FightResult(fight_id=1, winner=0, method="KO", round=2, seconds=400)
    won = lambda m, o: tracking.outcome_won(m, o, r, 3)  # noqa: E731
    assert won("moneyline", "0") and not won("moneyline", "1")
    assert won("prop", "a_ko") and not won("prop", "a_dec") and won("prop", "finish") and not won("prop", "distance")
    assert won("prop", "a_r2") and won("prop", "ends_r2") and not won("prop", "b_r2")
    assert won("total_rounds", "under 1.5") is True and won("total_rounds", "over 1.5") is False  # 6:40 is under 7:30
    dec = FightResult(fight_id=1, winner=1, method="DEC", round=3)
    assert tracking.outcome_won("total_rounds", "over 2.5", dec, 3) and tracking.outcome_won("prop", "b_dec", dec, 3)


@pytest.fixture
def db(tmp_path):
    with sessions(f"sqlite:///{tmp_path / 'track.db'}")() as session:
        yield session


def test_fight_start_freezes_forecasts_and_plan_then_result_settles_them(db):
    ev = S.sync_card(db, "ufc-333")
    f = ev.fights[1]
    db.add(Prediction(fight_id=f.id, p_model=0.47, p_market=0.6, p_final=0.6, market_source="test", details={
        "sides": [{"book": "Book", "decimal": 1.9, "edge": 0.07, "source": "line shopping"}, {"book": "Book", "decimal": 2.2, "edge": -0.05}],
        "pricing": {"bet_at_dec": [1.79, None]}}))
    db.add(OddsSnapshot(fight_id=f.id, source="odds_api", book="Book", dec_a=1.9, dec_b=2.2))
    db.commit()
    S.set_status(db, f, "live")
    assert db.query(Forecast).filter_by(fight_id=f.id, market="moneyline").one().p_ours == 0.6
    pick = db.query(PlanPick).filter_by(fight_id=f.id).one()
    assert pick.tier == "value" and pick.market == "moneyline" and pick.outcome == "0"
    S.set_status(db, f, "finished", 0)
    assert db.query(PlanPick).filter_by(fight_id=f.id).one().result == "won"
    card = tracking.scorecard(db)
    assert card["markets"][0]["market"] == "moneyline" and card["markets"][0]["n"] == 1
    assert card["tiers"]["value"]["won"] == 1 and card["tiers"]["value"]["profit"] > 0


def test_prop_bets_settle_on_the_method_not_the_winner(db):
    from octagon_edge.db import Bet

    ev = S.sync_card(db, "ufc-333")
    f = ev.fights[2]
    ko = Bet(fight_id=f.id, side=1, market="prop", outcome="b_ko", decimal=4.85, stake=20)
    ml = Bet(fight_id=f.id, side=1, decimal=2.0, stake=10)
    db.add_all([ko, ml])
    db.commit()
    S.set_status(db, f, "live")
    S.set_status(db, f, "finished", 1)  # winner known, method not yet: the prop waits
    assert ml.result == "won" and ko.result == "open"
    tracking.record_result(db, f, 1, "DEC", 3)
    assert ko.result == "lost" and ko.pnl == -20


def test_old_databases_get_new_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE bet (id INTEGER PRIMARY KEY, fight_id INTEGER, side INTEGER, book VARCHAR(60), decimal FLOAT,"
                " stake FLOAT, placed_at DATETIME, live BOOLEAN, closing_decimal FLOAT, result VARCHAR(8), pnl FLOAT, note TEXT)")
    con.execute("INSERT INTO bet (fight_id, side, decimal, stake, result, note, book, live) VALUES (1, 1, 4.85, 20, 'open', '', 'Fanatics', 0)")
    con.commit()
    con.close()
    from octagon_edge.db import Bet

    with sessions(f"sqlite:///{path}")() as session:
        b = session.query(Bet).one()
        assert b.market == "moneyline" and b.outcome == ""


def test_fighter_photo_prefers_the_ufc_athlete_page(tmp_path):
    import httpx

    from octagon_edge.ingest import images

    assert images.ufc_slug("Lone'er Kavanagh") == "loneer-kavanagh" and images.ufc_slug("Iasmin Lucíndo") == "iasmin-lucindo"

    def handler(request):
        assert request.url.path == "/athlete/colby-thicknesse"
        return httpx.Response(200, text='<meta property="og:image" content="https://ufc.com/images/2026-10/THICKNESSE_COLBY_10-24.png" />')

    found = images.lookup("Colby Thicknesse", httpx.Client(transport=httpx.MockTransport(handler)), tmp_path / "img.json")
    assert found == {"url": "https://www.ufc.com/images/2026-10/THICKNESSE_COLBY_10-24.png",
                     "credit": "https://www.ufc.com/athlete/colby-thicknesse"}
