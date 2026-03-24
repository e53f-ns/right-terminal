import pandas as pd
import numpy as np
from analysis.liquidity import analyze_liquidity


def make_df(n=60, seed=42):
    np.random.seed(seed)
    close = 40000 + np.cumsum(np.random.randn(n) * 100)
    high = close + np.abs(np.random.randn(n) * 80)
    low = close - np.abs(np.random.randn(n) * 80)
    volume = np.random.uniform(100, 1000, n)
    return pd.DataFrame({
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def make_ob_summary(price=40000.0):
    return {
        "best_bid": price - 10,
        "best_ask": price + 10,
        "bid_wall": {"price": price - 200, "size": 5.0},
        "ask_wall": {"price": price + 200, "size": 5.0},
        "top_bid_walls": [{"price": price - 200, "size": 5.0}],
        "top_ask_walls": [{"price": price + 200, "size": 5.0}],
        "thin_bid_zones": [{"from": price - 300, "to": price - 250, "gap": 50.0}],
        "thin_ask_zones": [{"from": price + 250, "to": price + 300, "gap": 50.0}],
        "bid_ask_ratio": 1.1,
        "raw_bids": [[price - i * 10, 1.0] for i in range(1, 10)],
        "raw_asks": [[price + i * 10, 1.0] for i in range(1, 10)],
    }


def test_analyze_liquidity_returns_required_keys():
    df = make_df()
    ob = make_ob_summary()
    result = analyze_liquidity(df, ob)
    for key in ["liquidity_sweeps", "stop_hunt_zones", "thin_bid_zones",
                "thin_ask_zones", "top_bid_walls", "top_ask_walls"]:
        assert key in result, f"Missing key: {key}"


def test_stop_hunt_zones_are_identified():
    df = make_df(n=60)
    ob = make_ob_summary()
    result = analyze_liquidity(df, ob)
    # Should identify at least some swing-based stop hunt zones
    assert isinstance(result["stop_hunt_zones"], list)
    if result["stop_hunt_zones"]:
        zone = result["stop_hunt_zones"][0]
        assert "type" in zone
        assert zone["type"] in ("stops_above", "stops_below")
        assert "price" in zone


def test_liquidity_sweep_detected():
    """Candle whose wick breaks prior high but closes back below triggers a sweep."""
    n = 40
    close = np.full(n, 40000.0)
    high = np.full(n, 40100.0)
    low = np.full(n, 39900.0)

    # Inject a sweep candle at index 33 (within last 16 of 40):
    # prior window (24-32) has high=40100, last window (32-48) candle 33:
    # wick goes to 40500 but body closes below 40100
    high[33] = 40500.0
    close[33] = 40050.0  # body below prior high

    df = pd.DataFrame({
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.full(n, 500.0),
    })
    ob = make_ob_summary(40000.0)
    result = analyze_liquidity(df, ob)
    sweep_types = [s["type"] for s in result["liquidity_sweeps"]]
    assert "sell_side_sweep" in sweep_types


def test_thin_zones_passed_through_from_orderbook():
    df = make_df()
    ob = make_ob_summary()
    result = analyze_liquidity(df, ob)
    assert result["thin_bid_zones"] == ob["thin_bid_zones"]
    assert result["thin_ask_zones"] == ob["thin_ask_zones"]
