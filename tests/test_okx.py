"""Tests for OKXClient — all exchange calls are mocked."""
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from data.okx import OKXClient


@pytest.fixture
def client():
    with patch("data.okx.ccxt.okx") as mock_cls:
        mock_cls.return_value = MagicMock()
        c = OKXClient(api_key="k", secret="s", passphrase="p")
        c._exchange = MagicMock()
        return c


def _make_ohlcv(n=5):
    ts = 1700000000000
    return [[ts + i * 900_000, 100 + i, 101 + i, 99 + i, 100 + i, 1000 + i]
            for i in range(n)]


def test_to_perp():
    assert OKXClient.to_perp("BTC/USDT") == "BTC/USDT:USDT"
    assert OKXClient.to_perp("ETH/USDT") == "ETH/USDT:USDT"


def test_get_candles_returns_dataframe(client):
    client._exchange.fetch_ohlcv.return_value = _make_ohlcv(10)
    df = client.get_candles("BTC/USDT")
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(df) == 10


def test_get_best_price(client):
    client._exchange.fetch_ticker.return_value = {"bid": 84000.0, "ask": 84001.0, "last": 84000.5}
    result = client.get_best_price("BTC/USDT")
    assert result["best_bid"] == 84000.0
    assert result["best_ask"] == 84001.0


def test_get_orderbook_summary(client):
    client._exchange.fetch_order_book.return_value = {
        "bids": [[84000, 1.0], [83990, 0.5], [83980, 2.0]],
        "asks": [[84010, 0.8], [84020, 0.3], [84030, 1.5]],
    }
    result = client.get_orderbook_summary("BTC/USDT")
    assert result["best_bid"] == 84000
    assert result["best_ask"] == 84010
    assert "bid_ask_ratio" in result
    assert "top_bid_walls" in result


def test_get_funding_rate(client):
    client._exchange.fetch_funding_rate.return_value = {"fundingRate": 0.0001}
    rate = client.get_funding_rate("BTC/USDT:USDT")
    assert rate == 0.0001


def test_get_funding_rate_exception_returns_zero(client):
    client._exchange.fetch_funding_rate.side_effect = Exception("not available")
    rate = client.get_funding_rate("BTC/USDT:USDT")
    assert rate == 0.0


def test_get_balance(client):
    client._exchange.fetch_balance.return_value = {
        "USDT": {"free": 5000.0, "total": 5500.0}
    }
    bal = client.get_balance()
    assert bal["USDT"] == 5000.0
    assert bal["total"] == 5500.0


def test_get_open_interest_returns_none_on_error(client):
    client._exchange.fetch_open_interest.side_effect = Exception("unsupported")
    result = client.get_open_interest("BTC/USDT:USDT")
    assert result is None
