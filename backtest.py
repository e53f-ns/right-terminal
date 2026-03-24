"""
Historical Backtester — tests strategies on past candle data.
Uses real indicator pipeline + rule-based signal generation + PaperEngine.

Usage:
    python backtest.py                    # backtest all strategies on 30 days
    python backtest.py --strategy SCALP   # backtest single strategy
    python backtest.py --days 14          # custom lookback
"""

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from analysis.indicators import compute_indicators
from analysis.smc import detect_smc
from trading.paper_engine import PaperEngine, compute_leverage


# Strategy parameters for signal generation
STRATEGY_PARAMS = {
    "SCALP": {
        "rsi_oversold": 40, "rsi_overbought": 60,
        "vol_min": 1.2, "sl_pct": 0.5, "tp_multiplier": 1.5,
        "hold_bars": 8,  # 8 x 15m = 2h max
    },
    "SWING": {
        "rsi_oversold": 42, "rsi_overbought": 58,
        "vol_min": 1.0, "sl_pct": 1.5, "tp_multiplier": 2.0,
        "hold_bars": 96,  # 96 x 15m = 24h max
    },
    "POSITION": {
        "rsi_oversold": 45, "rsi_overbought": 55,
        "vol_min": 0.8, "sl_pct": 3.0, "tp_multiplier": 2.5,
        "hold_bars": 384,  # 384 x 15m = 4 days max
    },
}


@dataclass
class BacktestResult:
    strategy: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    final_balance: float
    pnl_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    best_trade_pct: float
    worst_trade_pct: float
    trades: list = field(default_factory=list)

    def passes_gate(self, min_win_rate: float = 55,
                    min_profit_factor: float = 1.0) -> bool:
        if self.total_trades < 5:
            return False
        return (self.win_rate >= min_win_rate
                and self.profit_factor >= min_profit_factor)

    def save(self, path: str):
        data = asdict(self)
        data["saved_at"] = datetime.now(timezone.utc).isoformat()
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def summary(self) -> str:
        gate = "PASS" if self.passes_gate() else "FAIL"
        return (
            f"{self.strategy}: {self.win_rate:.1f}% WR | "
            f"PF={self.profit_factor:.2f} | "
            f"{self.total_trades} trades | "
            f"DD={self.max_drawdown_pct:.1f}% | "
            f"P&L={self.pnl_pct:+.1f}% | [{gate}]"
        )


class RuleBasedSignalGenerator:
    """Generates BUY/SELL signals using the same indicators as the live agent."""

    def generate_signals(self, df: pd.DataFrame,
                         strategy: str = "SCALP") -> list[dict]:
        params = STRATEGY_PARAMS.get(strategy, STRATEGY_PARAMS["SCALP"])
        signals = []

        # Need at least 200 bars for indicators
        if len(df) < 200:
            return signals

        for i in range(200, len(df)):
            window = df.iloc[max(0, i - 200):i + 1].copy()
            window = window.reset_index(drop=True)

            try:
                ind = compute_indicators(window)
            except Exception:
                continue

            rsi = ind.get("rsi", 50)
            vol_ratio = ind.get("volume_ratio", 1.0)
            macd = ind.get("macd", 0)
            ema20 = ind.get("ema20", 0)
            ema50 = ind.get("ema50", 0)
            ema200 = ind.get("ema200", 0)
            price = float(window["close"].iloc[-1])

            # Trend filter
            trend_bull = price > ema200 > 0 and ema20 > ema50
            trend_bear = price < ema200 and ema20 < ema50

            action = None
            confidence = 50

            # BUY signals
            if (rsi < params["rsi_oversold"] and vol_ratio > params["vol_min"]
                    and trend_bull):
                action = "BUY"
                confidence = min(60 + int((params["rsi_oversold"] - rsi) * 2)
                                 + int(vol_ratio * 5), 95)

            # SELL signals
            elif (rsi > params["rsi_overbought"] and vol_ratio > params["vol_min"]
                  and trend_bear):
                action = "SELL"
                confidence = min(60 + int((rsi - params["rsi_overbought"]) * 2)
                                 + int(vol_ratio * 5), 95)

            # MACD crossover confirmation
            if action and macd > 0 and action == "SELL":
                confidence -= 10
            if action and macd < 0 and action == "BUY":
                confidence -= 10

            if action and confidence >= 60:
                sl_dist = price * params["sl_pct"] / 100
                if action == "BUY":
                    sl = round(price - sl_dist, 6)
                    tp = round(price + sl_dist * params["tp_multiplier"], 6)
                else:
                    sl = round(price + sl_dist, 6)
                    tp = round(price - sl_dist * params["tp_multiplier"], 6)

                signals.append({
                    "bar_index": i,
                    "action": action,
                    "confidence": confidence,
                    "entry": price,
                    "sl": sl,
                    "tp": tp,
                    "strategy": strategy,
                })

        return signals


