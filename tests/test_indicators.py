import pandas as pd
import numpy as np
import pytest
from analysis.indicators import compute_indicators

def make_df(n=200):
    np.random.seed(42)
    close = 40000 + np.cumsum(np.random.randn(n) * 100)
    high = close + np.abs(np.random.randn(n) * 50)
    low = close - np.abs(np.random.randn(n) * 50)
    volume = np.random.uniform(100, 1000, n)
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": volume})

def test_indicators_returns_expected_keys():
    df = make_df()
    result = compute_indicators(df)
    for key in ["rsi", "macd", "macd_signal", "macd_hist",
                "bb_upper", "bb_lower", "bb_mid",
                "ema20", "ema50", "ema200", "vwap",
                "volume_current", "volume_avg_20", "volume_ratio",
                "volume_spike", "volume_trend"]:
        assert key in result, f"Missing key: {key}"

def test_volume_spike_detected_on_high_volume():
    df = make_df(50)
    # Make last candle volume 10x the average
    df.iloc[-1, df.columns.get_loc("volume")] = df["volume"].iloc[:-1].mean() * 10
    result = compute_indicators(df)
    assert result["volume_spike"] is True
    assert result["volume_ratio"] > 2.0

def test_volume_trend_increasing():
    n = 200
    np.random.seed(0)
    close = 40000 + np.cumsum(np.random.randn(n) * 100)
    # Strictly increasing volume over last 5 candles
    volume = np.ones(n) * 100.0
    volume[-5:] = [100, 150, 200, 300, 500]
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                       "close": close, "volume": volume})
    result = compute_indicators(df)
    assert result["volume_trend"] == "increasing"

def test_rsi_bounds():
    df = make_df(300)
    result = compute_indicators(df)
    assert 0 <= result["rsi"] <= 100

def test_ema_ordering_in_uptrend():
    close = pd.Series([float(i) for i in range(1, 300)])
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                       "close": close, "volume": pd.Series([100.0] * 299)})
    result = compute_indicators(df)
    assert result["ema20"] > result["ema50"] > result["ema200"]
