"""Public-RSS headlines for the brain (no API keys).
Fetch failures return stale cached data or unknown; envelopes retain availability.

Google News RSS, grouped by topic. Cached for CACHE_TTL seconds per query so a decision
cycle over many pairs does not hit the network for each pair. Headlines are untrusted
text: they are trimmed, stripped of control characters and passed to the model as DATA
only (the prompt says so), never as instructions.
"""
import logging
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import json
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

CACHE_TTL = 600
TIMEOUT = 8
BASE = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="

TOPICS = {
    "geopolitics": "geopolitics OR war OR sanctions OR tariffs markets when:3d",
    "fed": "Federal Reserve OR FOMC OR interest rates when:3d",
    "inflation": "CPI inflation OR jobs report when:3d",
    "crypto_reg": "crypto regulation OR SEC crypto OR bitcoin ETF when:3d",
}

_cache: Dict[str, tuple] = {}
_lock = threading.Lock()
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _clean(s: str, n: int = 110) -> str:
    s = _CTRL.sub(" ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n]


def _parse(xml_text: str, limit: int) -> List[dict]:
    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        title = _clean(it.findtext("title") or "")
        if not title:
            continue
        src = _clean(it.findtext("source") or "", 40)
        ts = 0
        try:
            ts = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
        except Exception:
            pass
        link = _clean(it.findtext("link") or "", 2048)
        url = link if urllib.parse.urlparse(link).scheme in ("http", "https") and urllib.parse.urlparse(link).netloc else None
        source_node = it.find("source")
        source_link = source_node.get("url", "") if source_node is not None else ""
        source_url = source_link if urllib.parse.urlparse(source_link).scheme in ("http", "https") and urllib.parse.urlparse(source_link).netloc else None
        out.append({"title": title, "source": src, "ts": ts,
                    "published_at": ts or None, "url": url, "source_url": source_url})
        if len(out) >= limit:
            break
    return out


def _result(hit, now, limit, error=None):
    fetched_at = hit[0] if hit else None
    age = max(0, now - fetched_at) if fetched_at is not None else None
    status = "unknown" if hit is None else ("stale" if now < fetched_at or age >= CACHE_TTL else "fresh")
    meta = {"status": status, "fetched_at": fetched_at, "age_seconds": age,
            "error": error}
    return {**meta, "items": [{**it, **meta} for it in (hit[1] if hit else [])[:max(0, limit)]]}


def fetch_result(query: str, limit: int = 6, session: Optional[requests.Session] = None) -> dict:
    """Envelope also exposes unknown/empty feeds. Only a successful fetch updates time."""
    now = time.time()
    with _lock:
        hit = _cache.get(query)
        if hit and 0 <= now - hit[0] < CACHE_TTL:
            return _result(hit, now, limit)
    try:
        r = (session or requests).get(BASE + urllib.parse.quote(query), timeout=TIMEOUT,
                                     headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        items = _parse(r.text, 12)
        completed = time.time()
        with _lock:
            # Do not let an older concurrent completion replace newer successful data.
            existing = _cache.get(query)
            if existing is None or existing[0] <= completed:
                _cache[query] = (completed, items)
            hit = _cache[query]
        return _result(hit, completed, limit)
    except Exception as e:
        logger.warning("news_feed %r failed: %s", query[:30], str(e)[:100])
        with _lock:
            hit = _cache.get(query)
        return _result(hit, time.time(), limit, type(e).__name__)


def fetch(query: str, limit: int = 6, session: Optional[requests.Session] = None) -> List[dict]:
    """Legacy list API; added per-item provenance. Use fetch_result for empty-feed status."""
    return fetch_result(query, limit, session)["items"]


def topic(name: str, limit: int = 3) -> List[dict]:
    q = TOPICS.get(name)
    return fetch(q, limit) if q else []


def for_asset(base: str, limit: int = 2) -> List[dict]:
    base = re.sub(r"[^A-Za-z0-9 ]", "", base or "")[:12]
    return fetch(f"{base} price OR news when:3d", limit) if base else []


def for_query(query: str, limit: int = 3) -> List[dict]:
    """Free-text headlines (used for forex currency pairs: 'EUR USD ECB Fed')."""
    q = re.sub(r"[^A-Za-z0-9 ]", "", query or "")[:60].strip()
    return fetch(f"{q} when:3d", limit) if q else []


def headlines(base: str = "", total: int = 6) -> List[dict]:
    """Mixed shortlist: 1 per macro topic + asset-specific, newest first, de-duplicated."""
    try:
        items: List[dict] = []
        for t in TOPICS:
            items += topic(t, 1)
        if base:
            items += for_asset(base, 2)
        seen, out = set(), []
        for it in sorted(items, key=lambda x: -x.get("ts", 0)):
            k = it["title"].lower()[:60]
            if k in seen:
                continue
            seen.add(k)
            out.append(it)
        return out[:total]
    except Exception as e:      # belt and braces: the cycle must never break on news
        logger.warning("news_feed headlines failed: %s", str(e)[:100])
        return []


def _utc(ts):
    try:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _prompt(query, total):
    feeds = []
    for name, q in TOPICS.items():
        feeds.append((name, fetch_result(q, 1)))
    if query:
        feeds.append(("asset", fetch_result(query, 3)))
    items = [it for _, feed in feeds for it in feed["items"]]
    seen, out = set(), []
    for it in sorted(items, key=lambda x: -x.get("ts", 0)):
        key = it["title"].lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append({**it, "published_at_utc": _utc(it.get("published_at")),
                    "fetched_at_utc": _utc(it.get("fetched_at"))})
    payload = {"feeds": [{"topic": name, **{k: v for k, v in feed.items() if k != "items"},
                           "item_count": len(feed["items"])} for name, feed in feeds],
               "headlines": out[:max(0, total)]}
    return ("NEWS_UNTRUSTED_DATA: unavailable dates/sources/URLs are null or empty; "
            "do not invent current events or infer no events from unknown/stale. "
            "Fresh means retrieval within TTL, not verified or recent publication. "
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def prompt_block_query(query: str = "", total: int = 6) -> str:
    q = re.sub(r"[^A-Za-z0-9 ]", "", query or "")[:60].strip()
    return _prompt(f"{q} when:3d" if q else "", total)


def prompt_block(base: str = "", total: int = 6) -> str:
    base = re.sub(r"[^A-Za-z0-9 ]", "", base or "")[:12]
    return _prompt(f"{base} price OR news when:3d" if base else "", total)


def clear_cache() -> None:
    with _lock:
        _cache.clear()
