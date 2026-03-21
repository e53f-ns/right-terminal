import requests
from typing import List

# CoinGecko free public API — no key required
OHLC_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"

COIN_ID_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
}


class CoinglassClient:
    """
    Estimates key price levels (liquidation zones) using CoinGecko's free
    public OHLC API. No API key required.

    Recent swing highs = short liquidation clusters (price breaks above → short squeezes).
    Recent swing lows  = long liquidation clusters (price breaks below → long liquidations).
    """

    def __init__(self, api_key: str = ""):
        pass  # no longer used — CoinGecko public API needs no key

    def get_liquidation_levels(self, symbol: str) -> List[dict]:
        coin_id = COIN_ID_MAP.get(symbol.upper())
        if not coin_id:
            return []
        try:
            resp = requests.get(
                OHLC_URL.format(coin_id=coin_id),
                params={"vs_currency": "usd", "days": "7"},
                timeout=10,
            )
            resp.raise_for_status()
            ohlc = resp.json()  # [[timestamp, open, high, low, close], ...]
            if not ohlc:
                return []

            # Collect swing highs (3-candle pivot) and swing lows from recent data
            levels = []
            candles = ohlc[-30:] if len(ohlc) >= 30 else ohlc
            highs = [c[2] for c in candles]
            lows = [c[3] for c in candles]

            for i in range(1, len(candles) - 1):
                if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
                    levels.append({"price": round(highs[i], 2), "liq_usdt": 0})
                if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                    levels.append({"price": round(lows[i], 2), "liq_usdt": 0})

            # Deduplicate levels within 0.3% of each other, keep most significant
            levels.sort(key=lambda x: x["price"])
            deduped = []
            for level in levels:
                if not deduped or abs(level["price"] - deduped[-1]["price"]) / deduped[-1]["price"] > 0.003:
                    deduped.append(level)

            return deduped[-10:]  # return up to 10 key levels
        except Exception:
            return []
