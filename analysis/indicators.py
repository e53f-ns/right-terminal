import pandas as pd
import numpy as np

def compute_indicators(df: pd.DataFrame) -> dict:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # RSI (14) — Wilder's EMA smoothing (com=13 ≡ alpha=1/14)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    last_gain = float(gain.iloc[-1])
    last_loss = float(loss.iloc[-1])
    if last_loss == 0:
        rsi = 100.0
    elif last_gain == 0:
        rsi = 0.0
    else:
        rs = last_gain / last_loss
        rsi = 100 - (100 / (1 + rs))

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()

    # Bollinger Bands (20, 2)
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()

    # EMAs
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

    # VWAP (rolling session approximation over available data)
    tp = (high + low + close) / 3
    vwap = float((tp * volume).cumsum().iloc[-1] / volume.cumsum().iloc[-1])

    # Volume analysis
    volume_current = float(volume.iloc[-1])
    volume_avg_20 = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean())
    volume_ratio = round(volume_current / volume_avg_20, 2) if volume_avg_20 > 0 else 1.0
    volume_spike = volume_ratio > 2.0
    last5 = volume.iloc[-5:].tolist()
    if len(last5) >= 5:
        first_half = sum(last5[:2]) / 2
        second_half = sum(last5[3:]) / 2
        if second_half > first_half * 1.1:
            volume_trend = "increasing"
        elif second_half < first_half * 0.9:
            volume_trend = "decreasing"
        else:
            volume_trend = "flat"
    else:
        volume_trend = "flat"

    return {
        "rsi": round(rsi, 2),
        "macd": round(float(macd_line.iloc[-1]), 4),
        "macd_signal": round(float(signal.iloc[-1]), 4),
        "macd_hist": round(float((macd_line - signal).iloc[-1]), 4),
        "bb_upper": round(float((sma20 + 2 * std20).iloc[-1]), 2),
        "bb_lower": round(float((sma20 - 2 * std20).iloc[-1]), 2),
        "bb_mid": round(float(sma20.iloc[-1]), 2),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "ema200": round(ema200, 2),
        "vwap": round(vwap, 2),
        "current_price": round(float(close.iloc[-1]), 2),
        "volume_current": round(volume_current, 4),
        "volume_avg_20": round(volume_avg_20, 4),
        "volume_ratio": volume_ratio,
        "volume_spike": volume_spike,
        "volume_trend": volume_trend,
    }
