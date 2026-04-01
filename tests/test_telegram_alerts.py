"""Tests for TelegramAlerter — all network calls are mocked."""
import pytest
from unittest.mock import patch, MagicMock
from utils.telegram_alerts import TelegramAlerter


@pytest.fixture
def alerter():
    return TelegramAlerter(token="TEST_TOKEN", chat_id="12345")


@pytest.fixture
def disabled_alerter():
    return TelegramAlerter(token="", chat_id="")


def mock_post_ok(*args, **kwargs):
    m = MagicMock()
    m.ok = True
    return m


def mock_post_fail(*args, **kwargs):
    m = MagicMock()
    m.ok = False
    m.status_code = 400
    m.text = "Bad Request"
    return m


def test_disabled_alerter_send_returns_false(disabled_alerter):
    assert disabled_alerter.send("test") is False


def test_enabled_alerter_sends_message(alerter):
    with patch("utils.telegram_alerts.requests.post", side_effect=mock_post_ok) as mock:
        result = alerter.send("Hello test")
    assert result is True
    mock.assert_called_once()
    call_json = mock.call_args.kwargs["json"]
    assert call_json["text"] == "Hello test"
    assert call_json["chat_id"] == "12345"


def test_send_returns_false_on_bad_response(alerter):
    with patch("utils.telegram_alerts.requests.post", side_effect=mock_post_fail):
        result = alerter.send("test")
    assert result is False


def test_trade_opened_formats_correctly(alerter):
    with patch("utils.telegram_alerts.requests.post", side_effect=mock_post_ok) as mock:
        alerter.trade_opened(
            pair="BTC/USDT", side="long", entry=84000.0,
            sl=82500.0, tp=87000.0, leverage=5,
            confidence=78, strategy="SCALP", balance=10000.0
        )
    text = mock.call_args.kwargs["json"]["text"]
    assert "BTC/USDT" in text
    assert "LONG" in text
    assert "78%" in text
    assert "5x" in text


def test_trade_closed_win(alerter):
    with patch("utils.telegram_alerts.requests.post", side_effect=mock_post_ok) as mock:
        alerter.trade_closed(
            pair="SOL/USDT", side="short",
            pnl_usdt=28.70, pnl_pct=2.1,
            close_reason="TP hit", total_balance=10228.70,
            total_pnl_pct=2.3
        )
    text = mock.call_args.kwargs["json"]["text"]
    assert "WIN" in text
    assert "SOL/USDT" in text
    assert "+28.70" in text


def test_trade_closed_loss(alerter):
    with patch("utils.telegram_alerts.requests.post", side_effect=mock_post_ok) as mock:
        alerter.trade_closed(
            pair="ETH/USDT", side="long",
            pnl_usdt=-4.34, pnl_pct=-0.5,
            close_reason="SL hit", total_balance=9995.66,
            total_pnl_pct=-0.04
        )
    text = mock.call_args.kwargs["json"]["text"]
    assert "LOSS" in text
    assert "ETH/USDT" in text
    assert "-4.34" in text


def test_circuit_breaker(alerter):
    with patch("utils.telegram_alerts.requests.post", side_effect=mock_post_ok) as mock:
        alerter.circuit_breaker("3 consecutive losses", "14:00 UTC")
    text = mock.call_args.kwargs["json"]["text"]
    assert "PAUSED" in text
    assert "3 consecutive losses" in text


def test_daily_summary(alerter):
    with patch("utils.telegram_alerts.requests.post", side_effect=mock_post_ok) as mock:
        alerter.daily_summary(
            trades=8, wins=5, losses=3,
            pnl_usdt=42.5, pnl_pct=0.43, balance=10042.5
        )
    text = mock.call_args.kwargs["json"]["text"]
    assert "DAILY SUMMARY" in text
    assert "W:5" in text
    assert "L:3" in text


def test_handle_status_command(alerter):
    state = MagicMock()
    state.paper_balance = 10219.5
    state.total_trades = 28
    state.winning_trades = 14
    state._data = {
        "open_positions": {
            "BTC/USDT": {
                "side": "long", "entry": 84000.0,
                "leverage": 5, "confidence": 80, "strategy": "SCALP"
            }
        },
        "stock_positions": {},
        "forex_positions": {},
        "daily_pnl_pct": 1.2,
        "paused_until": "",
    }

    alerter._state = state
    with patch("utils.telegram_alerts.requests.post", side_effect=mock_post_ok) as mock:
        alerter._handle_status()
    text = mock.call_args.kwargs["json"]["text"]
    assert "STATUS" in text
    assert "10,219.50" in text
    assert "BTC/USDT" in text


def test_handle_pause_sets_state(alerter):
    state = MagicMock()
    state._data = {}
    alerter._state = state
    alerter._agent_logger = MagicMock()

    with patch("utils.telegram_alerts.requests.post", side_effect=mock_post_ok):
        alerter._handle_pause()

    assert "paused_until" in state._data
    state.save.assert_called_once()


def test_handle_resume_sets_state(alerter):
    state = MagicMock()
    state._data = {"paused_until": "2099-01-01T00:00:00+00:00"}
    alerter._state = state
    alerter._agent_logger = MagicMock()

    with patch("utils.telegram_alerts.requests.post", side_effect=mock_post_ok):
        alerter._handle_resume()

    state.save.assert_called_once()


def test_network_error_handled_gracefully(alerter):
    import requests as req
    with patch("utils.telegram_alerts.requests.post",
               side_effect=req.RequestException("timeout")):
        result = alerter.send("test")
    assert result is False
