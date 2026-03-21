import pytest
from risk.manager import RiskManager
from analysis.claude_brain import TradeSignal


def make_signal(decision="BUY", confidence=85, stop_loss=82000, entry=84000, take_profit=87000):
    return TradeSignal(
        pair="BTC/USDT", decision=decision, confidence=confidence,
        entry=entry, stop_loss=stop_loss, take_profit=take_profit,
        risk_reward="2:1", bias="Bullish", retail_trap=False,
        retail_trap_explanation="", whale_activity="", psychology="",
        reasoning="", raw_response=""
    )


def make_cfg():
    class Cfg:
        min_confidence = 75
        max_total_exposure_pct = 15.0
        daily_loss_limit_pct = 8.0
        loss_streak_pause_trades = 3
        loss_streak_pause_minutes = 60
        size_tier_low_pct = 3.0
        size_tier_mid_pct = 5.0
        size_tier_high_pct = 7.0
    return Cfg()


def test_position_size_by_confidence_tier():
    rm = RiskManager(make_cfg())
    assert rm.calculate_position_size(balance=10000, confidence=76) == 300.0   # 3%
    assert rm.calculate_position_size(balance=10000, confidence=85) == 500.0   # 5%
    assert rm.calculate_position_size(balance=10000, confidence=92) == 700.0   # 7%


def test_blocks_trade_below_min_confidence():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(signal=make_signal(confidence=70),
                                   daily_pnl_pct=-1.0, consecutive_losses=0,
                                   paused_until=None, current_exposure_pct=0.0)
    assert not allowed
    assert "confidence" in reason.lower()


def test_blocks_trade_at_daily_loss_limit():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(signal=make_signal(confidence=85),
                                   daily_pnl_pct=-8.5, consecutive_losses=0,
                                   paused_until=None, current_exposure_pct=0.0)
    assert not allowed
    assert "daily" in reason.lower()


def test_blocks_trade_when_exposure_exceeded():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(signal=make_signal(confidence=85),
                                   daily_pnl_pct=-1.0, consecutive_losses=0,
                                   paused_until=None, current_exposure_pct=14.0)
    assert not allowed
    assert "exposure" in reason.lower()


def test_allows_valid_trade():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(signal=make_signal(confidence=85),
                                   daily_pnl_pct=-1.0, consecutive_losses=0,
                                   paused_until=None, current_exposure_pct=5.0)
    assert allowed
