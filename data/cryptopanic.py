import requests
from typing import List

BASE_URL = "https://cryptopanic.com/api/v1/posts/"

class CryptoPanicClient:
    def __init__(self, api_key: str):
        self._key = api_key

    def get_news(self, currencies: List[str] = None, limit: int = 10) -> List[dict]:
        params = {"auth_token": self._key, "public": "true", "limit": limit}
        if currencies:
            params["currencies"] = ",".join(currencies)
        resp = requests.get(BASE_URL, params=params, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        news = []
        for item in results:
            votes = item.get("votes", {})
            pos = votes.get("positive", 0)
            neg = votes.get("negative", 0)
            sentiment = "bullish" if pos > neg else ("bearish" if neg > pos else "neutral")
            news.append({
                "title": item["title"],
                "sentiment": sentiment,
                "positive_votes": pos,
                "negative_votes": neg,
                "currencies": [c["code"] for c in item.get("currencies", [])],
            })
        return news
