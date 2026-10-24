"""Free news and fan chatter: MMA RSS feeds and Reddit's public JSON.

Used for red flags (injury, weight miss, late replacement) and a rough read on
public sentiment. X/Twitter has no free read access, so it is not used.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

from .names import mentions

log = logging.getLogger(__name__)

RSS_FEEDS = {
    "MMA Junkie": "https://mmajunkie.usatoday.com/feed",
    "MMA Fighting": "https://www.mmafighting.com/rss/current.xml",
    "Sherdog": "https://www.sherdog.com/rss/news.xml",
    "Bloody Elbow": "https://bloodyelbow.com/feed/",
}
REDDIT_SEARCH = "https://www.reddit.com/r/{sub}/search.json"
USER_AGENT = "octagon-edge/0.1 (personal fight analytics)"

FLAG_PATTERNS = {
    "injury": r"\binjur|\bhurt\b|\bsurgery\b|\btorn\b|\bbroken\b",
    "weight": r"\bmiss(ed|es)? weight|\bweigh-?in|\bweight cut|\bhospitali",
    "withdrawal": r"\bwithdr|\bpulls? out|\bout of (the )?(fight|bout)|\bcancel",
    "replacement": r"\breplace|\bshort[- ]notice|\blate notice",
    "illness": r"\bill(ness)?\b|\bsick\b|\bflu\b|\bvirus\b",
}


@dataclass
class NewsItem:
    source: str
    title: str
    link: str
    text: str
    flags: list[str]


def _flags(text: str) -> list[str]:
    return [name for name, pat in FLAG_PATTERNS.items() if re.search(pat, text, re.I)]


def fetch_rss(http: httpx.Client) -> list[NewsItem]:
    items = []
    for source, url in RSS_FEEDS.items():
        try:
            resp = http.get(url, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except (httpx.HTTPError, ET.ParseError) as exc:
            log.warning("RSS %s unavailable: %s", source, exc)
            continue
        for node in root.iter():
            if not node.tag.endswith(("item", "entry")):
                continue
            get = lambda tag: next((c.text or "" for c in node if c.tag.split("}")[-1] == tag), "")  # noqa: E731
            title = get("title")
            desc = re.sub(r"<[^>]+>", " ", get("description") or get("summary"))
            link = get("link") or next((c.get("href", "") for c in node if c.tag.endswith("link")), "")
            text = f"{title} {desc}"
            items.append(NewsItem(source, title.strip(), link.strip(), text, _flags(text)))
    return items


def fetch_reddit(names: list[str], http: httpx.Client, subs=("MMA", "MMAbetting"),
                 token: str | None = None) -> list[NewsItem]:
    """Recent posts mentioning each fighter. Uses OAuth when a token is given, else public JSON."""
    items = []
    url = "https://oauth.reddit.com/r/{sub}/search" if token else REDDIT_SEARCH
    headers = {"User-Agent": USER_AGENT, **({"Authorization": f"Bearer {token}"} if token else {})}
    for sub in subs:
        for name in names:
            try:
                resp = http.get(url.format(sub=sub),
                                params={"q": f'"{name}"', "restrict_sr": 1, "sort": "new",
                                        "t": "week", "limit": 25},
                                headers=headers)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("Reddit r/%s unavailable: %s", sub, exc)
                break
            for child in resp.json().get("data", {}).get("children", []):
                d = child.get("data", {})
                text = f"{d.get('title', '')} {d.get('selftext', '')[:1500]}"
                items.append(NewsItem(f"r/{sub}", d.get("title", ""),
                                      "https://reddit.com" + d.get("permalink", ""), text, _flags(text)))
    return items


def for_fighter(name: str, items: list[NewsItem]) -> list[NewsItem]:
    return [i for i in items if mentions(name, i.text)]


def tone(name: str, items: list[NewsItem]) -> dict:
    """Sentiment of chatter about a fighter (transformer if installed, else word list)."""
    from ..sentiment import aggregate, score

    mine = for_fighter(name, items)
    agg = aggregate(score([i.text for i in mine]), [i.source for i in mine])
    return {"mentions": len(mine), "score": agg["mean"], **agg}
