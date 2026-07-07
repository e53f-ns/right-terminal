"""
Kalshi API client — public endpoints (no auth needed for market data).

Base: https://api.elections.kalshi.com/trade-api/v2
Series: KXBTCD  = BTC daily markets ("Will BTC be $X or above at 5 PM EDT?")
Ticker: KXBTCD-26APR1317-T71249.99
  - 26APR1317 = 2026-Apr-13, closes 17:00 EDT (21:00 UTC)
  - T71249.99 = floor_strike $71,249.99

Prices are in dollars: yes_bid_dollars="0.49" means 49¢ per share ($0.49 probability).
"""
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXBTCD"   # BTC daily series


class KalshiFetcher:
    def __init__(self, api_key: str = ""):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "trading-agent/1.0"})
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"

    # ── Public market data (no auth) ───────────────────────────────────────────

    def fetch_btc_markets(self, limit: int = 100) -> list:
        """
        Return all active KXBTCD markets sorted by floor_strike ascending.
        Each element is a parsed market dict ready for pricer.
        """
        try:
            r = self._session.get(
                f"{BASE}/markets",
                params={"status": "open", "series_ticker": SERIES, "limit": limit},
                timeout=10,
            )
            r.raise_for_status()
            raw = r.json().get("markets", [])
        except Exception as e:
            logger.error("Kalshi fetch failed: %s", e)
            return []

        result = []
        for m in raw:
            parsed = self._parse_market(m)
            if parsed:
                result.append(parsed)

        result.sort(key=lambda x: x["strike"])
        logger.debug("Kalshi: %d markets fetched", len(result))
        return result

    def get_orderbook(self, ticker: str) -> dict:
        """Return compact orderbook for a market ticker."""
        try:
            r = self._session.get(f"{BASE}/markets/{ticker}/orderbook", timeout=8)
            r.raise_for_status()
            ob = r.json().get("orderbook_fp", {})
        except Exception as e:
            logger.debug("Orderbook failed %s: %s", ticker, e)
            return {"yes_bids": [], "no_bids": [], "best_yes_bid": 0,
                    "best_yes_ask": 1, "spread": 1, "yes_depth": 0, "no_depth": 0}

        yes_bids = [[float(p), float(s)] for p, s in ob.get("yes_dollars", [])]
        no_bids  = [[float(p), float(s)] for p, s in ob.get("no_dollars",  [])]

        # YES asks = what you pay to buy YES = 1 - best NO bid
        best_yes_bid = yes_bids[0][0] if yes_bids else 0.0
        best_no_bid  = no_bids[0][0]  if no_bids  else 0.0
        best_yes_ask = round(1.0 - best_no_bid, 2) if best_no_bid else 1.0
        spread       = round(best_yes_ask - best_yes_bid, 2)

        yes_depth = sum(s for _, s in yes_bids[:5])
        no_depth  = sum(s for _, s in no_bids[:5])
        total     = yes_depth + no_depth
        imbalance = round(yes_depth / total, 3) if total > 0 else 0.5

        return {
            "best_yes_bid": best_yes_bid,   # highest price someone pays for YES
            "best_yes_ask": best_yes_ask,   # lowest price to buy YES
            "best_no_bid":  best_no_bid,
            "spread":       spread,
            "yes_depth":    round(yes_depth, 0),
            "no_depth":     round(no_depth, 0),
            "imbalance":    imbalance,       # >0.5 = more YES demand (bullish)
        }

    def find_atm_markets(self, markets: list, current_price: float, n: int = 5) -> list:
        """
        Return the N markets whose strikes are closest to current_price.
        These are the highest-liquidity, most meaningful markets to trade.
        """
        if not markets:
            return []
        scored = sorted(markets, key=lambda m: abs(m["strike"] - current_price))
        return scored[:n]

    def get_market(self, ticker: str) -> Optional[dict]:
        """Fetch a single market by ticker."""
        try:
            r = self._session.get(f"{BASE}/markets/{ticker}", timeout=8)
            r.raise_for_status()
            return self._parse_market(r.json().get("market", {}))
        except Exception as e:
            logger.warning("get_market %s failed: %s", ticker, e)
            return None

    # ── Internal ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_market(m: dict) -> Optional[dict]:
        ticker = m.get("ticker", "")
        if not ticker:
            return None

        strike = m.get("floor_strike")
        if strike is None:
            return None

        close_str = m.get("close_time", "")
        hours_left = _hours_to_close(close_str)
        if hours_left is None or hours_left <= 0:
            return None

        yes_bid = _to_float(m.get("yes_bid_dollars"))
        yes_ask = _to_float(m.get("yes_ask_dollars"))
        no_bid  = _to_float(m.get("no_bid_dollars"))
        no_ask  = _to_float(m.get("no_ask_dollars"))

        # Derive missing prices from complement
        if yes_bid is None and no_ask is not None:
            yes_bid = round(1.0 - no_ask, 2)
        if yes_ask is None and no_bid is not None:
            yes_ask = round(1.0 - no_bid, 2)

        mid = round(((yes_bid or 0) + (yes_ask or 1)) / 2, 3)

        return {
            "ticker":       ticker,
            "event_ticker": m.get("event_ticker", ""),
            "strike":       float(strike),
            "subtitle":     m.get("subtitle", ""),
            "close_time":   close_str,
            "hours_left":   round(hours_left, 2),
            "yes_bid":      yes_bid,
            "yes_ask":      yes_ask,
            "no_bid":       no_bid,
            "no_ask":       no_ask,
            "mid":          mid,           # market-implied P(YES)
            "spread":       round((yes_ask or 1) - (yes_bid or 0), 2),
            "volume":       _to_float(m.get("volume_24h_fp")) or 0,
            "open_interest":_to_float(m.get("open_interest_fp")) or 0,
            "last_price":   _to_float(m.get("last_price_dollars")),
            "status":       m.get("status", ""),
        }


def _hours_to_close(close_str: str) -> Optional[float]:
    if not close_str:
        return None
    try:
        close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
        delta = (close_dt - datetime.now(timezone.utc)).total_seconds()
        return max(delta / 3600.0, 0.0)
    except (ValueError, TypeError):
        return None


def _to_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
