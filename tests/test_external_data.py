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
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {"liqLevels": [
            {"price": 85000, "liqUsdt": 50000000},
            {"price": 80000, "liqUsdt": 30000000},
        ]}
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.coinglass.requests.get", return_value=mock_resp):
        client = CoinglassClient(api_key="test")
        levels = client.get_liquidation_levels("BTC")
    assert len(levels) == 2
    assert levels[0]["price"] == 85000
