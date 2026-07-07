"""
Polymarket BTC 5-minute screener.

Strategy: buy undervalued Up/Down contracts when BTC momentum aligns.
  - Buy Up when ask <= entry_threshold AND BTC moving up
  - Buy Down when ask <= entry_threshold AND BTC moving down
  - Target: sell at exit_threshold (~0.97)
  - Stop: sell at stop_loss (~0.88)

Note on BTC 5-min market liquidity:
  - Markets open with bid=0.01/ask=0.99 (no real pricing)
  - Proper orderbooks only form when market makers are active
  - Real prices converge in the last ~60-120s before close
  - Spread > 0.50 means the market hasn't priced in yet — skip
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── Entry filters ─────────────────────────────────────────────────────────────
# Strategy needs >50% win rate to be +EV. Only enter with strong signal:
#   - Last 15-30 seconds (minimal time for BTC to reverse)
#   - Real BTC momentum (≥0.10% in last 25s, not micro-noise)
#   - Price not too far above 50/50 (we want cheap side when we have edge)
ENTRY_THRESHOLD = 0.55   # avoid paying premium — only cheap undervalued side
EXIT_THRESHOLD = 0.80    # target big win or ride to settlement
STOP_LOSS = 0.25         # wider stop — rely more on true settlement
MAX_SPREAD = 0.50
MIN_TIME_LEFT = 15       # skip last 15s (noise spikes)
MAX_TIME_LEFT = 30       # enter only in last 15-30s window
MIN_MOMENTUM_PCT = 0.10  # require real BTC move, not noise


@dataclass
class PolyCandidate:
    slug: str
    side: str           # "up" or "down"
    token_id: str
    ask_price: float    # entry price
    bid_price: float    # current exit price
    spread: float
    btc_price: float
    btc_momentum: float  # % change over recent ticks
    time_left: int       # seconds to market close
    target_price: float
    stop_price: float


def screen(markets: list, btc_price: float, btc_momentum: float,
           open_slugs: set,
           entry_threshold: float = ENTRY_THRESHOLD,
           exit_threshold: float = EXIT_THRESHOLD,
           stop_loss: float = STOP_LOSS) -> list:
    """
    Screen Polymarket BTC 5-min markets for entry candidates.

    markets: list of market dicts from PolymarketFetcher with orderbook data
    btc_price: current BTC spot price
    btc_momentum: % price change (positive = up)
    open_slugs: set of slugs already in a position
    """
    candidates = []

    for m in markets:
        slug = m.get("slug", "")
        time_left = m.get("time_left", 0)

        if time_left < MIN_TIME_LEFT or time_left > MAX_TIME_LEFT:
            continue

        for side, token_info in m.get("tokens", {}).items():
            # Skip if already in this market (either side — no hedging)
            pos_key = f"{slug}:{side}"
            other_side = "down" if side == "up" else "up"
            other_key = f"{slug}:{other_side}"
            if pos_key in open_slugs or other_key in open_slugs:
                continue

            ob = m.get("orderbook", {}).get(side, {})
            clob_ask = ob.get("best_ask", 0)
            clob_bid = ob.get("best_bid", 0)
            spread = ob.get("spread", 1)
            gamma_price = token_info.get("gamma_price", 0)

            # Use CLOB prices if orderbook is liquid, else use Gamma implied price
            if spread <= MAX_SPREAD and clob_ask > 0 and clob_bid > 0:
                ask = clob_ask
                bid = clob_bid
            elif gamma_price > 0:
                # Gamma price is the implied probability — use as mid
                ask = gamma_price
                bid = gamma_price
                spread = 0.0  # no real spread info from Gamma
            else:
                continue

            if ask > entry_threshold:
                continue

            # Momentum alignment — require REAL directional signal
            if side == "up" and btc_momentum < MIN_MOMENTUM_PCT:
                continue
            if side == "down" and btc_momentum > -MIN_MOMENTUM_PCT:
                continue

            candidates.append(PolyCandidate(
                slug=slug,
                side=side,
                token_id=token_info["token_id"],
                ask_price=ask,
                bid_price=bid,
                spread=spread,
                btc_price=btc_price,
                btc_momentum=btc_momentum,
                time_left=time_left,
                target_price=exit_threshold,
                stop_price=stop_loss,
            ))

    # Sort: lowest ask first (most room to grow)
    candidates.sort(key=lambda c: c.ask_price)

    if candidates:
        logger.info("POLY SCREENER: %d candidates", len(candidates))
        for c in candidates:
            logger.info(
                "  %s %s | ask=%.3f bid=%.3f spread=%.3f | "
                "BTC=$%s mom=%+.3f%% | ttl=%ds",
                c.slug[-10:], c.side.upper(), c.ask_price, c.bid_price,
                c.spread, f"{c.btc_price:,.0f}", c.btc_momentum, c.time_left,
            )
    else:
        logger.debug("POLY SCREENER: no candidates")

    return candidates


def check_exits(positions: dict, current_markets: dict,
                exit_threshold: float = EXIT_THRESHOLD,
                stop_loss: float = STOP_LOSS) -> list:
    """
    Check open positions against current prices.

    positions: {pos_key: PolyPosition}
    current_markets: {slug: market dict with orderbook data}

    Returns list of (pos_key, exit_price, reason) tuples.
    """
    exits = []
    for pos_key, pos in positions.items():
        slug = pos.slug
        m = current_markets.get(slug)
        if not m:
            # Market expired — close at loss
            exits.append((pos_key, 0.0, "EXPIRED"))
            continue

        ob = m.get("orderbook", {}).get(pos.side, {})
        clob_bid = ob.get("best_bid", 0)
        clob_ask = ob.get("best_ask", 0)
        spread = ob.get("spread", 1)

        # Use CLOB bid if orderbook is liquid, else fall back to gamma price
        gamma_price = m.get("tokens", {}).get(pos.side, {}).get("gamma_price", 0)
        if spread <= MAX_SPREAD and clob_bid > 0 and clob_ask > 0:
            current_bid = clob_bid
        elif gamma_price > 0:
            current_bid = gamma_price
        else:
            continue

        if current_bid >= pos.target_price:
            exits.append((pos_key, current_bid, "TARGET"))
            logger.info(
                "TARGET HIT %s | bid=%.3f >= %.3f | P&L=+%.3f/share",
                pos_key, current_bid, pos.target_price,
                current_bid - pos.entry_price,
            )
        elif current_bid <= pos.stop_price:
            exits.append((pos_key, current_bid, "STOP"))
            logger.warning(
                "STOP HIT %s | bid=%.3f <= %.3f | P&L=%.3f/share",
                pos_key, current_bid, pos.stop_price,
                current_bid - pos.entry_price,
            )

    return exits
