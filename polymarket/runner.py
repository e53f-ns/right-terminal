#!/usr/bin/env python3
"""
Polymarket BTC 5-minute Up/Down scalper.

Logic:
  - Find active BTC 5-minute Up/Down markets on Polymarket
  - Monitor contract prices + BTC price from Bybit
  - Buy undervalued contracts when momentum aligns
  - Target +5¢, stop -4¢ (or configurable)
  - Paper mode by default

Run standalone:  python -m polymarket.runner
"""
import os
import sys
import time
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import warnings
warnings.filterwarnings("ignore", category=UserWarning)

BYBIT_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_SECRET = os.getenv("BYBIT_SECRET", "")
PAPER = os.getenv("POLYMARKET_PAPER", "true").lower() == "true"
INITIAL_BALANCE = float(os.getenv("POLYMARKET_INITIAL_BALANCE", "100"))
SCAN_INTERVAL = int(os.getenv("POLYMARKET_SCAN_INTERVAL", "5"))
ENTRY_THRESHOLD = float(os.getenv("POLYMARKET_ENTRY_THRESHOLD", "0.60"))
EXIT_THRESHOLD = float(os.getenv("POLYMARKET_EXIT_THRESHOLD", "0.65"))
STOP_LOSS = float(os.getenv("POLYMARKET_STOP_LOSS", "0.40"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("polymarket")

# BTC price history for momentum calculation
btc_history: list[float] = []
MAX_HISTORY = 30


def get_btc_price(bybit) -> float:
    try:
        df = bybit.get_candles("BTC/USDT", timeframe="1m", limit=2)
        return float(df["close"].iloc[-1])
    except Exception as e:
        logger.warning("BTC price failed: %s", e)
        return 0.0


def calc_momentum() -> float:
    """BTC momentum: % change over last 5 ticks."""
    if len(btc_history) < 5:
        return 0.0
    recent = btc_history[-5:]
    return (recent[-1] - recent[0]) / recent[0] * 100


def main():
    from data.bybit import BybitClient
    from polymarket.fetcher import PolymarketFetcher
    from polymarket.screener import screen, check_exits
    from polymarket.executor import PolymarketExecutor

    mode = "PAPER" if PAPER else "LIVE"
    logger.info("=" * 65)
    logger.info("POLYMARKET BTC 5-MIN | %s | balance=$%.2f", mode, INITIAL_BALANCE)
    logger.info("Entry: ask <= %.2f | Exit: bid >= %.2f | Stop: bid <= %.2f",
                ENTRY_THRESHOLD, EXIT_THRESHOLD, STOP_LOSS)
    logger.info("Scan interval: %ds", SCAN_INTERVAL)
    logger.info("=" * 65)

    bybit = BybitClient(api_key=BYBIT_KEY, secret=BYBIT_SECRET)
    fetcher = PolymarketFetcher()
    executor = PolymarketExecutor(
        initial_balance=INITIAL_BALANCE,
        paper=PAPER,
    )

    cycle = 0
    while True:
        cycle += 1
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        logger.info("── CYCLE %d | %s UTC", cycle, ts)

        # ── 1. Settle expired positions ────────────────────────────────────────
        settled = executor.check_expirations()
        for r in settled:
            icon = "+" if r.pnl > 0 else ""
            logger.info("EXPIRED | %s %s | P&L=%s%.4f",
                        r.side.upper(), r.slug[-10:], icon, r.pnl)

        # ── 2. Get BTC price ──────────────────────────────────────────────────
        btc = get_btc_price(bybit)
        if btc <= 0:
            logger.warning("No BTC price — skipping")
            time.sleep(SCAN_INTERVAL)
            continue

        btc_history.append(btc)
        if len(btc_history) > MAX_HISTORY:
            btc_history.pop(0)

        momentum = calc_momentum()
        logger.info("BTC: $%s | momentum: %+.3f%%", f"{btc:,.0f}", momentum)

        # ── 3. Fetch markets + orderbooks ─────────────────────────────────────
        markets = fetcher.fetch_markets(count=2)
        for m in markets:
            for side, token_info in m.get("tokens", {}).items():
                ob = fetcher.get_orderbook(token_info["token_id"])
                if "orderbook" not in m:
                    m["orderbook"] = {}
                m["orderbook"][side] = ob

        # ── 4. Check exits ────────────────────────────────────────────────────
        if executor.positions:
            current_by_slug = {m["slug"]: m for m in markets}
            exits = check_exits(
                executor.positions, current_by_slug,
                exit_threshold=EXIT_THRESHOLD,
                stop_loss=STOP_LOSS,
            )
            for pos_key, exit_price, reason in exits:
                executor.close(pos_key, exit_price, reason)

        # ── 5. Print market snapshot ──────────────────────────────────────────
        for m in markets:
            ttl = m.get("time_left", 0)
            for side in ["up", "down"]:
                ob = m.get("orderbook", {}).get(side, {})
                gamma_p = m.get("tokens", {}).get(side, {}).get("gamma_price", 0)
                clob_ask = ob.get("best_ask", 0)
                clob_bid = ob.get("best_bid", 0)
                spread = ob.get("spread", 0)
                # Show both Gamma implied price and CLOB orderbook
                liquid = "LIQUID" if spread < 0.50 else "THIN"
                logger.info(
                    "  %s %s | gamma=%.3f clob_bid=%.3f clob_ask=%.3f sprd=%.2f | %s | ttl=%ds",
                    m["slug"][-10:], side.upper(),
                    gamma_p, clob_bid, clob_ask, spread, liquid, ttl,
                )

        # ── 6. Screen for entries ─────────────────────────────────────────────
        open_slugs = set(executor.positions.keys())
        candidates = screen(
            markets, btc, momentum, open_slugs,
            entry_threshold=ENTRY_THRESHOLD,
            exit_threshold=EXIT_THRESHOLD,
            stop_loss=STOP_LOSS,
        )

        if candidates:
            best = candidates[0]
            mkt = next((m for m in markets if m["slug"] == best.slug), {})
            fill = executor.open(best, close_ts=mkt.get("close_ts", 0))
            if fill:
                logger.info(
                    "NEW POSITION | %s %s | entry=%.3f tgt=%.3f stop=%.3f | $%.2f",
                    best.side.upper(), best.slug[-10:],
                    fill["entry"], best.target_price, best.stop_price, fill["stake"],
                )

        # ── 7. Status ─────────────────────────────────────────────────────────
        executor.print_positions()
        s = executor.status()
        logger.info(
            "STATUS | bal=$%.2f | open=%d | P&L=%+.4f | trades=%d | WR=%.1f%%",
            s["balance"], s["open_positions"], s["total_pnl"],
            s["trades"], s["win_rate_pct"],
        )
        logger.info("Sleeping %ds...\n", SCAN_INTERVAL)
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Polymarket bot stopped.")
