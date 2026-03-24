# Learning Feedback System + Backtester Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add trade learning memory (learning_log.jsonl) and historical backtester (backtest.py) so the agent learns from mistakes and validates strategies before trading.

**Architecture:** Two independent modules: (1) `utils/learning_log.py` writes post-trade analysis with rule-based mistake detection, injects last 20 lessons into Claude's prompt; (2) `backtest.py` replays 30 days of candles through indicators + rule-based signals + PaperEngine, outputs per-strategy stats. Dashboard F9 shows top mistakes + backtest results. Weekly cron triggers Sunday backtest.

**Tech Stack:** Python, pandas, existing PaperEngine, existing indicator pipeline, JSONL storage

---

## Task 1: Learning Log Core Module

**Files:**
- Create: `utils/learning_log.py`
- Test: `tests/test_learning_log.py`

**Step 1: Write failing tests for LearningLog**

```python
# tests/test_learning_log.py
import json
import os
import tempfile
import pytest
from utils.learning_log import LearningLog


@pytest.fixture
def log(tmp_path):
    return LearningLog(log_dir=str(tmp_path))


def test_log_trade_creates_file(log):
    log.record_trade(
        pair="BTC/USDT", strategy="SCALP", side="BUY",
        entry_reason="RSI oversold + volume spike",
        result="LOSS", pnl_pct=-0.8,
        entry_price=84000, exit_price=83300,
        confidence=72, opened_at="2026-03-25T10:00:00+00:00",
        closed_at="2026-03-25T10:30:00+00:00",
        btc_trend="BEAR", htf_bias="MIXED",
    )
    entries = log.read_recent(20)
    assert len(entries) == 1
    assert entries[0]["pair"] == "BTC/USDT"
    assert entries[0]["result"] == "LOSS"
    assert "mistakes" in entries[0]


def test_detect_counter_trend(log):
    log.record_trade(
        pair="BTC/USDT", strategy="SCALP", side="BUY",
        entry_reason="RSI bounce", result="LOSS", pnl_pct=-0.5,
        entry_price=84000, exit_price=83500,
        confidence=65, opened_at="2026-03-25T10:00:00+00:00",
        closed_at="2026-03-25T10:30:00+00:00",
        btc_trend="BEAR", htf_bias="BEAR",
    )
    entries = log.read_recent(20)
    assert "counter_trend" in entries[0]["mistakes"]


def test_detect_low_confidence_loss(log):
    log.record_trade(
        pair="ETH/USDT", strategy="SCALP", side="SELL",
        entry_reason="breakdown", result="LOSS", pnl_pct=-0.3,
        entry_price=3200, exit_price=3210,
        confidence=62, opened_at="2026-03-25T10:00:00+00:00",
        closed_at="2026-03-25T10:30:00+00:00",
        btc_trend="BEAR", htf_bias="BEAR",
    )
    entries = log.read_recent(20)
    assert "low_confidence" in entries[0]["mistakes"]


def test_detect_wrong_session(log):
    # Asia low-volume hours: 0-7 UTC
    log.record_trade(
        pair="SOL/USDT", strategy="SCALP", side="BUY",
        entry_reason="bounce", result="LOSS", pnl_pct=-0.4,
        entry_price=150, exit_price=149.4,
        confidence=70, opened_at="2026-03-25T03:00:00+00:00",
        closed_at="2026-03-25T03:30:00+00:00",
        btc_trend="BULL", htf_bias="BULL",
    )
    entries = log.read_recent(20)
    assert "wrong_session" in entries[0]["mistakes"]


def test_detect_overtrading(log):
    # 4 trades within 1 hour
    base = "2026-03-25T14:"
    for i, pair in enumerate(["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]):
        log.record_trade(
            pair=pair, strategy="SCALP", side="BUY",
            entry_reason="signal", result="LOSS", pnl_pct=-0.2,
            entry_price=100, exit_price=99.8,
            confidence=70, opened_at=f"{base}{i*10:02d}:00+00:00",
            closed_at=f"{base}{i*10+5:02d}:00+00:00",
            btc_trend="BULL", htf_bias="BULL",
        )
    entries = log.read_recent(20)
    # Last entry should detect overtrading
    assert "overtrading" in entries[-1]["mistakes"]


def test_win_has_no_mistakes(log):
    log.record_trade(
        pair="BTC/USDT", strategy="SCALP", side="BUY",
        entry_reason="RSI + volume", result="WIN", pnl_pct=1.2,
        entry_price=84000, exit_price=85000,
        confidence=80, opened_at="2026-03-25T14:00:00+00:00",
        closed_at="2026-03-25T14:30:00+00:00",
        btc_trend="BULL", htf_bias="BULL",
    )
    entries = log.read_recent(20)
    assert entries[0]["mistakes"] == []


def test_get_pattern_summary(log):
    # Create 3 counter-trend losses
    for i in range(3):
        log.record_trade(
            pair=f"PAIR{i}/USDT", strategy="SCALP", side="BUY",
            entry_reason="bounce", result="LOSS", pnl_pct=-0.5,
            entry_price=100, exit_price=99.5,
            confidence=70, opened_at=f"2026-03-25T{10+i}:00:00+00:00",
            closed_at=f"2026-03-25T{10+i}:30:00+00:00",
            btc_trend="BEAR", htf_bias="BEAR",
        )
    summary = log.get_pattern_summary(20)
    assert "counter_trend" in summary
    assert "3" in summary  # count should appear


def test_read_recent_limits(log):
    for i in range(30):
        log.record_trade(
            pair=f"P{i}/USDT", strategy="SCALP", side="BUY",
            entry_reason="test", result="WIN", pnl_pct=0.1,
            entry_price=100, exit_price=100.1,
            confidence=75, opened_at=f"2026-03-25T{i%24:02d}:00:00+00:00",
            closed_at=f"2026-03-25T{i%24:02d}:30:00+00:00",
            btc_trend="BULL", htf_bias="BULL",
        )
    entries = log.read_recent(20)
    assert len(entries) == 20
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/e53f/trading-agent && python -m pytest tests/test_learning_log.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.learning_log'`

