import numpy as np
import pandas as pd
from typing import List


def detect_smc(df: pd.DataFrame) -> dict:
    """
    Detect Smart Money Concepts from OHLCV data.
    Returns structured findings for Claude to reason about.
    """
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    opens = df["open"].values
    volumes = df["volume"].values
    n = len(df)

    # ── Swing Highs / Lows (3-candle pivot) ──────────────────────
    swing_highs = []
    swing_lows = []
    for i in range(1, n - 1):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            swing_highs.append({"idx": i, "price": float(highs[i])})
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            swing_lows.append({"idx": i, "price": float(lows[i])})

    # ── Premium / Discount Zones ──────────────────────────────────
    recent_high = float(max(highs[-50:])) if n >= 50 else float(max(highs))
    recent_low = float(min(lows[-50:])) if n >= 50 else float(min(lows))
    mid = (recent_high + recent_low) / 2
    premium_zone = round((recent_high + mid) / 2, 2)
    discount_zone = round((recent_low + mid) / 2, 2)

    # ── Order Blocks (small candle before a strong directional move) ──
    order_blocks = []
    for i in range(2, n - 1):
        body = abs(closes[i] - opens[i])
        next_move = closes[i + 1] - closes[i]
        avg_body = np.mean([abs(closes[j] - opens[j]) for j in range(max(0, i - 10), i)])
        if avg_body > 0 and body < 0.5 * avg_body and abs(next_move) > 1.5 * avg_body:
            ob_type = "bullish" if next_move > 0 else "bearish"
            order_blocks.append({
                "idx": i,
                "type": ob_type,
                "high": float(highs[i]),
                "low": float(lows[i]),
            })

    # ── Fair Value Gaps (3-candle FVG) ───────────────────────────
    fvgs = []
    for i in range(1, n - 1):
        if lows[i + 1] > highs[i - 1]:  # bullish FVG: gap between candle[i-1] high and candle[i+1] low
            fvgs.append({
                "type": "bullish",
                "top": float(lows[i + 1]),
                "bottom": float(highs[i - 1]),
                "idx": i,
            })
        elif highs[i + 1] < lows[i - 1]:  # bearish FVG
            fvgs.append({
                "type": "bearish",
                "top": float(lows[i - 1]),
                "bottom": float(highs[i + 1]),
                "idx": i,
            })

    # ── BOS / CHOCH Detection ─────────────────────────────────────
    bos_detected = False
    choch_detected = False
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        last_sh = swing_highs[-1]["price"]
        prev_sh = swing_highs[-2]["price"]
        last_sl = swing_lows[-1]["price"]
        prev_sl = swing_lows[-2]["price"]
        current = float(closes[-1])

        # BOS: price breaks previous swing in same direction as trend
        if current > last_sh and last_sh > prev_sh:
            bos_detected = True
        if current < last_sl and last_sl < prev_sl:
            bos_detected = True

        # CHOCH: price breaks previous swing in OPPOSITE direction
        if current > last_sh and last_sh < prev_sh:
            choch_detected = True
        if current < last_sl and last_sl > prev_sl:
            choch_detected = True

    # ── Liquidity Levels (swing points as stop-hunt targets) ──────
    liquidity_above = sorted(
        [sh["price"] for sh in swing_highs if sh["price"] > float(closes[-1])]
    )[:3]
    liquidity_below = sorted(
        [sl["price"] for sl in swing_lows if sl["price"] < float(closes[-1])],
        reverse=True
    )[:3]

    return {
        "order_blocks": order_blocks[-5:],
        "fvgs": fvgs[-5:],
        "swing_highs": [sh["price"] for sh in swing_highs[-5:]],
        "swing_lows": [sl["price"] for sl in swing_lows[-5:]],
        "bos_detected": bos_detected,
        "choch_detected": choch_detected,
        "premium_zone": premium_zone,
        "discount_zone": discount_zone,
        "liquidity_above": liquidity_above,
        "liquidity_below": liquidity_below,
        "recent_range_high": round(recent_high, 2),
        "recent_range_low": round(recent_low, 2),
    }
