import pytest
from risk.manager import RiskManager
from analysis.claude_brain import TradeSignal


def make_signal(decision="BUY", confidence=85, stop_loss=82000,
                entry=84000, take_profit=87000, strategy="SCALP"):
    return TradeSignal(
        pair="BTC/USDT", decision=decision, confidence=confidence,
        entry=entry, stop_loss=stop_loss, take_profit=take_profit,
        risk_reward="2:1", bias="Bullish", retail_trap=False,
        retail_trap_explanation="", whale_activity="", psychology="",
        reasoning="", raw_response="", strategy=strategy,
    )


def make_cfg():
    class Cfg:
        min_confidence = 60
        max_total_exposure_pct = 60.0
        daily_loss_limit_pct = 12.0
        loss_streak_pause_trades = 3
        loss_streak_pause_minutes = 60
        size_tier_low_pct = 3.0
        size_tier_mid_pct = 5.0
        size_tier_high_pct = 10.0
        daily_trade_target = 5
        max_simultaneous_positions = 8
    return Cfg()


def test_scalp_position_sizing_capped_at_3pct():
    rm = RiskManager(make_cfg())
    # SCALP gets 1.2x bonus: 3% * 1.2 = 3.6%
    assert rm.calculate_position_size(10000, 62, "SCALP") == 360.0
    assert rm.calculate_position_size(10000, 72, "SCALP") == 360.0
    assert rm.calculate_position_size(10000, 82, "SCALP") == 360.0
    assert rm.calculate_position_size(10000, 92, "SCALP") == 360.0


def test_partial_position_sizing():
    rm = RiskManager(make_cfg())
    full = rm.calculate_position_size(10000, 62, "SCALP", partial=False)
    half = rm.calculate_position_size(10000, 62, "SCALP", partial=True)
    assert half == full / 2


def test_blocks_trade_below_strategy_min():
    rm = RiskManager(make_cfg())
    # SL within 1% so it passes SL check, but confidence too low
    allowed, reason = rm.can_trade(
        signal=make_signal(confidence=40, strategy="SCALP", entry=84000, stop_loss=83500),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=0.0,
        btc_trend="BULL",
    )
    assert not allowed
    assert "score" in reason.lower()


def test_trend_filter_blocks_long_in_bear():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(
        signal=make_signal(decision="BUY", confidence=80),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=0.0,
        btc_trend="BEAR",
    )
    assert not allowed
    assert "trend" in reason.lower()


def test_trend_filter_blocks_short_in_bull():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(
        signal=make_signal(decision="SELL", confidence=80),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=0.0,
        btc_trend="BULL",
    )
    assert not allowed
    assert "trend" in reason.lower()


def test_trend_filter_allows_long_in_bull():
    rm = RiskManager(make_cfg())
    # SL within 1%: entry=84000, sl=83200 → 0.95%
    allowed, reason = rm.can_trade(
        signal=make_signal(decision="BUY", confidence=80, entry=84000, stop_loss=83200),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=0.0,
        btc_trend="BULL",
    )
    assert allowed


def test_trend_filter_allows_short_in_bear():
    rm = RiskManager(make_cfg())
    # SL within 1%: entry=84000, sl=84800 → 0.95%
    allowed, reason = rm.can_trade(
        signal=make_signal(decision="SELL", confidence=80, entry=84000, stop_loss=84800),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=0.0,
        btc_trend="BEAR",
    )
    assert allowed


def test_sl_too_wide_blocks_trade():
    rm = RiskManager(make_cfg())
    # SL 2.4% from entry → blocked
    allowed, reason = rm.can_trade(
        signal=make_signal(decision="BUY", confidence=80, entry=84000, stop_loss=82000),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=0.0,
        btc_trend="BULL",
    )
    assert not allowed
    assert "sl too wide" in reason.lower()


def test_sl_within_1pct_allowed():
    rm = RiskManager(make_cfg())
    # SL 0.59% from entry → allowed
    allowed, reason = rm.can_trade(
        signal=make_signal(decision="BUY", confidence=80, entry=84000, stop_loss=83500),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=0.0,
        btc_trend="BULL",
    )
    assert allowed


def test_confidence_adjustment_lowers_threshold():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(
        signal=make_signal(confidence=57, strategy="SCALP", entry=84000, stop_loss=83500),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=0.0,
        confidence_adjustment=5,
        btc_trend="BULL",
    )
    assert allowed


def test_blocks_trade_at_daily_loss_limit():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(
        signal=make_signal(confidence=85, entry=84000, stop_loss=83500),
        daily_pnl_pct=-13.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=0.0,
        btc_trend="BULL",
    )
    assert not allowed
    assert "daily" in reason.lower()


def test_blocks_trade_when_exposure_exceeded():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(
        signal=make_signal(confidence=85, entry=84000, stop_loss=83500),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=58.0,
        btc_trend="BULL",
    )
    assert not allowed
    assert "exposure" in reason.lower()


def test_blocks_trade_when_strategy_slots_full():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(
        signal=make_signal(confidence=75, strategy="SCALP", entry=84000, stop_loss=83500),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=10.0,
        strategy_counts={"SCALP": 5, "SWING": 0, "POSITION": 0},
        btc_trend="BULL",
    )
    assert not allowed
    assert "slot" in reason.lower()


def test_blocks_trade_when_max_positions_reached():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(
        signal=make_signal(confidence=75, strategy="SCALP", entry=84000, stop_loss=83500),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=10.0,
        total_open_positions=8,
        btc_trend="BULL",
    )
    assert not allowed
    assert "max positions" in reason.lower()


def test_allows_valid_trade():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(
        signal=make_signal(confidence=75, strategy="SCALP", entry=84000, stop_loss=83500),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=10.0,
        strategy_counts={"SCALP": 1, "SWING": 0, "POSITION": 0},
        btc_trend="BULL",
    )
    assert allowed


def test_partial_candidate():
    rm = RiskManager(make_cfg())
    sig = make_signal(confidence=53, strategy="SCALP")
    assert rm.is_partial_candidate(sig)
    sig2 = make_signal(confidence=30, strategy="SCALP")
    assert not rm.is_partial_candidate(sig2)


def test_adaptive_confidence():
    assert RiskManager.compute_confidence_adjustment(0, 5, 10) == 0
    assert RiskManager.compute_confidence_adjustment(0, 5, 14) == 5
    assert RiskManager.compute_confidence_adjustment(2, 5, 19) == 8


def test_swing_disabled():
    rm = RiskManager(make_cfg())
    # SWING max_positions is 0
    allowed, reason = rm.can_trade(
        signal=make_signal(confidence=80, strategy="SWING", entry=84000, stop_loss=83500),
        daily_pnl_pct=-1.0, consecutive_losses=0,
        paused_until=None, current_exposure_pct=10.0,
        strategy_counts={"SCALP": 0, "SWING": 0, "POSITION": 0},
        btc_trend="BULL",
    )
    # SWING gets forced to SCALP in risk manager, so it should pass SCALP checks
    # The strategy override happens in can_trade
    assert allowed  # passes as SCALP