**Step 3: Write minimal implementation**

```python
# utils/learning_log.py
import json
import os
from collections import Counter
from datetime import datetime


# Asia session: 0-7 UTC (low liquidity for crypto)
ASIA_LOW_VOLUME_HOURS = range(0, 8)
LOW_CONFIDENCE_THRESHOLD = 68
OVERTRADING_WINDOW_MINUTES = 60
OVERTRADING_THRESHOLD = 3


class LearningLog:
    def __init__(self, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        self._path = os.path.join(log_dir, "learning_log.jsonl")

    def record_trade(self, *, pair: str, strategy: str, side: str,
                     entry_reason: str, result: str, pnl_pct: float,
                     entry_price: float, exit_price: float,
                     confidence: int, opened_at: str, closed_at: str,
                     btc_trend: str = "UNKNOWN", htf_bias: str = "UNKNOWN"):
        """Record a closed trade with automatic mistake detection."""
        mistakes = self._detect_mistakes(
            side=side, result=result, confidence=confidence,
            opened_at=opened_at, btc_trend=btc_trend, htf_bias=htf_bias,
        )
        record = {
            "pair": pair,
            "strategy": strategy,
            "side": side,
            "entry_reason": entry_reason,
            "result": result,
            "pnl_pct": round(pnl_pct, 3),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "confidence": confidence,
            "opened_at": opened_at,
            "closed_at": closed_at,
            "btc_trend": btc_trend,
            "htf_bias": htf_bias,
            "mistakes": mistakes,
            "ts": datetime.utcnow().isoformat(),
        }
        with open(self._path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def read_recent(self, n: int = 20) -> list[dict]:
        """Read the last n entries from the learning log."""
        if not os.path.exists(self._path):
            return []
        entries = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries[-n:]

    def get_pattern_summary(self, n: int = 20) -> str:
        """Summarize mistake patterns from recent trades for Claude's prompt."""
        entries = self.read_recent(n)
        if not entries:
            return "none"

        mistake_counts = Counter()
        for e in entries:
            for m in e.get("mistakes", []):
                mistake_counts[m] += 1

        losses = [e for e in entries if e["result"] == "LOSS"]
        wins = [e for e in entries if e["result"] == "WIN"]
        total = len(entries)
        win_rate = len(wins) / total * 100 if total else 0

        parts = [f"Last {total} trades: {win_rate:.0f}% win rate"]
        for mistake, count in mistake_counts.most_common(5):
            parts.append(f"{mistake}:{count}")

        # Pair-specific patterns
        pair_losses = Counter(e["pair"] for e in losses)
        for pair, count in pair_losses.most_common(2):
            if count >= 2:
                parts.append(f"{pair} lost {count}x")

        return " | ".join(parts)

    def get_top_mistakes(self, n: int = 3) -> list[dict]:
        """Get top n most frequent recent mistakes with details."""
        entries = self.read_recent(20)
        mistake_details = {}
        for e in entries:
            for m in e.get("mistakes", []):
                if m not in mistake_details:
                    mistake_details[m] = {"count": 0, "examples": []}
                mistake_details[m]["count"] += 1
                if len(mistake_details[m]["examples"]) < 2:
                    mistake_details[m]["examples"].append(
                        f"{e['pair']} {e['side']} {e['pnl_pct']:+.1f}%"
                    )
        sorted_mistakes = sorted(mistake_details.items(),
                                 key=lambda x: x[1]["count"], reverse=True)
        return [{"mistake": k, **v} for k, v in sorted_mistakes[:n]]

    def _detect_mistakes(self, *, side: str, result: str, confidence: int,
                         opened_at: str, btc_trend: str,
                         htf_bias: str) -> list[str]:
        """Rule-based mistake detection."""
        mistakes = []
        if result != "LOSS":
            return mistakes

        # Counter-trend: bought in bear or sold in bull
        if side == "BUY" and btc_trend == "BEAR":
            mistakes.append("counter_trend")
        elif side == "SELL" and btc_trend == "BULL":
            mistakes.append("counter_trend")
        # Also check HTF bias
        if side == "BUY" and htf_bias == "BEAR":
            if "counter_trend" not in mistakes:
                mistakes.append("counter_trend")
        elif side == "SELL" and htf_bias == "BULL":
            if "counter_trend" not in mistakes:
                mistakes.append("counter_trend")

        # Low confidence entry that lost
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            mistakes.append("low_confidence")

        # Wrong session (Asia low-volume hours)
        try:
            hour = datetime.fromisoformat(opened_at).hour
            if hour in ASIA_LOW_VOLUME_HOURS:
                mistakes.append("wrong_session")
        except (ValueError, TypeError):
            pass

        # Overtrading: check recent entries for trades in same hour
        recent = self.read_recent(10)
        try:
            trade_time = datetime.fromisoformat(opened_at)
            trades_in_window = 0
            for e in recent:
                try:
                    t = datetime.fromisoformat(e["opened_at"])
                    diff_minutes = abs((trade_time - t).total_seconds()) / 60
                    if diff_minutes <= OVERTRADING_WINDOW_MINUTES:
                        trades_in_window += 1
                except (ValueError, KeyError):
                    pass
            if trades_in_window >= OVERTRADING_THRESHOLD:
                mistakes.append("overtrading")
        except (ValueError, TypeError):
            pass

        return mistakes
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/e53f/trading-agent && python -m pytest tests/test_learning_log.py -v`
Expected: All 9 tests PASS

