import json
from pathlib import Path

import pytest

from octagon_edge.ingest.card import load_card

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def card():
    return load_card("ufc-333")


@pytest.fixture
def fixture():
    return lambda name: json.loads((FIX / name).read_text())
