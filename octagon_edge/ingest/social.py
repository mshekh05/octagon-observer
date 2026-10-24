"""Free social sources beyond RSS: Reddit (OAuth when configured), Bluesky, YouTube comments."""
from __future__ import annotations

import logging
import os

import httpx

from .news import USER_AGENT, NewsItem, _flags

log = logging.getLogger(__name__)
BSKY_HOSTS = ("https://api.bsky.app", "https://public.api.bsky.app")
YT = "https://www.googleapis.com/youtube/v3"


def reddit_token(http: httpx.Client) -> str | None:
    """App-only OAuth token (free, 100 req/min) when REDDIT_CLIENT_ID/SECRET are set."""
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        return None
    try:
        resp = http.post("https://www.reddit.com/api/v1/access_token", auth=(cid, secret),
                         data={"grant_type": "client_credentials"}, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        return resp.json().get("access_token")
    except httpx.HTTPError as exc:
        log.warning("Reddit OAuth failed: %s", exc)
        return None


def parse_bluesky(body: dict, name: str) -> list[NewsItem]:
    items = []
    for post in body.get("posts", []):
        text = (post.get("record") or {}).get("text", "")
        uri, handle = post.get("uri", ""), (post.get("author") or {}).get("handle", "")
        rkey = uri.rsplit("/", 1)[-1]
        link = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else uri
        items.append(NewsItem("Bluesky", text[:140], link, text, _flags(text)))
    return items


def fetch_bluesky(names: list[str], http: httpx.Client, per_name: int = 25) -> list[NewsItem]:
    items: list[NewsItem] = []
    for name in names:
        for host in BSKY_HOSTS:
            try:
                resp = http.get(f"{host}/xrpc/app.bsky.feed.searchPosts",
                                params={"q": f'"{name}"', "limit": per_name, "sort": "latest"},
                                headers={"User-Agent": USER_AGENT})
                if resp.status_code == 200:
                    items += parse_bluesky(resp.json(), name)
                    break
            except httpx.HTTPError as exc:
                log.debug("Bluesky %s failed: %s", host, exc)
    return items


def parse_youtube_comments(body: dict, video_title: str) -> list[NewsItem]:
    items = []
    for th in body.get("items", []):
        top = ((th.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
        text = top.get("textOriginal") or top.get("textDisplay") or ""
        vid = top.get("videoId", "")
        items.append(NewsItem("YouTube", f"{video_title[:60]}: {text[:80]}",
                              f"https://www.youtube.com/watch?v={vid}", text, _flags(text)))
    return items


def fetch_youtube(fights: list[tuple[str, str]], http: httpx.Client, videos_per_fight: int = 2) -> list[NewsItem]:
    """Comments on recent prediction/breakdown videos. Costs ~100 quota units per fight (search)."""
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        return []
    items: list[NewsItem] = []
    for a, b in fights:
        try:
            resp = http.get(f"{YT}/search", params={
                "key": key, "q": f"{a} vs {b} prediction", "part": "snippet", "type": "video",
                "order": "relevance", "maxResults": videos_per_fight, "publishedAfter": "2026-09-01T00:00:00Z"})
            resp.raise_for_status()
            for v in resp.json().get("items", []):
                vid, title = v["id"]["videoId"], v["snippet"]["title"]
                c = http.get(f"{YT}/commentThreads", params={"key": key, "videoId": vid, "part": "snippet",
                                                            "maxResults": 50, "order": "relevance"})
                if c.status_code == 200:
                    items += parse_youtube_comments(c.json(), title)
        except (httpx.HTTPError, KeyError) as exc:
            log.warning("YouTube failed for %s vs %s: %s", a, b, exc)
    return items
