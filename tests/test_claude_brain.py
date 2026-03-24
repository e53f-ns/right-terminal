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
                       "ema200": 80000, "vwap": 83900, "current_price": 84000,
                       "volume_current": 100, "volume_avg_20": 80,
                       "volume_ratio": 1.25, "volume_spike": False,
                       "volume_trend": "flat"},
        "smc": {"order_blocks": [], "fvgs": [], "swing_highs": [85000],
                "swing_lows": [82000], "bos_detected": True,
                "choch_detected": False, "premium_zone": 85500,
                "discount_zone": 82500, "liquidity_above": [85000],
                "liquidity_below": [82000], "recent_range_high": 86000,
                "recent_range_low": 82000},
        "orderbook": {"best_bid": 83990, "best_ask": 84010,
                      "bid_wall": {"price": 83500, "size": 50},
                      "ask_wall": {"price": 84500, "size": 40},
                      "bid_ask_ratio": 1.2,
                      "top_bid_walls": [], "top_ask_walls": [],
                      "thin_bid_zones": [], "thin_ask_zones": []},
        "funding_rate": 0.0001,
        "open_interest": 5000000000,
        "fear_greed": {"value": 65, "label": "Greed", "trend": "rising"},
        "btc_dominance": 52.3,
        "news": [{"title": "BTC breaks ATH", "sentiment": "bullish",
                  "positive_votes": 10, "negative_votes": 1, "currencies": ["BTC"]}],
        "liquidation_levels": [{"price": 85000, "liq_usdt": 50000000}],
        "liquidity_analysis": {"liquidity_sweeps": [], "stop_hunt_zones": [],
                               "thin_bid_zones": [], "thin_ask_zones": [],
                               "top_bid_walls": [], "top_ask_walls": []},
        "open_positions": {},
        "remaining_exposure_pct": 60.0,
        "multi_timeframe": {},
    }


# New JSON format response
MOCK_CLAUDE_RESPONSE_JSON = '{"action":"BUY","confidence":75,"entry":84000,"sl":83200,"tp":85200,"strategy":"SCALP","leverage":2,"reason":"BOS breakout with volume"}'

# Old format for backward compat
MOCK_CLAUDE_RESPONSE_OLD = """
[STRATEGY]: SCALP
[DECISION]: BUY
[SIGNAL SCORE]: 75
[ENTRY]: $84,000
[STOP LOSS]: $83,200
[TARGET 1]: $85,200
[R:R RATIO]: 1:2.5
[THESIS]: BOS confirmed bullish momentum.
[SMART MONEY BIAS]: Bullish
"""


def test_parse_json_response():
    brain = ClaudeBrain(api_key="test", model="claude-opus-4-6")
    signal = brain._parse_response(MOCK_CLAUDE_RESPONSE_JSON, current_price=84000.0, pair="BTC/USDT")
    assert signal.decision == "BUY"
    assert signal.strategy == "SCALP"
    assert signal.confidence == 75
    assert signal.entry == 84000.0
    assert signal.stop_loss == 83200.0
    assert signal.take_profit == 85200.0
    assert signal.leverage == 2


def test_parse_json_with_surrounding_text():
    brain = ClaudeBrain(api_key="test", model="claude-opus-4-6")
    text = 'Here is my analysis: {"action":"SELL","confidence":80,"entry":85000,"sl":86000,"tp":83000,"strategy":"SWING","leverage":1,"reason":"distribution"} done.'
    signal = brain._parse_response(text, current_price=85000.0, pair="ETH/USDT")
    assert signal.decision == "SELL"
    assert signal.confidence == 80
    assert signal.strategy == "SCALP"  # forced SCALP only


def test_parse_invalid_json_falls_back():
    brain = ClaudeBrain(api_key="test", model="claude-opus-4-6")
    text = "This is not valid JSON but contains BUY signal with 65% confidence"
    signal = brain._parse_response(text, current_price=84000.0, pair="BTC/USDT")
    assert signal.decision == "BUY"
    assert signal.confidence == 65


def test_parse_garbage_returns_hold():
    brain = ClaudeBrain(api_key="test", model="claude-opus-4-6")
    signal = brain._parse_response("random gibberish no signals", current_price=84000.0, pair="BTC/USDT")
    assert signal.decision == "HOLD"
    assert signal.confidence == 0


def test_analyze_calls_claude_api():
    brain = ClaudeBrain(api_key="test", model="claude-opus-4-6")

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=MOCK_CLAUDE_RESPONSE_JSON)]
    mock_client.messages.create.return_value = mock_message
    brain._client = mock_client

    ctx = make_mock_context()
    signal = brain.analyze(ctx)
    assert signal.decision == "BUY"
    assert signal.strategy == "SCALP"
    assert signal.confidence == 75
    assert mock_client.messages.create.called


def test_analyze_api_error_returns_hold():
    brain = ClaudeBrain(api_key="test", model="claude-opus-4-6")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("credit balance too low")
    brain._client = mock_client

    ctx = make_mock_context()
    signal = brain.analyze(ctx)
    assert signal.decision == "HOLD"
    assert "API error" in signal.reasoning


def test_hold_signal_factory():
    signal = ClaudeBrain._hold_signal("BTC/USDT", "test reason")
    assert signal.decision == "HOLD"
    assert signal.pair == "BTC/USDT"
    assert signal.confidence == 0


def test_compact_prompt_format():
    brain = ClaudeBrain(api_key="test", model="claude-opus-4-6")
    ctx = make_mock_context()
    prompt = brain._build_prompt(ctx)
    # Should be compact — under 600 tokens (~2400 chars)
    assert len(prompt) < 2400
    assert "MARKET:" in prompt
    assert "TREND:" in prompt
    assert "MOMENTUM:" in prompt
    assert "BTC/USDT" in prompt
