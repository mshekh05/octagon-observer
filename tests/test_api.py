import pytest
from fastapi.testclient import TestClient

from octagon_edge import api as api_mod
from octagon_edge import services as S
from octagon_edge.db import OddsSnapshot, Prediction, sessions
from octagon_edge.engine.live import RoundInput


class FakeLive:
    def predict(self, p_pre, rounds, scheduled):
        lead = sum(1 if r.winner == 0 else -1 if r.winner == 1 else 0 for r in rounds)
        return min(max(p_pre + 0.15 * lead, 0.01), 0.99)

    predict_from_prior = predict


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr("octagon_edge.webpush.DATA_DIR", tmp_path)
    monkeypatch.setattr("octagon_edge.webpush.VAPID_FILE", tmp_path / "vapid.json")
    monkeypatch.setattr("octagon_edge.webpush.PRIVATE_PEM", tmp_path / "vapid.pem")
    monkeypatch.setattr(api_mod, "_models", {"live": FakeLive()})
    monkeypatch.setattr(api_mod, "APP_TOKEN", "")
    monkeypatch.setattr("octagon_edge.engine.live.LIVE_MODEL_PATH", tmp_path / "none.joblib")
    with sessions(url)() as db:
        ev = S.sync_card(db, "ufc-333")
        main = ev.fights[0]
        db.add(Prediction(fight_id=main.id, p_model=0.41, p_market=0.52, p_final=0.48, market_source="test",
                          details={"record_a": "15-3 (UFC)", "record_b": "10-0 (UFC)"}))
        db.add(OddsSnapshot(fight_id=main.id, source="odds_api", book="DraftKings", dec_a=1.87, dec_b=1.95))
        db.commit()
    return TestClient(api_mod.app)


def test_event_and_fight(client):
    ev = client.get("/api/events/ufc-333").json()
    assert len(ev["fights"]) == 14
    main = ev["fights"][0]
    assert main["a"] == "Alexander Volkanovski" and main["p_final"] == 0.48
    detail = client.get(f"/api/fights/{main['id']}").json()
    assert detail["details"]["record_b"] == "10-0 (UFC)"
    odds = client.get(f"/api/fights/{main['id']}/odds").json()
    assert odds["books"]["DraftKings"]["a"] == 1.87
    assert 0.45 < odds["series"]["Sportsbooks"][0]["p_a"] < 0.55


def test_bet_lifecycle_with_clv_and_settlement(client):
    fid = client.get("/api/events/ufc-333").json()["fights"][0]["id"]
    bet = client.post("/api/bets", json={"fight_id": fid, "side": 1, "book": "DraftKings",
                                         "american": 110, "stake": 20}).json()
    assert bet["decimal"] == pytest.approx(2.10)
    client.post(f"/api/fights/{fid}/status", json={"status": "live"})
    after = client.get("/api/bets").json()["bets"][0]
    assert after["closing_decimal"] == 1.95  # last pre-fight DraftKings price for Evloev
    assert after["clv"] == pytest.approx(2.10 / 1.95 - 1)
    client.post(f"/api/fights/{fid}/status", json={"status": "finished", "winner": 1})
    out = client.get("/api/bets").json()
    assert out["bets"][0]["result"] == "won" and out["bets"][0]["pnl"] == 22.0
    assert out["summary"]["roi"] == pytest.approx(1.1)


def test_live_rounds_move_probability(client):
    fid = client.get("/api/events/ufc-333").json()["fights"][0]["id"]
    r1 = client.post(f"/api/fights/{fid}/rounds", json={"round": 1, "winner": 0}).json()
    assert r1["p_live"] == pytest.approx(0.63)
    r2 = client.post(f"/api/fights/{fid}/rounds", json={"round": 2, "winner": 0, "kd_a": 1}).json()
    assert r2["p_live"] > r1["p_live"]
    state = client.get(f"/api/fights/{fid}/live").json()
    assert state["status"] == "live" and len(state["rounds"]) == 2
    assert client.post(f"/api/fights/{fid}/rounds", json={"round": 6, "winner": 0}).status_code == 422


def test_auth_required_when_token_set(client, monkeypatch):
    monkeypatch.setattr(api_mod, "APP_TOKEN", "s3cret")
    assert client.get("/api/events").status_code == 401
    assert client.get("/api/events", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/events?token=s3cret").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_settings_and_push(client):
    s = client.put("/api/settings", json={"bankroll": 500, "edge_prefight": 0.05}).json()
    assert s["tuning"]["bankroll"] == 500 and s["tuning"]["edge_prefight"] == 0.05
    assert len(s["vapid_public_key"]) > 80
    assert client.put("/api/settings", json={"kelly_fraction": 3}).status_code == 422
    bad = client.post("/api/push/subscribe", json={"endpoint": "http://x", "keys": {}})
    assert bad.status_code == 400
    ok = client.post("/api/push/subscribe", json={"endpoint": "https://fcm.googleapis.com/fcm/send/abc",
                                                  "keys": {"p256dh": "k", "auth": "a"}})
    assert ok.json()["subscribed"]
    assert "webpush" in client.get("/api/settings").json()["channels"]


def test_record_round_service_without_model(client):
    with sessions()() as db:
        f = S.sync_card(db, "ufc-333").fights[1]
        est = S.record_round(db, f, 1, RoundInput(winner=1), live_model=FakeLive())
        assert est.p_live == pytest.approx(0.35)  # no prediction stored -> 0.5 prior, B won round 1
