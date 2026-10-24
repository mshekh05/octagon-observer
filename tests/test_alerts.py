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


def test_alert_uses_the_same_blend_as_the_analysis():
    from octagon_edge.engine.edge import final_prob

    books = {"Book": (dec(120), dec(-140))}
    inputs = {"model_weight": 0.175, "llm_adjust": 0.0, "p_experts": 0.2, "expert_weight": 0.15,
              "threshold_mult": 1.5}
    st = FightState(p_model=0.70, a="A", b="B")
    st.apply_blend_inputs(inputs)
    fair = 0.4545  # roughly the de-vigged +120/-140 price
    p = final_prob(0.70, fair, 0.175, 0.0, 0.2, 0.15)
    alerts = [a for a in engine().evaluate("A vs B", st, books, None, False, {}, now=1) if a.kind == "pre"]
    # With the debut-fighter gate (half weight, 1.5x threshold) and experts against A, there is no A alert,
    # whereas the plain model-vs-market blend would have fired one (see the first test).
    assert p - 1 / dec(120) < 0.04 * 1.5
    assert not [a for a in alerts if a.title.endswith("+120")]


def test_at_model_weight_zero_only_the_market_and_youth_price_the_fight():
    e = AlertEngine(1000, 0.25, 0.03, edge_pre=0.04, edge_live=0.06, model_weight=0.0)
    books = {"Book": (dec(120), dec(-140))}
    # the model loves A, but at weight 0 that alone is no value
    assert not [a for a in e.evaluate("A vs B", FightState(p_model=0.9, a="A", b="B"), books, None, False, {}, now=1)
                if a.kind == "pre"]
    # A is 10 years younger: the youth shift alone still isn't 4 points here
    young = FightState(p_model=0.9, a="A", b="B", age_gap=-10, youth_adj=0.07)
    assert not [a for a in e.evaluate("A vs B", young, books, None, False, {}, now=1) if a.kind == "pre"]
    # an off-market price (line shopping) is value whatever the model says
    shop = {"Book": (dec(120), dec(-140)), "Book2": (dec(120), dec(-140)), "Soft": (dec(200), dec(-260))}
    alerts = [a for a in e.evaluate("A vs B", FightState(p_model=0.1, a="A", b="B"), shop, None, False, {}, now=1)
              if a.kind == "pre"]
    assert alerts and alerts[0].book == "Soft" and alerts[0].source == "line shopping"
    assert "Market" in alerts[0].body


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