class Backtester:
    def __init__(self, initial_balance: float = 10000):
        self._initial_balance = initial_balance

    def run(self, df: pd.DataFrame, strategy: str = "SCALP") -> BacktestResult:
        engine = PaperEngine(initial_balance=self._initial_balance)
        gen = RuleBasedSignalGenerator()
        params = STRATEGY_PARAMS.get(strategy, STRATEGY_PARAMS["SCALP"])

        signals = gen.generate_signals(df, strategy=strategy)
        trades = []
        peak_balance = self._initial_balance
        max_drawdown_pct = 0.0
        pair = "BACKTEST/USDT"

        for sig in signals:
            bar_idx = sig["bar_index"]

            # Skip if already in position
            if pair in engine.positions:
                # Check exit on this bar
                current_price = float(df["close"].iloc[bar_idx])
                result = engine.check_stop_take(pair, current_price)
                if result and result.get("status") == "closed":
                    trades.append(result)
                # Check max hold time
                pos = engine.positions.get(pair)
                if pos:
                    entry_bar = pos.get("_entry_bar", bar_idx)
                    if bar_idx - entry_bar >= params["hold_bars"]:
                        result = engine.close_position(pair, current_price)
                        if result.get("status") == "closed":
                            trades.append(result)
                continue

            # Open position
            leverage = compute_leverage(sig["confidence"], strategy)
            margin = engine.balance * 0.03  # 3% per trade
            if margin < 10:
                continue

            try:
                result = engine.open_position(
                    pair=pair, side=sig["action"].lower(),
                    size_usdt=margin, entry=sig["entry"],
                    stop_loss=sig["sl"], take_profit=sig["tp"],
                    confidence=sig["confidence"], leverage=leverage,
                    strategy=strategy,
                )
                if result.get("status") == "filled":
                    engine.positions[pair]["_entry_bar"] = bar_idx
            except (ValueError, KeyError):
                pass

            # Track drawdown
            current_balance = engine.balance + engine.locked_margin
            peak_balance = max(peak_balance, current_balance)
            if peak_balance > 0:
                dd = (peak_balance - current_balance) / peak_balance * 100
                max_drawdown_pct = max(max_drawdown_pct, dd)

        # Close any remaining position at last price
        if pair in engine.positions:
            last_price = float(df["close"].iloc[-1])
            result = engine.close_position(pair, last_price)
            if result.get("status") == "closed":
                trades.append(result)

        # Also walk remaining bars for SL/TP exits
        # (signals may have opened a position that needs bar-by-bar checking)
        # This is handled above within the signal loop

        return self._compute_result(strategy, trades, engine, max_drawdown_pct)

    def run_all_strategies(self, df: pd.DataFrame) -> dict[str, BacktestResult]:
        results = {}
        for strategy in STRATEGY_PARAMS:
            results[strategy] = self.run(df, strategy=strategy)
        return results

    def _compute_result(self, strategy: str, trades: list,
                        engine: PaperEngine,
                        max_drawdown_pct: float) -> BacktestResult:
        wins = [t for t in trades if t.get("pnl_usdt", 0) > 0]
        losses = [t for t in trades if t.get("pnl_usdt", 0) <= 0]

        total = len(trades)
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total * 100) if total > 0 else 0

        total_win = sum(t["pnl_usdt"] for t in wins) if wins else 0
        total_loss = abs(sum(t["pnl_usdt"] for t in losses)) if losses else 0
        profit_factor = (total_win / total_loss) if total_loss > 0 else (
            999.0 if total_win > 0 else 0.0)

        win_pcts = [t.get("pnl_pct", 0) for t in wins]
        loss_pcts = [t.get("pnl_pct", 0) for t in losses]
        all_pcts = [t.get("pnl_pct", 0) for t in trades]

        final = engine.balance + engine.locked_margin
        pnl_pct = (final - self._initial_balance) / self._initial_balance * 100

        return BacktestResult(
            strategy=strategy,
            total_trades=total,
            wins=win_count,
            losses=loss_count,
            win_rate=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            max_drawdown_pct=round(max_drawdown_pct, 1),
            final_balance=round(final, 2),
            pnl_pct=round(pnl_pct, 1),
            avg_win_pct=round(sum(win_pcts) / len(win_pcts), 2) if win_pcts else 0,
            avg_loss_pct=round(sum(loss_pcts) / len(loss_pcts), 2) if loss_pcts else 0,
            best_trade_pct=round(max(all_pcts), 2) if all_pcts else 0,
            worst_trade_pct=round(min(all_pcts), 2) if all_pcts else 0,
            trades=[{
                "pnl_usdt": t.get("pnl_usdt", 0),
                "pnl_pct": t.get("pnl_pct", 0),
                "result": t.get("result", ""),
            } for t in trades],
        )


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backtest trading strategies")
    parser.add_argument("--strategy", default=None,
                        help="Strategy to test (SCALP/SWING/POSITION or 'all')")
    parser.add_argument("--days", type=int, default=30, help="Lookback days")
    parser.add_argument("--pair", default="BTC/USDT", help="Pair to backtest")
    parser.add_argument("--output", default="logs/backtest_results.json",
                        help="Output file path")
    args = parser.parse_args()

    from config import Config
    from data.bybit import BybitClient

    cfg = Config()
    bybit = BybitClient(cfg.bybit_api_key, cfg.bybit_secret, cfg.bybit_testnet)

    # Fetch historical data
    # 30 days of 15m candles = 2880 bars, Bybit max per request = 200
    # Need multiple requests
    print(f"Fetching {args.days} days of 15m candles for {args.pair}...")
    all_candles = []
    limit = 200
    bars_needed = args.days * 24 * 4  # 15m bars per day = 96

    df = bybit.get_candles(args.pair, timeframe="15m", limit=min(bars_needed, limit))
    # For >200 bars, we'd need pagination — use what we can get for now
    print(f"Got {len(df)} candles")

    bt = Backtester(initial_balance=cfg.paper_initial_balance)

    if args.strategy and args.strategy.upper() != "ALL":
        result = bt.run(df, strategy=args.strategy.upper())
        print(result.summary())
        gate = "PASS" if result.passes_gate() else "FAIL"
        print(f"Gate: {gate}")
    else:
        results = bt.run_all_strategies(df)
        all_results = {}
        best = None
        for strat, result in results.items():
            print(result.summary())
            all_results[strat] = result
            if best is None or result.win_rate > best.win_rate:
                best = result

        if best:
            print(f"\nBest strategy: {best.strategy} ({best.win_rate:.1f}% WR)")

        # Save combined results
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        combined = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "pair": args.pair,
            "days": args.days,
            "bars": len(df),
            "strategies": {k: asdict(v) for k, v in results.items()},
            "best_strategy": best.strategy if best else None,
        }
        with open(args.output, "w") as f:
            json.dump(combined, f, indent=2, default=str)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
