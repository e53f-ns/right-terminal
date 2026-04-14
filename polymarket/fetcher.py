"""
Polymarket CLOB + Gamma API client — BTC 5-minute Up/Down markets.

Market discovery uses deterministic slugs: btc-updown-5m-{window_ts}
where window_ts is a Unix timestamp divisible by 300.

Prices come from the CLOB orderbook (public, no auth needed).
"""
import json
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


class PolymarketFetcher:
    def __init__(self, gamma_url: str = GAMMA_BASE, clob_url: str = CLOB_BASE):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "trading-agent/1.0"})
        self.gamma_url = gamma_url
        self.clob_url = clob_url

    def fetch_current_market(self) -> Optional[dict]:
        """Find active BTC 5-min market using deterministic slug."""
        now = int(time.time())
        window_ts = now - (now % 300)

        for ts in [window_ts, window_ts + 300]:
            slug = f"btc-updown-5m-{ts}"
            try:
                r = self._session.get(
                    f"{self.gamma_url}/events",
                    params={"slug": slug},
                    timeout=5,
                )
                r.raise_for_status()
                data = r.json()
                if data and len(data) > 0:
                    event = data[0] if isinstance(data, list) else data
                    result = self._parse_event(event, ts)
                    if result:
                        return result
            except Exception as e:
                logger.debug("Polymarket fetch %s: %s", slug, e)
        return None

    def fetch_markets(self, count: int = 3) -> list:
        """Fetch current + upcoming BTC 5-min markets."""
        now = int(time.time())
        window_ts = now - (now % 300)
        markets = []

        for i in range(count):
            ts = window_ts + (i * 300)
            slug = f"btc-updown-5m-{ts}"
            try:
                r = self._session.get(
                    f"{self.gamma_url}/events",
                    params={"slug": slug},
                    timeout=5,
                )
                r.raise_for_status()
                data = r.json()
                if data and len(data) > 0:
                    event = data[0] if isinstance(data, list) else data
                    parsed = self._parse_event(event, ts)
                    if parsed:
                        markets.append(parsed)
            except Exception as e:
                logger.debug("Polymarket fetch %s: %s", slug, e)

        return markets

    def get_orderbook(self, token_id: str) -> dict:
        """Get bid/ask/mid for a token from CLOB orderbook."""
        try:
            r = self._session.get(
                f"{self.clob_url}/book",
                params={"token_id": token_id},
                timeout=5,
            )
            r.raise_for_status()
            book = r.json()

            bids = book.get("bids", [])
            asks = book.get("asks", [])
            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 0.0
            mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0

            bid_depth = sum(float(b.get("size", 0)) for b in bids[:5])
            ask_depth = sum(float(a.get("size", 0)) for a in asks[:5])

            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid": mid,
                "spread": round(best_ask - best_bid, 4) if best_ask and best_bid else 0,
                "bid_depth": round(bid_depth, 0),
                "ask_depth": round(ask_depth, 0),
            }
        except Exception as e:
            logger.debug("Polymarket orderbook error: %s", e)
            return {"best_bid": 0, "best_ask": 0, "mid": 0,
                    "spread": 0, "bid_depth": 0, "ask_depth": 0}

    def _parse_event(self, event: dict, window_ts: int) -> Optional[dict]:
        """Extract token IDs and market info from Gamma event."""
        markets = event.get("markets", [])
        result = {
            "event_id": event.get("id"),
            "slug": event.get("slug", f"btc-updown-5m-{window_ts}"),
            "title": event.get("title", ""),
            "window_ts": window_ts,
            "close_ts": window_ts + 300,
            "time_left": max(0, (window_ts + 300) - int(time.time())),
            "active": event.get("active", False),
            "tokens": {},
        }
        for m in markets:
            outcomes = m.get("outcomes", [])
            token_ids = m.get("clobTokenIds", [])
            outcome_prices = m.get("outcomePrices", [])
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)
            if isinstance(token_ids, str):
                token_ids = json.loads(token_ids)
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices)

            for i, outcome in enumerate(outcomes):
                side = outcome.lower()
                if side in ("up", "down") and i < len(token_ids):
                    gamma_price = float(outcome_prices[i]) if i < len(outcome_prices) else 0.0
                    result["tokens"][side] = {
                        "token_id": token_ids[i],
                        "condition_id": m.get("conditionId"),
                        "gamma_price": gamma_price,  # Gamma API implied price
                    }

        return result if result["tokens"] else None
