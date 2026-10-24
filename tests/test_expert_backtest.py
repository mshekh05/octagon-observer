import numpy as np
import pandas as pd
import pytest

from octagon_edge.engine import expert_backtest as EB
from octagon_edge.ingest import experts as X
from octagon_edge.ingest.card import Card, Fight
from octagon_edge.ingest.kalshi_history import candle_mid, match

INDEX = """<a href="kings.html">Kings</a>
<a href="kings/ufc-333-volkanovski-vs-evloev.html">333</a>
<a href="kings/ufc-buckley-vs-malott.html">FN</a>
<a href="https://splitpicks.com/kings/ufc-silva-vs-delgado.html">Noche</a>"""


def test_event_slug_matches_splitpicks_page_names():
    assert X.event_slug("UFC 333: Volkanovski vs. Evloev") == "ufc-333-volkanovski-vs-evloev"
    assert X.event_slug("UFC Fight Night: Buckley vs. Malott") == "ufc-buckley-vs-malott"
    assert X.event_slug("UFC 327: Procházka vs. Ulberg") == "ufc-327-prochazka-vs-ulberg"


def test_finds_the_cards_page_by_slug_or_main_event():
    pages = X.parse_index(INDEX)
    assert set(pages) == {"ufc-333-volkanovski-vs-evloev", "ufc-buckley-vs-malott", "ufc-silva-vs-delgado"}
    exact = Card("UFC 333: Volkanovski vs. Evloev", "2026-10-24", [Fight("Alexander Volkanovski", "Movsar Evloev", "")])
    assert X.find_event_page(pages, exact).endswith("ufc-333-volkanovski-vs-evloev")
    # "Noche UFC" doesn't slug the same way; the main-event surnames still find it
    noche = Card("Noche UFC: Silva vs. Delgado", "2026-09-13", [Fight("Jean Silva", "Jose Delgado", "")])
    assert X.find_event_page(pages, noche).endswith("ufc-silva-vs-delgado")
    other = Card("UFC 334: A vs. B", "2026-11-07", [Fight("Ann Able", "Bea Baker", "")])
    assert X.find_event_page(pages, other) is None


def test_resolve_url_honours_explicit_and_off():
    card = Card("UFC 333", "2026-10-24", [])
    assert X.resolve_url("off", card, http=None) is None
    assert X.resolve_url("https://example.test/page", card, http=None) == "https://example.test/page"


def test_candle_mid_reads_live_and_archived_formats():
    live = {"yes_bid": {"close_dollars": "0.61"}, "yes_ask": {"close_dollars": "0.63"}}
    archived = {"yes_bid": {"close": "0.43"}, "yes_ask": {"close": "0.44"}}
    assert candle_mid(live) == pytest.approx(0.62)
    assert candle_mid(archived) == pytest.approx(0.435)
    assert candle_mid({"price": {"close": "0.30"}}) == pytest.approx(0.30)


def test_kalshi_markets_match_bouts_and_devig_to_fighter_a():
    fights = pd.DataFrame([{"url": "u1", "date": pd.Timestamp("2026-10-17"), "fighter_a": "Louis Jourdain",
                            "fighter_b": "Timothy Angel Cuamba"}])
    markets = [{"ticker": t, "event_ticker": "E1", "yes_sub_title": n, "close_time": "2026-10-18T00:59:44Z"}
               for t, n in (("E1-JOU", "Louis Jourdain"), ("E1-CUA", "Timothy Angel Cuamba"))]
    out = match(markets, {"E1-JOU": 0.62, "E1-CUA": 0.42}, fights)
    assert list(out["url"]) == ["u1"] and out["p_kalshi"].iloc[0] == pytest.approx(0.62 / 1.04)


def test_expert_backtest_finds_no_weight_when_experts_are_noise(monkeypatch):
    rng = np.random.default_rng(5)
    n = 600
    p_market = rng.uniform(0.25, 0.75, n)
    rows = pd.DataFrame({"url": [f"u{i}" for i in range(n)], "date": pd.Timestamp("2026-05-01"),
                         "a_won": (rng.uniform(size=n) < p_market).astype(int), "a_age": 30.0, "b_age": 30.0})
    experts = pd.DataFrame({"url": rows["url"], "event": [f"E{i // 12}" for i in range(n)],
                            "share_a": rng.uniform(0, 1, n), "n": 10})
    kalshi = pd.DataFrame({"url": rows["url"], "p_kalshi": p_market})
    monkeypatch.setattr(EB, "walk_forward", lambda rows, start_year: pd.Series(p_market, index=rows["url"]))
    bt = EB.run(rows, experts, kalshi, model_weight=0.17, youth_adj=0.0, configured=0.15)
    assert bt.suggested_weight == 0.0
    assert bt.losses[0.5].mean() > bt.losses[0.0].mean()
    assert "Suggested EXPERT_WEIGHT 0.00" in bt.summary()