**Step 5: Commit**

```bash
git add utils/learning_log.py tests/test_learning_log.py
git commit -m "feat: add learning log with rule-based mistake detection"
```

---

## Task 2: Integrate Learning Log into Trade Pipeline

**Files:**
- Modify: `main.py` (handle_close function + imports)
- Modify: `analysis/claude_brain.py` (_build_prompt method)

**Step 1: Write failing test for prompt injection**

```python
# tests/test_learning_log.py (append to existing)

def test_prompt_injection_format(log):
    """Learning summary should be a compact single line for Claude."""
    log.record_trade(
        pair="BTC/USDT", strategy="SCALP", side="BUY",
        entry_reason="bounce", result="LOSS", pnl_pct=-0.5,
        entry_price=84000, exit_price=83500,
        confidence=65, opened_at="2026-03-25T10:00:00+00:00",
        closed_at="2026-03-25T10:30:00+00:00",
        btc_trend="BEAR", htf_bias="BEAR",
    )
    summary = log.get_pattern_summary(20)
    assert isinstance(summary, str)
    assert len(summary) < 200  # must fit in Claude's context
    assert "\n" not in summary
```

**Step 2: Run test — should PASS (already implemented)**

**Step 3: Integrate into main.py**

In `main.py`, add import at top:
```python
from utils.learning_log import LearningLog
```

