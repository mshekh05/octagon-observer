import pytest

from octagon_edge.engine.odds_math import american_to_decimal
from octagon_edge.ingest.names import same_fighter
from octagon_edge.ingest.odds_api import parse
from octagon_edge.ingest.prediction_markets import parse_kalshi, parse_polymarket


def test_name_matching():
    assert same_fighter("Alexander Volkanovski", "Alex Volkanovski")
    assert not same_fighter("Alexander Volkanovski", "Alexander Volkov")
    assert same_fighter("Abus Magomedov", "Abusupiyan Magomedov")
    assert same_fighter("Lone'er Kavanagh", "Loneer Kavanagh")
    assert same_fighter("Iasmin Lucindo", "Iasmin Lucíndo")


def test_card_find_orients_fighters(card):
    fight, first_is_a = card.find("Merab Dvalishvili", "Petr Yan")
    assert fight.a == "Petr Yan" and first_is_a is False
    assert card.find("Someone Else", "Petr Yan") is None


def test_odds_api_parse_orients_to_card(card, fixture):
    odds = parse(fixture("odds_api.json"), card, now_iso="2026-10-24T21:45:00Z")
    assert set(odds) == {"Alexander Volkanovski vs Movsar Evloev", "Petr Yan vs Merab Dvalishvili"}
    volk = odds["Alexander Volkanovski vs Movsar Evloev"]
    assert volk.books["FanDuel"] == pytest.approx((american_to_decimal(-122), american_to_decimal(102)))
    assert not volk.live
    yan = odds["Petr Yan vs Merab Dvalishvili"]
    assert yan.books["BetMGM"][0] == pytest.approx(american_to_decimal(-180))
    assert yan.live


def test_kalshi_parse(card, fixture):
    out = parse_kalshi(fixture("kalshi.json")["markets"], card)
    assert out["Alexander Volkanovski vs Movsar Evloev"].p_a == pytest.approx(0.5)
    assert out["Petr Yan vs Merab Dvalishvili"].p_a == pytest.approx(0.59)
    assert out["Alexander Volkov vs Rizvan Kuniev"].p_a == pytest.approx(0.54)


def test_polymarket_parse(card, fixture):
    out = parse_polymarket(fixture("polymarket.json"), card)
    assert out["Alexander Volkanovski vs Movsar Evloev"].p_a == pytest.approx(0.48)
    assert len(out) == 1  # yes/no market without fighter names is ignored


def test_kalshi_discovery_falls_back_to_event_search(card, fixture, tmp_path, monkeypatch):
    import httpx
    from octagon_edge.ingest import prediction_markets as pm

    monkeypatch.setattr(pm, "SERIES_CACHE", tmp_path / "series.json")
    markets = fixture("kalshi.json")["markets"]

    def handler(req: httpx.Request):
        if req.url.path.endswith("/markets"):
            return httpx.Response(200, json={"markets": [], "cursor": ""})
        return httpx.Response(200, json={"cursor": "", "events": [
            {"event_ticker": "KXNBA-1", "series_ticker": "KXNBA", "title": "NBA game", "markets": []},
            {"event_ticker": "KXUFC333-VOLEVL", "series_ticker": "KXUFC333", "title": "UFC 333 main event",
             "markets": markets[:2]},
        ]})

    out = pm.fetch_kalshi(card, httpx.Client(transport=httpx.MockTransport(handler)))
    assert out["Alexander Volkanovski vs Movsar Evloev"].p_a == pytest.approx(0.5)
    assert "KXUFC333" in (tmp_path / "series.json").read_text()
