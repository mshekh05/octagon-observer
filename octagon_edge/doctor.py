"""`octagon-edge doctor`: checks every key and data source, and says how to fix failures."""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .config import CACHE_DIR, ROOT, Settings
from .ingest import experts, news, prediction_markets as pm
from .ingest.card import Card

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
ODDS_EVENTS = "https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/events"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def _odds_api(s: Settings, card: Card, http: httpx.Client) -> Check:
    name = "The Odds API (US sportsbooks)"
    if not s.odds_api_key:
        return Check(name, FAIL, "ODDS_API_KEY not set - no sportsbook prices, so no value picks",
                     "Get a free key at https://the-odds-api.com and put it in .env")
    # The events endpoint is free: it validates the key without spending credits.
    try:
        resp = http.get(ODDS_EVENTS, params={"apiKey": s.odds_api_key})
    except httpx.HTTPError as exc:
        return Check(name, FAIL, f"unreachable: {exc}", "Check internet access / firewall from this machine")
    if resp.status_code == 401:
        return Check(name, FAIL, "key rejected (401)", "Copy the key again from your the-odds-api.com dashboard")
    if resp.status_code == 429:
        return Check(name, FAIL, "out of credits or rate-limited (429)", "Wait for the monthly reset or use a second key")
    if resp.status_code != 200:
        return Check(name, FAIL, f"HTTP {resp.status_code}: {resp.text[:120]}")
    matched = sum(1 for ev in resp.json() if card.find(ev.get("home_team", ""), ev.get("away_team", "")))
    remaining = resp.headers.get("x-requests-remaining", "?")
    status = PASS if matched >= len(card.fights) // 2 else WARN
    return Check(name, status, f"key OK, {remaining} credits left, {matched}/{len(card.fights)} card fights listed",
                 "" if status == PASS else "Books may not have posted every prelim yet; re-run closer to the event. "
                 "If main-card fights are missing, a name differs - tell Claude the names shown on the book.")


def _kalshi(card: Card, http: httpx.Client) -> Check:
    name = "Kalshi (live prediction market)"
    try:
        found = pm.fetch_kalshi(card, http)
    except httpx.HTTPError as exc:
        return Check(name, FAIL, f"unreachable: {exc}", "Kalshi may be geo-blocked here; Polymarket/Odds API still work")
    if found:
        return Check(name, PASS, f"{len(found)}/{len(card.fights)} fights matched")
    try:
        markets, series = pm.discover_kalshi_ufc(http)
    except httpx.HTTPError:
        markets, series = [], set()
    titles = sorted({m.get("title", "")[:70] for m in markets})[:5]
    return Check(name, WARN, f"0 fights matched. UFC series seen: {sorted(series) or 'none'}. Sample titles: {titles}",
                 "Send these lines to Claude so the market parser can be adjusted")


def _polymarket(card: Card, http: httpx.Client) -> Check:
    name = "Polymarket (live prediction market)"
    try:
        found = pm.fetch_polymarket(card, http)
    except httpx.HTTPError as exc:
        return Check(name, FAIL, f"unreachable: {exc}", "Polymarket may be geo-blocked here; Kalshi/Odds API still work")
    return Check(name, PASS if found else WARN, f"{len(found)}/{len(card.fights)} fights matched",
                 "" if found else "No UFC 333 markets listed (yet), or they use a different tag")


def _experts(s: Settings, card: Card, http: httpx.Client) -> Check:
    name = "Expert picks (SplitPicks)"
    if not s.splitpicks_url:
        return Check(name, WARN, "SPLITPICKS_URL not set - skipped", "Optional: set SPLITPICKS_URL in .env")
    try:
        consensus = experts.fetch_consensus(s.splitpicks_url, card, http)
    except httpx.HTTPError as exc:
        return Check(name, WARN, f"unreachable: {exc}", "Optional source; everything else still works without it")
    matched = sum(1 for c in consensus.values() if c.n)
    if not matched:
        return Check(name, WARN, "0 fights matched - page layout may have changed",
                     "Send the page's HTML to Claude so the parser can be adjusted")
    return Check(name, PASS, f"{matched}/{len(card.fights)} fights, {sum(c.n for c in consensus.values())} picks")


def _news(http: httpx.Client) -> list[Check]:
    out = []
    for source, url in news.RSS_FEEDS.items():
        try:
            resp = http.get(url, headers={"User-Agent": news.USER_AGENT})
            ok = resp.status_code == 200 and b"<" in resp.content[:200]
            out.append(Check(f"RSS: {source}", PASS if ok else WARN, f"HTTP {resp.status_code}"))
        except httpx.HTTPError as exc:
            out.append(Check(f"RSS: {source}", WARN, f"unreachable: {exc}"))
    try:
        resp = http.get(news.REDDIT_SEARCH.format(sub="MMA"), params={"q": "UFC 333", "limit": 5},
                        headers={"User-Agent": news.USER_AGENT})
        out.append(Check("Reddit public JSON", PASS if resp.status_code == 200 else WARN,
                         f"HTTP {resp.status_code}",
                         "" if resp.status_code == 200 else "Reddit blocks some IPs; set REDDIT_CLIENT_ID/SECRET for OAuth"))
    except httpx.HTTPError as exc:
        out.append(Check("Reddit public JSON", WARN, f"unreachable: {exc}"))
    return out


