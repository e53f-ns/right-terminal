import pytest
from unittest.mock import patch, MagicMock

def test_coingecko_fear_greed():
    from data.coingecko import CoinGeckoClient
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [{"value": "62", "value_classification": "Greed",
                  "timestamp": "1700000000"}]
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.coingecko.requests.get", return_value=mock_resp):
        client = CoinGeckoClient()
        fg = client.get_fear_greed()
    assert fg["value"] == 62
    assert fg["label"] == "Greed"

def test_coingecko_btc_dominance():
    from data.coingecko import CoinGeckoClient
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {"market_cap_percentage": {"btc": 52.3}}
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.coingecko.requests.get", return_value=mock_resp):
        client = CoinGeckoClient()
        dom = client.get_btc_dominance()
    assert dom == 52.3

def test_cryptopanic_returns_headlines():
    from data.cryptopanic import CryptoPanicClient
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {"title": "BTC hits new high", "votes": {"positive": 10, "negative": 2},
             "currencies": [{"code": "BTC"}]},
        ]
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.cryptopanic.requests.get", return_value=mock_resp):
        client = CryptoPanicClient(api_key="test")
        news = client.get_news(currencies=["BTC"])
    assert len(news) == 1
    assert news[0]["title"] == "BTC hits new high"
    assert news[0]["sentiment"] == "bullish"

def test_coinglass_liquidation_levels():
    from data.coinglass import CoinglassClient
    # CoinGecko OHLC format: [timestamp, open, high, low, close]
    # Build 5 candles with a clear swing high at index 2 (high=87000) and swing low at index 2 (low=81000)
    mock_resp = MagicMock()
    mock_resp.json.return_value = [
        [1700000000000, 84000, 85000, 83000, 84500],
        [1700003600000, 84500, 86000, 84000, 85500],
        [1700007200000, 85500, 87000, 81000, 83000],  # swing high + swing low
        [1700010800000, 83000, 84000, 82000, 82500],
        [1700014400000, 82500, 83500, 82000, 83000],
    ]
    mock_resp.raise_for_status = MagicMock()
    with patch("data.coinglass.requests.get", return_value=mock_resp):
        client = CoinglassClient()  # no api_key needed
        levels = client.get_liquidation_levels("BTC")
    assert isinstance(levels, list)
    assert all("price" in lv for lv in levels)
    assert all("liq_usdt" in lv for lv in levels)
