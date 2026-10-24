from pathlib import Path

import pytest

from octagon_edge.ingest.experts import parse

FIX = Path(__file__).parent / "fixtures" / "splitpicks_ufc333.html"


@pytest.fixture
def html():
    return FIX.read_text()


def test_parses_main_event_consensus_by_surname_not_position(html, card):
    consensus = parse(html, card)
    main = consensus["Alexander Volkanovski vs Movsar Evloev"]
    # 10 of 14 handicappers (across all three tables) picked Volkanovski.
    assert main.n == 14
    assert main.p_a is not None and 0.6 < main.p_a < 0.8


def test_matches_the_last_row_to_the_last_fight_on_the_card(html, card):
    # The "Caliari" row has only one non-blank pick and sits first in the page
    # (earliest prelim first) but last in the card (main-event-first) - a
    # position-based mapping would get this wrong.
    consensus = parse(html, card)
    lucindo = consensus["Iasmin Lucindo vs Nicolle Caliari"]
    assert lucindo.n == 1
    assert lucindo.p_a == 0.0  # the one pick (Caliari) was side B


def test_every_card_fight_gets_a_consensus_entry(html, card):
    consensus = parse(html, card)
    assert set(consensus) == {f.key for f in card.fights}


def test_lopsided_fight_still_counts_the_minority_pick(html, card):
    # Yan vs Dvalishvili: 12 for Yan, 2 for Dvalishvili across the three tables.
    consensus = parse(html, card)
    c = consensus["Petr Yan vs Merab Dvalishvili"]
    assert c.n == 14
    assert 0.75 < c.p_a < 0.95
