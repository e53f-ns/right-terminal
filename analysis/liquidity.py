import pandas as pd
from typing import Optional


def analyze_liquidity(df: pd.DataFrame, ob_summary: dict) -> dict:
    """
    Analyze liquidity conditions from OHLC data and orderbook snapshot.

    Returns:
      - liquidity_sweeps: recent wick-based sweeps of prior highs/lows (last 16 candles)
      - thin_bid_zones / thin_ask_zones: price gaps with low resting liquidity
      - top_bid_walls / top_ask_walls: top-3 size walls on each side
      - stop_hunt_zones: price levels where clustered stops are likely sitting
    """
    sweeps = _detect_liquidity_sweeps(df)
    stop_hunt_zones = _estimate_stop_hunt_zones(df)

    return {
        "liquidity_sweeps": sweeps,
        "stop_hunt_zones": stop_hunt_zones,
        "thin_bid_zones": ob_summary.get("thin_bid_zones", []),
        "thin_ask_zones": ob_summary.get("thin_ask_zones", []),
        "top_bid_walls": ob_summary.get("top_bid_walls", []),
        "top_ask_walls": ob_summary.get("top_ask_walls", []),
    }


def _detect_liquidity_sweeps(df: pd.DataFrame) -> list:
    """
    Detect recent liquidity sweeps: candles whose wicks pierce a prior swing
    high/low but close back inside the range (last 16 candles = ~4h at 15m).
    """
    if len(df) < 20:
        return []

    window = df.iloc[-16:]
    prior = df.iloc[-32:-16]  # prior window used to establish swing levels

    if prior.empty:
        return []

    prior_high = float(prior["high"].max())
    prior_low = float(prior["low"].min())

    sweeps = []
    for i, row in window.iterrows():
        candle_open = float(row["open"])
        candle_close = float(row["close"])
        candle_high = float(row["high"])
        candle_low = float(row["low"])
        body_high = max(candle_open, candle_close)
        body_low = min(candle_open, candle_close)

        # Upper wick pierces prior high but body closes back below it
        if candle_high > prior_high and body_high < prior_high:
            sweeps.append({
                "type": "sell_side_sweep",
                "level": round(prior_high, 2),
                "wick_high": round(candle_high, 2),
                "description": f"Upper wick swept ${prior_high:,.2f} resistance, closed back inside",
            })

        # Lower wick pierces prior low but body closes back above it
        if candle_low < prior_low and body_low > prior_low:
            sweeps.append({
                "type": "buy_side_sweep",
                "level": round(prior_low, 2),
                "wick_low": round(candle_low, 2),
                "description": f"Lower wick swept ${prior_low:,.2f} support, closed back inside",
            })

    return sweeps[-3:]  # most recent 3 sweeps


def _estimate_stop_hunt_zones(df: pd.DataFrame) -> list:
    """
    Identify likely stop clusters: swing highs/lows from the last 50 candles
    where retail traders typically place stops (just above swing highs, just below lows).
    """
    if len(df) < 10:
        return []

    recent = df.iloc[-50:]
    zones = []

    # 3-candle pivot detection
    highs = recent["high"].values
    lows = recent["low"].values

    for i in range(1, len(highs) - 1):
        # Swing high: higher than both neighbors
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            zones.append({
                "type": "stops_above",
                "price": round(float(highs[i]), 2),
                "description": f"Stop cluster above swing high at ${highs[i]:,.2f}",
            })
        # Swing low: lower than both neighbors
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            zones.append({
                "type": "stops_below",
                "price": round(float(lows[i]), 2),
                "description": f"Stop cluster below swing low at ${lows[i]:,.2f}",
            })

    # Deduplicate zones within 0.3% of each other, keep most recent
    deduplicated = []
    for zone in reversed(zones):
        price = zone["price"]
        if not any(abs(price - z["price"]) / price < 0.003 for z in deduplicated):
            deduplicated.append(zone)
        if len(deduplicated) >= 4:
            break

    return list(reversed(deduplicated))