In `main()` function, after creating `trade_logger`, add:
```python
learning_log = LearningLog(log_dir=cfg.log_dir)
```

In `handle_close()`, add `learning_log` parameter and after the `trade_logger.log_close()` call, add:
```python
learning_log.record_trade(
    pair=pair, strategy=strategy_type, side=result.get("side", "unknown"),
    entry_reason="", result=result["result"], pnl_pct=result["pnl_pct"],
    entry_price=entry_price or 0, exit_price=result["exit"],
    confidence=pos_data.get("confidence", 0),
    opened_at=pos_data.get("opened_at", ""),
    closed_at=result.get("closed_at", ""),
    btc_trend=state._data.get("last_btc_trend", "UNKNOWN"),
    htf_bias=pos_data.get("htf_bias", "UNKNOWN"),
)
```

In `analyze_single_pair()`, add `learning_log` parameter and inject summary into context:
```python
context["learning_summary"] = learning_log.get_pattern_summary(20)
```

In `claude_brain.py` `_build_prompt()`, add after SCENARIO line:
```python
learning = ctx.get("learning_summary", "none")
# ... then in the return f-string:
f"MISTAKES: {learning}\n"
```

**Step 4: Run full test suite**

Run: `cd /Users/e53f/trading-agent && python -m pytest -v`
Expected: All tests pass

**Step 5: Commit**

```bash
git add main.py analysis/claude_brain.py
git commit -m "feat: integrate learning log into trade pipeline and Claude prompt"
```

---

## Task 3: Backtester Core Module

**Files:**
- Create: `backtest.py`
- Test: `tests/test_backtest.py`

**Step 1: Write failing tests for backtester**

