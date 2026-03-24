import json
import time
import pytest
from unittest.mock import patch, MagicMock
from analysis.scenario_engine import ScenarioEngine, ScenarioPrediction


def test_should_run_extreme_fear():
    engine = ScenarioEngine()
    assert engine.should_run(fear_greed=10, fg_24h_change=0)


def test_should_run_extreme_greed():
    engine = ScenarioEngine()
    assert engine.should_run(fear_greed=90, fg_24h_change=0)


def test_should_run_fg_shift():
    engine = ScenarioEngine()
    assert engine.should_run(fear_greed=50, fg_24h_change=-15)
    assert engine.should_run(fear_greed=50, fg_24h_change=12)


def test_should_not_run_normal():
    engine = ScenarioEngine()
    assert not engine.should_run(fear_greed=50, fg_24h_change=3, hour_utc=14)


def test_should_run_daily_at_8utc():
    engine = ScenarioEngine()
    assert engine.should_run(fear_greed=50, fg_24h_change=0, hour_utc=8)


def test_cache_hit():
    engine = ScenarioEngine()
    pred = ScenarioPrediction(
        direction="BULLISH", confidence=70, risk_factors="none",
        timeframe="24h", scenario="test", raw_response="test",
        timestamp=time.time(),
    )
    engine._cache["test_key"] = pred
    assert engine.get_scenario("test_key") is pred


def test_cache_expired():
    engine = ScenarioEngine()
    pred = ScenarioPrediction(
        direction="BULLISH", confidence=70, risk_factors="none",
        timeframe="24h", scenario="test", raw_response="test",
        timestamp=time.time() - 2000,  # expired
    )
    engine._cache["test_key"] = pred
    assert engine.get_scenario("test_key") is None


def test_extract_direction_bullish():
    assert ScenarioEngine._extract_direction("Market likely to rally higher") == "BULLISH"
    assert ScenarioEngine._extract_direction("Recovery and upward momentum expected") == "BULLISH"


def test_extract_direction_bearish():
    assert ScenarioEngine._extract_direction("Correction likely, market will drop") == "BEARISH"
    assert ScenarioEngine._extract_direction("Bearish outlook with downward pressure") == "BEARISH"


def test_extract_direction_neutral():
    assert ScenarioEngine._extract_direction("Sideways movement expected") == "NEUTRAL"


def test_parse_json_response():
    engine = ScenarioEngine()
    raw = json.dumps({
        "prediction": "BULLISH",
        "confidence": 75,
        "risk_factors": "Fed rate uncertainty",
        "timeframe": "48h",
    })
    pred = engine._parse_response(raw, "test scenario")
    assert pred.direction == "BULLISH"
    assert pred.confidence == 75
    assert pred.timeframe == "48h"


def test_parse_text_response():
    engine = ScenarioEngine()
    raw = "The market will likely rally upward with 65% probability over 24-48 hours. Risk: CPI data release."
    pred = engine._parse_response(raw, "test scenario")
    assert pred.direction == "BULLISH"
    assert pred.confidence == 65


def test_neutral_fallback():
    pred = ScenarioEngine._neutral_fallback("test", "connection refused")
    assert pred.direction == "NEUTRAL"
    assert pred.confidence == 0


def test_compact_summary_empty():
    engine = ScenarioEngine()
    assert engine.get_compact_summary() == "none"


def test_compact_summary_with_cache():
    engine = ScenarioEngine()
    engine._cache["k1"] = ScenarioPrediction(
        direction="BULLISH", confidence=70, risk_factors="",
        timeframe="24h", scenario="", raw_response="",
        timestamp=time.time(),
    )
    engine._cache["k2"] = ScenarioPrediction(
        direction="BEARISH", confidence=60, risk_factors="",
        timeframe="48h", scenario="", raw_response="",
        timestamp=time.time(),
    )
    summary = engine.get_compact_summary()
    assert "BULLISH(70%,24h)" in summary
    assert "BEARISH(60%,48h)" in summary


def test_simulate_uses_cache():
    engine = ScenarioEngine()
    pred = ScenarioPrediction(
        direction="BULLISH", confidence=80, risk_factors="none",
        timeframe="24h", scenario="cached", raw_response="cached",
        timestamp=time.time(),
    )
    engine._cache["my_key"] = pred
    result = engine.simulate("any scenario", "my_key")
    assert result.direction == "BULLISH"
    assert result.confidence == 80


@patch("analysis.scenario_engine.urlopen")
def test_simulate_api_error_returns_neutral(mock_urlopen):
    mock_urlopen.side_effect = Exception("connection refused")
    engine = ScenarioEngine()
    result = engine.simulate("test scenario", "new_key")
    assert result.direction == "NEUTRAL"
    assert result.confidence == 0


@patch("analysis.scenario_engine.urlopen")
def test_simulate_api_success(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({
        "prediction": "BEARISH",
        "confidence": 65,
        "risk_factors": "Fed hawkish",
        "timeframe": "24h",
    }).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_resp

    engine = ScenarioEngine()
    result = engine.simulate("test scenario", "api_key")
    assert result.direction == "BEARISH"
    assert result.confidence == 65
    assert "api_key" in engine._cache


def test_run_macro_scenarios_extreme_fear():
    engine = ScenarioEngine()
    with patch.object(engine, "simulate") as mock_sim:
        mock_sim.return_value = ScenarioPrediction(
            direction="BULLISH", confidence=70, risk_factors="",
            timeframe="24h", scenario="", raw_response="",
            timestamp=time.time(),
        )
        results = engine.run_macro_scenarios(fear_greed=10, btc_price=70000)
        # Should have at least daily + extreme fear
        assert len(results) >= 2
        assert mock_sim.call_count >= 2