def _alerts(s: Settings, send: bool) -> list[Check]:
    from .notify import Notifier

    n = Notifier(s)
    if not n.channels:
        return [Check("Phone alerts", FAIL, "no channel configured",
                      "Set NTFY_TOPIC (install the ntfy app, subscribe to that topic) or TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID")]
    if not send:
        return [Check("Phone alerts", WARN, f"configured: {n.channels} (not tested, --no-send)")]
    ok = n.send("Octagon Edge doctor", "Test alert: if this reached your phone, alerts work.", urgent=True)
    out = []
    for ch in n.channels:
        if ch in ok:
            out.append(Check(f"Alerts: {ch}", PASS, "test message sent - confirm it arrived on your phone"))
        elif ch == "ntfy":
            out.append(Check("Alerts: ntfy", FAIL, f"send to {s.ntfy_url}/{s.ntfy_topic} failed",
                             "Check NTFY_URL; for self-hosted ntfy make sure the server is reachable"))
        else:
            out.append(Check("Alerts: telegram", FAIL, "send failed",
                             "Message your bot once first; get chat id from https://api.telegram.org/bot<TOKEN>/getUpdates"))
    return out


def _llm(s: Settings, http: httpx.Client) -> Check:
    if s.llm_provider == "none":
        return Check("LLM analyst", WARN, "disabled (LLM_PROVIDER=none) - reports have no written breakdown",
                     "Optional: LLM_PROVIDER=ollama or claude")
    if s.llm_provider == "ollama":
        try:
            tags = http.get(f"{s.ollama_url}/api/tags").json().get("models", [])
        except (httpx.HTTPError, ValueError) as exc:
            return Check("LLM analyst (Ollama)", FAIL, f"{s.ollama_url} unreachable: {exc}", "Start Ollama or fix OLLAMA_URL")
        names = [t.get("name", "") for t in tags]
        if not any(n.split(":")[0] == s.ollama_model.split(":")[0] for n in names):
            return Check("LLM analyst (Ollama)", FAIL, f"model {s.ollama_model} not pulled (have {names[:5]})",
                         f"Run: ollama pull {s.ollama_model}")
        return Check("LLM analyst (Ollama)", PASS, f"{s.ollama_model} available")
    if s.llm_provider == "claude":
        try:
            import anthropic

            anthropic.Anthropic().models.retrieve(s.claude_model)
            return Check("LLM analyst (Claude)", PASS, f"{s.claude_model} reachable with your key")
        except Exception as exc:  # noqa: BLE001 - report whatever went wrong
            return Check("LLM analyst (Claude)", FAIL, str(exc)[:160], "Set ANTHROPIC_API_KEY in .env")
    return Check("LLM analyst", FAIL, f"unknown LLM_PROVIDER={s.llm_provider}", "Use none, ollama or claude")


def _stats() -> Check:
    path = CACHE_DIR / "greco" / "ufc_event_details.csv"
    try:
        from .ingest import greco

        greco.download()
    except httpx.HTTPError as exc:
        if not path.exists():
            return Check("Fighter stats (ufcstats via GitHub)", FAIL, f"download failed: {exc}",
                         "Needs raw.githubusercontent.com access once")
    age_h = (time.time() - path.stat().st_mtime) / 3600
    latest = path.read_text().splitlines()[1].split(",http")[0].split(",")[0] if path.exists() else "?"
    return Check("Fighter stats (ufcstats via GitHub)", PASS, f"cached {age_h:.0f}h ago, latest event: {latest}")


def run(card: Card, s: Settings, send: bool = True) -> int:
    http = httpx.Client(timeout=20, follow_redirects=True)
    checks = [Check(".env file", PASS if (ROOT / ".env").exists() else WARN,
                    "found" if (ROOT / ".env").exists() else "missing - using defaults/environment",
                    "" if (ROOT / ".env").exists() else "cp .env.example .env and fill it in")]
    checks.append(_stats())
    checks.append(_odds_api(s, card, http))
    checks.append(_kalshi(card, http))
    checks.append(_polymarket(card, http))
    checks.append(_experts(s, card, http))
    checks += _news(http)
    checks += _alerts(s, send)
    checks.append(_llm(s, http))

    width = max(len(c.name) for c in checks)
    colors = {PASS: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m"}
    print(f"\nOctagon Edge doctor - {card.event}\n")
    for c in checks:
        print(f"  {colors[c.status]}{c.status}\033[0m  {c.name:<{width}}  {c.detail}")
        if c.fix and c.status != PASS:
            print(f"        {'':<{width}}  -> {c.fix}")
    fails = sum(c.status == FAIL for c in checks)
    has_prices = any(c.status == PASS for c in checks if "Odds API" in c.name or "prediction market" in c.name)
    print(f"\n{fails} failing check(s).", "Ready for the event." if has_prices and not fails else
          "Fix the FAIL lines above (at minimum one price source and one alert channel).")
    return 1 if fails else 0
