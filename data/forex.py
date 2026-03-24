"""Forex data via yfinance — hourly/4h candles for major pairs."""

import pandas as pd
import yfinance as yf
from datetime import datetime, timezone


DEFAULT_PAIRS = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCHF=X"]

# Central bank policy context (updated manually or via news)
CB_STANCE = {
    "EUR": {"bank": "ECB", "stance": "neutral", "rate": 2.65},
    "GBP": {"bank": "BoE", "stance": "hawkish", "rate": 4.50},
    "USD": {"bank": "Fed", "stance": "hawkish", "rate": 4.50},
    "JPY": {"bank": "BoJ", "stance": "dovish", "rate": 0.50},
    "AUD": {"bank": "RBA", "stance": "neutral", "rate": 4.10},
    "CHF": {"bank": "SNB", "stance": "dovish", "rate": 0.50},
}


class ForexClient:
    def __init__(self, pairs: list = None):
        self.pairs = pairs or DEFAULT_PAIRS

    def get_candles(self, pair: str, period: str = "60d",
                    interval: str = "1h") -> pd.DataFrame:
        """Fetch OHLCV candles for a forex pair."""
        ticker = yf.Ticker(pair)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            raise ValueError(f"No data for {pair}")
        df = df.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })
        df = df[["open", "high", "low", "close", "volume"]].reset_index()
        df = df.rename(columns={"Date": "timestamp", "Datetime": "timestamp"})
        return df

    @staticmethod
    def pair_to_display(yf_pair: str) -> str:
        """Convert 'EURUSD=X' to 'EUR/USD'."""
        clean = yf_pair.replace("=X", "")
        return f"{clean[:3]}/{clean[3:]}"

    @staticmethod
    def get_rate_differential(yf_pair: str) -> dict:
        """Get interest rate differential and central bank stances."""
        clean = yf_pair.replace("=X", "")
        base_ccy = clean[:3]
        quote_ccy = clean[3:]
        base_info = CB_STANCE.get(base_ccy, {"bank": "?", "stance": "neutral", "rate": 0})
        quote_info = CB_STANCE.get(quote_ccy, {"bank": "?", "stance": "neutral", "rate": 0})
        return {
            "base": base_ccy,
            "quote": quote_ccy,
            "base_rate": base_info["rate"],
            "quote_rate": quote_info["rate"],
            "differential": round(base_info["rate"] - quote_info["rate"], 2),
            "base_stance": base_info["stance"],
            "quote_stance": quote_info["stance"],
            "base_bank": base_info["bank"],
            "quote_bank": quote_info["bank"],
        }

    @staticmethod
    def is_market_open() -> bool:
        """Forex trades 24/5 — closed Sat/Sun."""
        now = datetime.now(timezone.utc)
        # Closed from Friday 21:00 UTC to Sunday 21:00 UTC
        if now.weekday() == 5:  # Saturday
            return False
        if now.weekday() == 6 and now.hour < 21:  # Sunday before 21:00
            return False
        if now.weekday() == 4 and now.hour >= 21:  # Friday after 21:00
            return False
        return True
