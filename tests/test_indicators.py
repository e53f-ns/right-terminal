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
                "ema20", "ema50", "ema200", "vwap"]:
        assert key in result, f"Missing key: {key}"

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
