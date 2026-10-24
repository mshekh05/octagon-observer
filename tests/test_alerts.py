from octagon_edge.alerts import AlertEngine, FightState
from octagon_edge.engine.odds_math import american_to_decimal as dec


def engine():
    return AlertEngine(1000, 0.25, 0.03, edge_pre=0.04, edge_live=0.06, model_weight=0.35)


def test_prefight_value_alert_and_dedup():
    e = engine()
    st = FightState(p_model=0.70, a="A Fighter", b="B Fighter")
    books = {"Book": (dec(120), dec(-140))}  # market says A ~44%, model says 70%
    alerts = e.evaluate("A vs B", st, books, None, live=False, targets={}, now=1000)
    kinds = [a.kind for a in alerts]
    assert "pre" in kinds
    assert all("A Fighter" in a.title for a in alerts if a.kind == "pre")
    # same situation a minute later is suppressed
    assert not [a for a in e.evaluate("A vs B", st, books, None, False, {}, now=1060) if a.kind == "pre"]
    # after the cooldown it fires again
    assert [a for a in e.evaluate("A vs B", st, books, None, False, {}, now=1000 + 601) if a.kind == "pre"]


def test_no_value_when_model_agrees_with_market():
    st = FightState(p_model=0.5, a="A", b="B")
    assert engine().evaluate("A vs B", st, {"Book": (dec(-110), dec(-110))}, None, False, {}, now=1) == []


def test_live_value_uses_prediction_market():
    st = FightState(p_model=0.5, a="A", b="B")
    alerts = engine().evaluate("A vs B", st, {"Book": (dec(150), dec(-180))}, pm_p=0.55, live=True,
                               targets={}, now=1)
    assert any(a.kind == "live" and a.urgent and "A" in a.title for a in alerts)


def test_swing_and_target():
    e = engine()
    st = FightState(p_model=0.5, a="A", b="B")
    e.evaluate("A vs B", st, {}, pm_p=0.50, live=True, targets={}, now=0)
    swing = e.evaluate("A vs B", st, {}, pm_p=0.62, live=True, targets={}, now=60)
    assert [a for a in swing if a.kind == "swing"]
    hits = e.evaluate("A vs B", st, {"Book": (dec(130), dec(-150))}, None, False, {"A": dec(120)}, now=70)
    assert [a for a in hits if a.kind == "target"]
