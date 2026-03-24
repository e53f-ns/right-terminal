"""Stock data via yfinance — daily OHLCV for major US equities."""

import pandas as pd
import yfinance as yf
from datetime import datetime, timezone


DEFAULT_SYMBOLS = ["AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "SPY", "QQQ"]


class StockClient:
    def __init__(self, symbols: list = None):
        self.symbols = symbols or DEFAULT_SYMBOLS

    def get_candles(self, symbol: str, period: str = "6mo",
                    interval: str = "1d") -> pd.DataFrame:
        """Fetch OHLCV candles for a stock symbol."""
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            raise ValueError(f"No data for {symbol}")
        df = df.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })
        df = df[["open", "high", "low", "close", "volume"]].reset_index()
        df = df.rename(columns={"Date": "timestamp", "Datetime": "timestamp"})
        return df

    def get_fundamentals(self, symbol: str) -> dict:
        """Get key fundamental data for a stock."""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            return {
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "eps": info.get("trailingEps"),
                "market_cap": info.get("marketCap"),
                "sector": info.get("sector", "Unknown"),
                "industry": info.get("industry", "Unknown"),
                "52w_high": info.get("fiftyTwoWeekHigh"),
                "52w_low": info.get("fiftyTwoWeekLow"),
                "recommendation": info.get("recommendationKey", "none"),
                "earnings_date": None,  # yfinance .calendar can be unreliable
            }
        except Exception:
            return {}

    @staticmethod
    def is_market_open() -> bool:
        """Check if US stock market is roughly open (weekday, 9:30-16:00 ET)."""
        now = datetime.now(timezone.utc)
        # Rough check: weekday and between 13:30-20:00 UTC (ET+5h during EST)
        if now.weekday() >= 5:
            return False
        hour = now.hour
        return 13 <= hour <= 20
