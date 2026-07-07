"""
Pure-math screener for Kalshi near-certainty spread scalping.

Strategy: buy deep-ITM YES contracts that are 90%+ likely to resolve YES.
No directional prediction — just harvest the gap between current price and $1.00.

Entry criteria:
  - Strike is >15% below current BTC price (deep ITM)
  - YES ask price between 0.88 and 0.96 (room to grow to 1.0)
  - Expires within 24h (daily market, fastest resolution)
  - Volume >= MIN_VOLUME_USD (enough liquidity to enter/exit)
  - Not already in a position on this ticker

Exit targets (set at entry, checked every scan):
  - TAKE PROFIT: yes_bid >= entry + PROFIT_TARGET
  - STOP LOSS:   yes_bid <= entry - STOP_LOSS
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── Entry filters ──────────────────────────────────────────────────────────────
MIN_SAFETY_MARGIN  = 0.15   # strike must be >=15% below current BTC price
MIN_YES_PRICE      = 0.88   # don't buy if already too close to 1.0
MAX_YES_PRICE      = 0.96   # don't buy if not enough room to profit
MAX_HOURS_TO_CLOSE = 24.0   # only daily markets
MIN_VOLUME         = 500    # minimum 24h volume in contracts

# ── Exit targets ───────────────────────────────────────────────────────────────
PROFIT_TARGET = 0.04   # exit when yes_bid rises 4¢ above entry
STOP_LOSS     = 0.03   # exit when yes_bid falls 3¢ below entry


@dataclass
class Candidate:
    ticker:        str
    subtitle:      str
    strike:        float
    btc_price:     float
    safety_margin: float   # (btc - strike) / btc
    yes_ask:       float   # entry price
    yes_bid:       float   # current sell price
    spread:        float
    volume:        float
    hours_left:    float
    target_price:  float   # yes_bid target to exit at profit
    stop_price:    float   # yes_bid level to stop out


def screen(markets: list, btc_price: float, open_tickers: set) -> list:
    """
    Filter markets and return Candidate list sorted by best value
    (highest safety margin, lowest ask price = most room to 1.0).

    markets: parsed market dicts from KalshiFetcher
    btc_price: current BTC spot price
    open_tickers: set of tickers already in a position (skip these)
    """
    candidates = []

    for m in markets:
        ticker = m.get("ticker", "")
        if ticker in open_tickers:
            continue

        strike     = m.get("strike", 0)
        yes_ask    = m.get("yes_ask")
        yes_bid    = m.get("yes_bid") or 0.0
        hours_left = m.get("hours_left", 0)
        volume     = m.get("volume", 0)

        # ── Filter 1: expiry within 24h ────────────────────────────────────────
        if hours_left <= 0 or hours_left > MAX_HOURS_TO_CLOSE:
            continue

        # ── Filter 2: strike safety margin ────────────────────────────────────
        if strike <= 0 or btc_price <= 0:
            continue
        safety = (btc_price - strike) / btc_price
        if safety < MIN_SAFETY_MARGIN:
            continue

        # ── Filter 3: YES price in sweet spot ─────────────────────────────────
        if yes_ask is None:
            continue
        if not (MIN_YES_PRICE <= yes_ask <= MAX_YES_PRICE):
            continue

        # ── Filter 4: liquidity ────────────────────────────────────────────────
        if volume < MIN_VOLUME:
            continue

        # ── Filter 5: spread sanity (shouldn't be too wide) ───────────────────
        spread = m.get("spread", 1.0)
        if spread > 0.10:   # >10¢ spread = too illiquid
            continue

        candidates.append(Candidate(
            ticker        = ticker,
            subtitle      = m.get("subtitle", ""),
            strike        = strike,
            btc_price     = btc_price,
            safety_margin = round(safety, 4),
            yes_ask       = yes_ask,
            yes_bid       = yes_bid,
            spread        = spread,
            volume        = volume,
            hours_left    = hours_left,
            target_price  = round(yes_ask + PROFIT_TARGET, 2),
            stop_price    = round(yes_ask - STOP_LOSS, 2),
        ))

    # Sort: highest safety margin first, then lowest ask (most room to grow)
    candidates.sort(key=lambda c: (-c.safety_margin, c.yes_ask))

    if candidates:
        logger.info("SCREENER: %d candidates found", len(candidates))
        for c in candidates:
            logger.info(
                "  %s | BTC=$%s strike=$%s margin=%.1f%% | "
                "ask=%.2f bid=%.2f vol=%.0f h=%.1f",
                c.ticker, f"{c.btc_price:,.0f}", f"{c.strike:,.0f}",
                c.safety_margin * 100,
                c.yes_ask, c.yes_bid, c.volume, c.hours_left,
            )
    else:
        logger.info("SCREENER: no candidates (all filtered out)")

    return candidates


def check_exits(positions: dict, current_markets: dict) -> list:
    """
    Check open positions against current market prices.

    positions: {ticker: KalshiPosition}
    current_markets: {ticker: market dict with latest yes_bid/yes_ask}

    Returns list of (ticker, exit_price, reason) tuples to close.
    """
    exits = []
    for ticker, pos in positions.items():
        m = current_markets.get(ticker)
        if not m:
            continue

        current_bid = m.get("yes_bid") or 0.0
        if current_bid <= 0:
            continue

        target = pos.target_price
        stop   = pos.stop_price

        if current_bid >= target:
            exits.append((ticker, current_bid, "TARGET"))
            logger.info(
                "TARGET HIT %s | bid=%.2f >= target=%.2f | P&L=+%.2f/share",
                ticker, current_bid, target, current_bid - pos.entry_price,
            )
        elif current_bid <= stop:
            exits.append((ticker, current_bid, "STOP"))
            logger.warning(
                "STOP HIT %s | bid=%.2f <= stop=%.2f | P&L=%.2f/share",
                ticker, current_bid, stop, current_bid - pos.entry_price,
            )

    return exits
