import pytest
from unittest.mock import MagicMock
from analysis.claude_brain import ClaudeBrain, TradeSignal


def make_mock_context():
    return {
        "pair": "BTC/USDT",
        "price": 84000.0,
        "indicators": {"rsi": 55.0, "macd": 120.0, "macd_signal": 100.0,
                       "macd_hist": 20.0, "bb_upper": 86000, "bb_lower": 82000,
                       "bb_mid": 84000, "ema20": 83800, "ema50": 83000,
                       "ema200": 80000, "vwap": 83900, "current_price": 84000},
        "smc": {"order_blocks": [], "fvgs": [], "swing_highs": [85000],
                "swing_lows": [82000], "bos_detected": True,
                "choch_detected": False, "premium_zone": 85500,
                "discount_zone": 82500, "liquidity_above": [85000],
                "liquidity_below": [82000], "recent_range_high": 86000,
                "recent_range_low": 82000},
        "orderbook": {"best_bid": 83990, "best_ask": 84010,
                      "bid_wall": {"price": 83500, "size": 50},
                      "ask_wall": {"price": 84500, "size": 40},
                      "bid_ask_ratio": 1.2},
        "funding_rate": 0.0001,
        "open_interest": 5000000000,
        "fear_greed": {"value": 65, "label": "Greed", "trend": "rising"},
        "btc_dominance": 52.3,
        "news": [{"title": "BTC breaks ATH", "sentiment": "bullish",
                  "positive_votes": 10, "negative_votes": 1, "currencies": ["BTC"]}],
        "liquidation_levels": [{"price": 85000, "liq_usdt": 50000000}],
        "open_positions": {},
        "remaining_exposure_pct": 15.0,
    }


MOCK_CLAUDE_RESPONSE = """
[SMART MONEY BIAS]: Bullish
[RETAIL TRAP DETECTED]: No
[WHALE ACTIVITY]: Accumulation detected at discount zone
[PSYCHOLOGY]: Mild greed, not euphoric — healthy uptrend
[DECISION]: BUY
[CONFIDENCE]: 82%
[ENTRY]: $84000 | [STOP-LOSS]: $82500 | [TARGET]: $87000
[RISK/REWARD]: 2:1
[REASONING]: BOS confirmed bullish structure. Price in discount zone with BTC dominance rising indicating altcoin strength. Funding rate neutral, not overextended. FVG above at 84500 as near-term magnet.
"""


def test_parse_claude_output():
    brain = ClaudeBrain(api_key="test", model="claude-opus-4-6")
    signal = brain._parse_response(MOCK_CLAUDE_RESPONSE, current_price=84000.0)
    assert signal.decision == "BUY"
    assert signal.confidence == 82
    assert signal.entry == 84000.0
    assert signal.stop_loss == 82500.0
    assert signal.take_profit == 87000.0
    assert signal.bias == "Bullish"


def test_analyze_calls_claude_api():
    brain = ClaudeBrain(api_key="test", model="claude-opus-4-6")

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=MOCK_CLAUDE_RESPONSE)]
    mock_client.messages.create.return_value = mock_message
    brain._client = mock_client

    ctx = make_mock_context()
    signal = brain.analyze(ctx)
    assert signal.decision == "BUY"
    assert signal.confidence == 82
    assert mock_client.messages.create.called
