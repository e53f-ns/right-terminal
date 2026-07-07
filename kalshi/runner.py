#!/usr/bin/env python3
"""
Kalshi near-certainty spread scalper.

Logic:
  - Find "BTC above $X" contracts where BTC is 15%+ above strike
  - YES price in 0.88–0.96 range (near certain, not yet fully priced)
  - Buy YES, target +4¢, stop -3¢
  - Hold ≤24h; resolves at $1.00 if BTC stays above strike

No directional prediction — pure spread harvest to $1.00.
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

BYBIT_KEY       = os.getenv("BYBIT_API_KEY", "")
BYBIT_SECRET    = os.getenv("BYBIT_SECRET", "")
KALSHI_API_KEY  = os.getenv("KALSHI_API_KEY", "")
PAPER           = os.getenv("KALSHI_PAPER", "true").lower() == "true"
INITIAL_BALANCE = float(os.getenv("KALSHI_INITIAL_BALANCE", "1000"))
SCAN_INTERVAL   = 60    # 1 minute — need to catch target/stop quickly

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(message)s",
    datefmt = "%H:%M:%S",
)
logger = logging.getLogger("kalshi")


def get_btc_price(bybit) -> float:
    try:
        df = bybit.get_candles("BTC/USDT", timeframe="1m", limit=2)
        return float(df["close"].iloc[-1])
    except Exception as e:
        logger.warning("BTC price failed: %s", e)
        return 0.0


def print_snapshot(markets: list, btc: float):
    """Print all 24h markets with their current prices — good for spotting entries."""
    daily = [m for m in markets if m.get("hours_left", 99) <= 24]
    if not daily:
        logger.info("No 24h markets available")
        return

    logger.info("── SNAPSHOT | BTC=$%s | %d daily markets ──────────────", f"{btc:,.0f}", len(daily))
    for m in sorted(daily, key=lambda x: x.get("strike", 0), reverse=True)[:15]:
        strike   = m.get("strike", 0)
        margin   = (btc - strike) / btc * 100 if btc and strike else 0
        yes_ask  = m.get("yes_ask")
        yes_bid  = m.get("yes_bid") or 0
        vol      = m.get("volume", 0)
        h        = m.get("hours_left", 0)

        # Flag contracts in sweet spot
        in_range = yes_ask and 0.88 <= yes_ask <= 0.96 and margin >= 15
        flag = "⚡" if in_range else "  "

        logger.info(
            "%s $%-9s %+.1f%% away | ask=%.2f bid=%.2f vol=%-6.0f h=%.1f | %s",
            flag, f"{strike:,.0f}", margin,
            yes_ask or 0, yes_bid, vol, h,
            m.get("subtitle", "")[:30],
        )


def main():
    from data.bybit       import BybitClient
    from kalshi.fetcher   import KalshiFetcher
    from kalshi.screener  import screen, check_exits
    from kalshi.executor  import KalshiExecutor

    mode = "PAPER" if PAPER else "LIVE ⚠️"
    logger.info("=" * 65)
    logger.info("KALSHI SCALPER | %s | balance=$%.2f", mode, INITIAL_BALANCE)
    logger.info("Entry: YES ask 0.88–0.96 | margin >15%% | expire <24h")
    logger.info("Exit:  target +4¢ | stop -3¢")
    logger.info("=" * 65)

    bybit    = BybitClient(api_key=BYBIT_KEY, secret=BYBIT_SECRET)
    fetcher  = KalshiFetcher(api_key=KALSHI_API_KEY)
    executor = KalshiExecutor(
        initial_balance = INITIAL_BALANCE,
        paper           = PAPER,
        api_key         = KALSHI_API_KEY if not PAPER else "",
    )

    # Reset stale positions.json if it has old polymarket data
    cycle = 0
    while True:
        cycle += 1
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        logger.info("── CYCLE %d | %s UTC", cycle, ts)

        # ── 1. Settle expired positions ────────────────────────────────────────
        settled = executor.check_expirations(fetcher)
        for r in settled:
            icon = "✅" if r.pnl > 0 else "❌"
            logger.info("SETTLED %s | %s | P&L=%+.2f", icon, r.subtitle[:40], r.pnl)

        # ── 2. Get current BTC price ───────────────────────────────────────────
        btc = get_btc_price(bybit)
        if btc <= 0:
            logger.warning("No BTC price — skipping cycle")
            time.sleep(SCAN_INTERVAL)
            continue
        logger.info("BTC: $%s", f"{btc:,.0f}")

        # ── 3. Fetch all markets ───────────────────────────────────────────────
        all_markets = fetcher.fetch_btc_markets(limit=200)

        # ── 4. Check exits for open positions ─────────────────────────────────
        if executor.positions:
            current_by_ticker = {m["ticker"]: m for m in all_markets if m.get("ticker")}
            exits = check_exits(executor.positions, current_by_ticker)
            for ticker, exit_price, reason in exits:
                executor.close(ticker, exit_price, reason)

        # ── 5. Snapshot every 5 cycles ────────────────────────────────────────
        if cycle % 5 == 1:
            print_snapshot(all_markets, btc)

        # ── 6. Screen for new entries ──────────────────────────────────────────
        open_tickers = set(executor.positions.keys())
        candidates   = screen(all_markets, btc, open_tickers)

        if candidates:
            # Trade top candidate only (best safety margin)
            best = candidates[0]
            # Get close_time from the market dict for expiry tracking
            mkt_dict = next((m for m in all_markets if m.get("ticker") == best.ticker), {})
            close_time = mkt_dict.get("close_time", "")
            fill = executor.open(best, close_time=close_time)
            if fill:
                logger.info(
                    "NEW POSITION | entry=%.2f | target=%.2f | stop=%.2f | $%.2f",
                    fill["entry"], best.target_price, best.stop_price, fill["stake"],
                )

        # ── 7. Status ──────────────────────────────────────────────────────────
        executor.print_positions()
        s = executor.status()
        logger.info(
            "STATUS | bal=$%.2f | open=%d | P&L=%+.2f | trades=%d | WR=%.1f%%",
            s["balance"], s["open_positions"], s["total_pnl"],
            s["trades"], s["win_rate_pct"],
        )
        logger.info("Sleeping %ds...\n", SCAN_INTERVAL)
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
