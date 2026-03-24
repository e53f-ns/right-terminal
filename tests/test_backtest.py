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
