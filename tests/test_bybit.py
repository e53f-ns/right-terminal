import pytest
from unittest.mock import MagicMock, patch
from data.bybit import BybitClient

@pytest.fixture
def mock_exchange():
    exchange = MagicMock()
    exchange.fetch_ohlcv.return_value = [
        [1700000000000, 40000, 41000, 39500, 40500, 1200.5]
    ] * 200
    exchange.fetch_order_book.return_value = {
        "bids": [[40400, 1.5], [40300, 2.0]],
        "asks": [[40600, 1.0], [40700, 3.0]],
    }
    exchange.fetch_funding_rate.return_value = {"fundingRate": 0.0001}
    return exchange

def test_get_candles_returns_dataframe(mock_exchange):
    with patch("data.bybit.ccxt.bybit", return_value=mock_exchange):
        client = BybitClient(api_key="k", secret="s", testnet=False)
        df = client.get_candles("BTC/USDT", timeframe="15m", limit=200)
    assert len(df) == 200
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]

def test_get_orderbook_summary(mock_exchange):
    with patch("data.bybit.ccxt.bybit", return_value=mock_exchange):
        client = BybitClient(api_key="k", secret="s", testnet=False)
        ob = client.get_orderbook_summary("BTC/USDT")
    assert "best_bid" in ob
    assert "best_ask" in ob
    assert "bid_wall" in ob
    assert "ask_wall" in ob

def test_get_funding_rate(mock_exchange):
    with patch("data.bybit.ccxt.bybit", return_value=mock_exchange):
        client = BybitClient(api_key="k", secret="s", testnet=False)
        rate = client.get_funding_rate("BTC/USDT")
    assert rate == 0.0001
