import requests
from typing import List

BASE_URL = "https://open-api.coinglass.com/public/v2"

class CoinglassClient:
    def __init__(self, api_key: str):
        self._key = api_key
        self._headers = {"coinglassSecret": api_key}

    def get_liquidation_levels(self, symbol: str) -> List[dict]:
        try:
            resp = requests.get(
                f"{BASE_URL}/liquidation_map",
                headers=self._headers,
                params={"symbol": symbol},
                timeout=10,
            )
            resp.raise_for_status()
            levels = resp.json().get("data", {}).get("liqLevels", [])
            return [{"price": level["price"], "liq_usdt": level["liqUsdt"]} for level in levels]
        except Exception:
            return []
