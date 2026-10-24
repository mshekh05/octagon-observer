"""Settings loaded from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = ROOT / "reports"
CARDS_DIR = Path(__file__).resolve().parent / "data" / "cards"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _f(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    return float(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    odds_api_key: str
    odds_api_regions: str
    odds_api_budget: int
    ntfy_url: str
    ntfy_topic: str
    telegram_bot_token: str
    telegram_chat_id: str
    llm_provider: str
    ollama_url: str
    ollama_model: str
    claude_model: str
    bankroll: float
    kelly_fraction: float
    max_stake_pct: float
    edge_prefight: float
    edge_live: float
    model_weight: float
    expert_weight: float
    splitpicks_url: str
    llm_max_adjust: float = 0.0
    max_model_gap: float = 0.25
    youth_adj: float = 0.07
    home_book: str = "Fanatics"  # the book you bet at; not in the Odds API feed, so it gets "bet at" prices
    usable_books: tuple[str, ...] = ()  # books you can bet at; empty = all books in the feed
    totals_every: int = 1800  # seconds between total-rounds fetches on event day (costs an extra credit)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        e = os.environ.get
        return cls(
            odds_api_key=e("ODDS_API_KEY", ""),
            odds_api_regions=e("ODDS_API_REGIONS", "us") or "us",
            odds_api_budget=int(_f("ODDS_API_BUDGET", 150)),
            ntfy_url=(e("NTFY_URL", "") or "https://ntfy.sh").rstrip("/"),
            ntfy_topic=e("NTFY_TOPIC", ""),
            telegram_bot_token=e("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=e("TELEGRAM_CHAT_ID", ""),
            llm_provider=(e("LLM_PROVIDER", "none") or "none").lower(),
            ollama_url=(e("OLLAMA_URL", "") or "http://localhost:11434").rstrip("/"),
            ollama_model=e("OLLAMA_MODEL", "") or "gemma4:12b",
            claude_model=e("CLAUDE_MODEL", "") or "claude-opus-5-5",
            bankroll=_f("BANKROLL", 1000),
            kelly_fraction=_f("KELLY_FRACTION", 0.25),
            max_stake_pct=_f("MAX_STAKE_PCT", 0.03),
            edge_prefight=_f("EDGE_PREFIGHT", 0.04),
            edge_live=_f("EDGE_LIVE", 0.06),
            # 0: against Kalshi closing prices (backtest-experts) and BestFightOdds lines (backtest-market)
            # the stats model made the market worse; it is still computed and shown, just not blended in.
            model_weight=_f("MODEL_WEIGHT", 0.0),
            # 0 from `octagon-edge backtest-experts`: on 312 fights the consensus only made Kalshi worse
            expert_weight=_f("EXPERT_WEIGHT", 0.0),
            splitpicks_url=e("SPLITPICKS_URL", ""),  # blank: find the card's page on splitpicks.com/kings
            # Short-notice and missed-weight news is already in the price, so the LLM's
            # news nudge is off unless you opt in (max 0.03).
            llm_max_adjust=min(_f("LLM_MAX_ADJUST", 0.0), 0.03),
            max_model_gap=_f("MAX_MODEL_GAP", 0.25),
            youth_adj=_f("YOUTH_ADJ", 0.07),  # log-odds per 5 years older, from the market backtest
            home_book=e("HOME_BOOK", "") or "Fanatics",
            usable_books=tuple(b.strip() for b in e("USABLE_BOOKS", "").split(",") if b.strip()),
            totals_every=int(_f("TOTALS_EVERY", 1800)),
        )
