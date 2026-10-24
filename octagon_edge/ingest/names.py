"""Fighter-name normalisation and matching across sources."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z ]+", "", s.replace("-", " ")).lower()
    return " ".join(s.split())


def tokens(name: str) -> set[str]:
    return set(normalize(name).split())


def same_fighter(canonical: str, other: str) -> bool:
    """True when `other` (from any source) refers to `canonical`.

    Matches on the surname as a whole token (so Volkov != Volkanovski), or on
    a close fuzzy match of the full name.
    """
    canon, cand = normalize(canonical), normalize(other)
    if not canon or not cand:
        return False
    if canon == cand:
        return True
    last = canon.split()[-1]
    if last in cand.split():
        return True
    # Fuzzy fallback for spelling variants; the surname itself must be close too.
    surname_close = SequenceMatcher(None, last, cand.split()[-1]).ratio() >= 0.8
    return surname_close and SequenceMatcher(None, canon, cand).ratio() >= 0.85


def mentions(canonical: str, text: str) -> bool:
    """Surname appears as a whole word in free text (e.g. a market title)."""
    last = normalize(canonical).split()[-1]
    return last in normalize(text).split()
