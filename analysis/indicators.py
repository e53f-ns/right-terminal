import pandas as pd
import numpy as np

def compute_indicators(df: pd.DataFrame) -> dict:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # RSI (14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = float((100 - 100 / (1 + rs)).iloc[-1])

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
    }
