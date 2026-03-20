import requests

FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=7"
GLOBAL_URL = "https://api.coingecko.com/api/v3/global"

class CoinGeckoClient:
    def get_fear_greed(self) -> dict:
        resp = requests.get(FEAR_GREED_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()["data"]
        latest = data[0]
        history = [int(d["value"]) for d in data]
        trend = "rising" if history[0] > history[-1] else "falling"
        return {
            "value": int(latest["value"]),
            "label": latest["value_classification"],
            "trend": trend,
            "history_7d": history,
        }

    def get_btc_dominance(self) -> float:
        resp = requests.get(GLOBAL_URL, timeout=10)
        resp.raise_for_status()
        return round(resp.json()["data"]["market_cap_percentage"].get("btc", 0), 2)