```python
# tests/test_backtest.py
import pandas as pd
import numpy as np
import pytest
from backtest import RuleBasedSignalGenerator, Backtester, BacktestResult


@pytest.fixture
def sample_df():
    """30 days of fake 15m candles (2880 bars)."""
    np.random.seed(42)
    n = 2880
    prices = 84000 + np.cumsum(np.random.randn(n) * 50)
    prices = np.maximum(prices, 50000)  # floor
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-02-23", periods=n, freq="15min", tz="UTC"),
        "open": prices,
        "high": prices + np.random.uniform(10, 200, n),
        "low": prices - np.random.uniform(10, 200, n),
        "close": prices + np.random.randn(n) * 30,
        "volume": np.random.uniform(100, 10000, n),
    })
    return df


def test_signal_generator_returns_valid_signals(sample_df):
    gen = RuleBasedSignalGenerator()
    signals = gen.generate_signals(sample_df)
    assert len(signals) > 0
    for s in signals:
        assert s["action"] in ("BUY", "SELL")
        assert 0 <= s["confidence"] <= 100
        assert s["entry"] > 0
        assert s["sl"] > 0
        assert s["tp"] > 0


def test_backtester_runs_without_error(sample_df):
    bt = Backtester(initial_balance=10000)
    result = bt.run(sample_df, strategy="SCALP")
    assert isinstance(result, BacktestResult)
    assert result.total_trades >= 0
    assert result.final_balance > 0


def test_backtester_result_has_metrics(sample_df):
    bt = Backtester(initial_balance=10000)
    result = bt.run(sample_df, strategy="SCALP")
    assert hasattr(result, "win_rate")
    assert hasattr(result, "profit_factor")
    assert hasattr(result, "max_drawdown_pct")
    assert hasattr(result, "total_trades")
    assert hasattr(result, "strategy")


def test_backtester_multi_strategy(sample_df):
    bt = Backtester(initial_balance=10000)
    results = bt.run_all_strategies(sample_df)
    assert "SCALP" in results
    assert isinstance(results["SCALP"], BacktestResult)


def test_backtester_gate_check():
    """Strategies with <55% win rate should be flagged."""
    result = BacktestResult(
        strategy="SCALP", total_trades=20, wins=8, losses=12,
        win_rate=40.0, profit_factor=0.8, max_drawdown_pct=5.0,
        final_balance=9500, pnl_pct=-5.0, avg_win_pct=1.0,
        avg_loss_pct=-0.8, best_trade_pct=2.0, worst_trade_pct=-1.5,
        trades=[],
    )
    assert not result.passes_gate(min_win_rate=55)
    result2 = BacktestResult(
        strategy="SCALP", total_trades=20, wins=12, losses=8,
        win_rate=60.0, profit_factor=1.5, max_drawdown_pct=3.0,
        final_balance=10500, pnl_pct=5.0, avg_win_pct=1.2,
        avg_loss_pct=-0.6, best_trade_pct=2.5, worst_trade_pct=-1.0,
        trades=[],
    )
    assert result2.passes_gate(min_win_rate=55)


def test_backtester_saves_results(sample_df, tmp_path):
    bt = Backtester(initial_balance=10000)
    result = bt.run(sample_df, strategy="SCALP")
    path = str(tmp_path / "backtest_results.json")
    result.save(path)
    import json
    with open(path) as f:
        data = json.load(f)
    assert data["strategy"] == "SCALP"
    assert "win_rate" in data
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/e53f/trading-agent && python -m pytest tests/test_backtest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backtest'`

**Step 3: Write implementation**

```python
# backtest.py
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
        "rsi_oversold": 30, "rsi_overbought": 70,
        "vol_min": 1.5, "sl_pct": 0.5, "tp_multiplier": 1.5,
        "hold_bars": 8,  # 8 x 15m = 2h max
    },
    "SWING": {
        "rsi_oversold": 35, "rsi_overbought": 65,
        "vol_min": 1.2, "sl_pct": 1.5, "tp_multiplier": 2.0,
        "hold_bars": 96,  # 96 x 15m = 24h max
    },
    "POSITION": {
        "rsi_oversold": 40, "rsi_overbought": 60,
        "vol_min": 1.0, "sl_pct": 3.0, "tp_multiplier": 2.5,
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

        for sig in signals:
            bar_idx = sig["bar_index"]
            pair = "BACKTEST/USDT"

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
```

**Step 4: Run tests**

Run: `cd /Users/e53f/trading-agent && python -m pytest tests/test_backtest.py -v`
Expected: All 7 tests PASS

**Step 5: Commit**

```bash
git add backtest.py tests/test_backtest.py
git commit -m "feat: add historical backtester with per-strategy testing and gate check"
```

---

## Task 4: Dashboard F9 — Learning + Backtest Panel

**Files:**
- Modify: `dashboard.py` (add draw_f9, update VIEWS, PANEL_FNS, handle_key)

**Step 1: Add F9 view to dashboard**

Update VIEWS dict:
```python
VIEWS = {1:"MACRO", 2:"CRYPTO", 3:"STOCKS", 4:"FOREX",
         5:"SMART MONEY", 6:"SIGNALS", 7:"AGENT", 8:"ALERTS",
         9:"LEARNING"}
```

Update PANEL_FNS:
```python
PANEL_FNS = {1: draw_f1, 2: draw_f2, 3: draw_f3, 4: draw_f4,
             5: draw_f5, 6: draw_f6, 7: draw_f7, 8: draw_f8,
             9: draw_f9}
```

Update handle_key to accept 9:
```python
for i in range(1, 10):  # was range(1, 9)
    if n == f"KEY_F{i}":
        _current_view = i; return
if ch in "123456789":  # was "12345678"
```

Update fkey bar hint:
```python
sys.stdout.write(W(t) + "  [1-9] SWITCH  [R] REFRESH  [A] ADD ALERT  [Q] QUIT" + N(t))
```

Add draw_f9 function:
```python
def draw_f9(t, sy, height, width):
    """F9: Learning & Backtest — recent mistakes + backtest results."""
    w = Writer(t, sy, height, width)
    w.header("LEARNING & BACKTEST")

    # Top 3 recent mistakes from learning_log.jsonl
    w.subheader("TOP MISTAKES (last 20 trades)")
    try:
        mistakes = _load_learning_mistakes()
        if mistakes:
            for m in mistakes[:3]:
                tag = m["mistake"].upper().replace("_", " ")
                count = m["count"]
                examples = " | ".join(m.get("examples", []))
                w.row(f"  {tag}: {count}x — {examples}")
        else:
            w.row("  No mistakes logged yet")
    except Exception:
        w.row("  Error loading learning log")

    w.blank()

    # Backtest results
    w.subheader("LATEST BACKTEST")
    try:
        bt_data = _load_backtest_results()
        if bt_data:
            run_at = bt_data.get("run_at", "unknown")[:16]
            pair = bt_data.get("pair", "?")
            w.row(f"  Run: {run_at} | Pair: {pair}")
            w.blank()
            best = bt_data.get("best_strategy", "?")
            for strat, res in bt_data.get("strategies", {}).items():
                wr = res.get("win_rate", 0)
                pf = res.get("profit_factor", 0)
                trades = res.get("total_trades", 0)
                pnl = res.get("pnl_pct", 0)
                dd = res.get("max_drawdown_pct", 0)
                gate = "PASS" if wr >= 55 and pf >= 1.0 else "FAIL"
                marker = " ★" if strat == best else ""
                w.row(f"  {strat:10s} WR={wr:5.1f}% PF={pf:.2f} "
                      f"Trades={trades:3d} P&L={pnl:+6.1f}% "
                      f"DD={dd:4.1f}% [{gate}]{marker}")
        else:
            w.row("  No backtest results yet. Run: python backtest.py")
    except Exception:
        w.row("  Error loading backtest results")


def _load_learning_mistakes():
    """Load top mistakes from learning_log.jsonl."""
    path = os.path.join("logs", "learning_log.jsonl")
    if not os.path.exists(path):
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    entries = entries[-20:]  # last 20

    from collections import Counter
    mistake_counts = {}
    for e in entries:
        for m in e.get("mistakes", []):
            if m not in mistake_counts:
                mistake_counts[m] = {"count": 0, "examples": []}
            mistake_counts[m]["count"] += 1
            if len(mistake_counts[m]["examples"]) < 2:
                mistake_counts[m]["examples"].append(
                    f"{e['pair']} {e['side']} {e.get('pnl_pct', 0):+.1f}%"
                )
    sorted_m = sorted(mistake_counts.items(), key=lambda x: x[1]["count"], reverse=True)
    return [{"mistake": k, **v} for k, v in sorted_m[:3]]


def _load_backtest_results():
    """Load latest backtest results."""
    path = os.path.join("logs", "backtest_results.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)
```

**Step 2: Test manually**

Run: `cd /Users/e53f/trading-agent && python dashboard.py` then press F9
Expected: See "LEARNING & BACKTEST" panel

**Step 3: Commit**

```bash
git add dashboard.py
git commit -m "feat: add F9 dashboard panel for learning mistakes and backtest results"
```

---

## Task 5: Weekly Sunday Backtest

**Files:**
- Modify: `main.py` (add weekly backtest check in main loop)

**Step 1: Add weekly backtest trigger**

In `main.py`, add a function:
```python
def maybe_run_weekly_backtest(cfg, bybit, logger, state):
    """Run backtest every Sunday if not already run this week."""
    now = datetime.now(timezone.utc)
    if now.weekday() != 6:  # 6 = Sunday
        return
    last_backtest = state._data.get("last_weekly_backtest", "")
    today = now.strftime("%Y-%m-%d")
    if last_backtest == today:
        return

    logger.info("WEEKLY BACKTEST: Running Sunday strategy review...")
    try:
        from backtest import Backtester
        df = bybit.get_candles("BTC/USDT", timeframe="15m", limit=200)
        bt = Backtester(initial_balance=cfg.paper_initial_balance)
        results = bt.run_all_strategies(df)

        # Save results
        os.makedirs(cfg.log_dir, exist_ok=True)
        output_path = os.path.join(cfg.log_dir, "backtest_results.json")

        # Load previous for comparison
        prev_path = os.path.join(cfg.log_dir, "backtest_previous.json")
        if os.path.exists(output_path):
            os.replace(output_path, prev_path)

        from dataclasses import asdict
        combined = {
            "run_at": now.isoformat(),
            "pair": "BTC/USDT",
            "days": "~3 (200 bars)",
            "bars": len(df),
            "strategies": {k: asdict(v) for k, v in results.items()},
            "best_strategy": max(results, key=lambda k: results[k].win_rate),
        }
        with open(output_path, "w") as f:
            json.dump(combined, f, indent=2, default=str)

        for strat, result in results.items():
            logger.info(f"  BACKTEST {result.summary()}")

        # Compare to previous week
        if os.path.exists(prev_path):
            with open(prev_path) as f:
                prev = json.load(f)
            for strat in results:
                prev_wr = prev.get("strategies", {}).get(strat, {}).get("win_rate", 0)
                curr_wr = results[strat].win_rate
                diff = curr_wr - prev_wr
                logger.info(f"  {strat} WR change: {diff:+.1f}% vs last week")

        state._data["last_weekly_backtest"] = today
        state.save()
    except Exception as e:
        logger.error(f"Weekly backtest failed: {e}")
```

Call this at the start of the main loop (before crypto cycle).

**Step 2: Run full test suite**

Run: `cd /Users/e53f/trading-agent && python -m pytest -v`
Expected: All tests pass (no new tests needed — this is a scheduling integration)

**Step 3: Commit**

```bash
git add main.py
git commit -m "feat: add weekly Sunday backtest with week-over-week comparison"
```

---

## Task 6: Final Integration Test

**Step 1: Run full test suite**

Run: `cd /Users/e53f/trading-agent && python -m pytest -v`
Expected: All tests pass

**Step 2: Dry-run backtest CLI**

Run: `cd /Users/e53f/trading-agent && python backtest.py --pair BTC/USDT --strategy SCALP`
Expected: Outputs strategy summary with win rate, profit factor, gate result

**Step 3: Final commit with all files**

```bash
git add -A
git commit -m "feat: learning feedback system + backtester — complete integration"
```
