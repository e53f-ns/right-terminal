# tests/test_learning_log.py
import json
import os
import tempfile
import pytest
from utils.learning_log import LearningLog


@pytest.fixture
def log(tmp_path):
    return LearningLog(log_dir=str(tmp_path))


def test_log_trade_creates_file(log):
    log.record_trade(
        pair="BTC/USDT", strategy="SCALP", side="BUY",
        entry_reason="RSI oversold + volume spike",
        result="LOSS", pnl_pct=-0.8,
        entry_price=84000, exit_price=83300,
        confidence=72, opened_at="2026-03-25T10:00:00+00:00",
        closed_at="2026-03-25T10:30:00+00:00",
        btc_trend="BEAR", htf_bias="MIXED",
    )
    entries = log.read_recent(20)
    assert len(entries) == 1
    assert entries[0]["pair"] == "BTC/USDT"
    assert entries[0]["result"] == "LOSS"
    assert "mistakes" in entries[0]


def test_detect_counter_trend(log):
    log.record_trade(
        pair="BTC/USDT", strategy="SCALP", side="BUY",
        entry_reason="RSI bounce", result="LOSS", pnl_pct=-0.5,
        entry_price=84000, exit_price=83500,
        confidence=65, opened_at="2026-03-25T10:00:00+00:00",
        closed_at="2026-03-25T10:30:00+00:00",
        btc_trend="BEAR", htf_bias="BEAR",
    )
    entries = log.read_recent(20)
    assert "counter_trend" in entries[0]["mistakes"]


def test_detect_low_confidence_loss(log):
    log.record_trade(
        pair="ETH/USDT", strategy="SCALP", side="SELL",
        entry_reason="breakdown", result="LOSS", pnl_pct=-0.3,
        entry_price=3200, exit_price=3210,
        confidence=62, opened_at="2026-03-25T10:00:00+00:00",
        closed_at="2026-03-25T10:30:00+00:00",
        btc_trend="BEAR", htf_bias="BEAR",
    )
    entries = log.read_recent(20)
    assert "low_confidence" in entries[0]["mistakes"]


def test_detect_wrong_session(log):
    # Asia low-volume hours: 0-7 UTC
    log.record_trade(
        pair="SOL/USDT", strategy="SCALP", side="BUY",
        entry_reason="bounce", result="LOSS", pnl_pct=-0.4,
        entry_price=150, exit_price=149.4,
        confidence=70, opened_at="2026-03-25T03:00:00+00:00",
        closed_at="2026-03-25T03:30:00+00:00",
        btc_trend="BULL", htf_bias="BULL",
    )
    entries = log.read_recent(20)
    assert "wrong_session" in entries[0]["mistakes"]


def test_detect_overtrading(log):
    # 4 trades within 1 hour
    base = "2026-03-25T14:"
    for i, pair in enumerate(["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]):
        log.record_trade(
            pair=pair, strategy="SCALP", side="BUY",
            entry_reason="signal", result="LOSS", pnl_pct=-0.2,
            entry_price=100, exit_price=99.8,
            confidence=70, opened_at=f"{base}{i*10:02d}:00+00:00",
            closed_at=f"{base}{i*10+5:02d}:00+00:00",
            btc_trend="BULL", htf_bias="BULL",
        )
    entries = log.read_recent(20)
    # Last entry should detect overtrading
    assert "overtrading" in entries[-1]["mistakes"]


def test_win_has_no_mistakes(log):
    log.record_trade(
        pair="BTC/USDT", strategy="SCALP", side="BUY",
        entry_reason="RSI + volume", result="WIN", pnl_pct=1.2,
        entry_price=84000, exit_price=85000,
        confidence=80, opened_at="2026-03-25T14:00:00+00:00",
        closed_at="2026-03-25T14:30:00+00:00",
        btc_trend="BULL", htf_bias="BULL",
    )
    entries = log.read_recent(20)
    assert entries[0]["mistakes"] == []


def test_get_pattern_summary(log):
    # Create 3 counter-trend losses
    for i in range(3):
        log.record_trade(
            pair=f"PAIR{i}/USDT", strategy="SCALP", side="BUY",
            entry_reason="bounce", result="LOSS", pnl_pct=-0.5,
            entry_price=100, exit_price=99.5,
            confidence=70, opened_at=f"2026-03-25T{10+i}:00:00+00:00",
            closed_at=f"2026-03-25T{10+i}:30:00+00:00",
            btc_trend="BEAR", htf_bias="BEAR",
        )
    summary = log.get_pattern_summary(20)
    assert "counter_trend" in summary
    assert "3" in summary  # count should appear


def test_read_recent_limits(log):
    for i in range(30):
        log.record_trade(
            pair=f"P{i}/USDT", strategy="SCALP", side="BUY",
            entry_reason="test", result="WIN", pnl_pct=0.1,
            entry_price=100, exit_price=100.1,
            confidence=75, opened_at=f"2026-03-25T{i%24:02d}:00:00+00:00",
            closed_at=f"2026-03-25T{i%24:02d}:30:00+00:00",
            btc_trend="BULL", htf_bias="BULL",
        )
    entries = log.read_recent(20)
    assert len(entries) == 20


def test_get_top_mistakes(log):
    """get_top_mistakes returns ranked mistake list with counts and examples."""
    for i in range(3):
        log.record_trade(
            pair=f"PAIR{i}/USDT", strategy="SCALP", side="BUY",
            entry_reason="bounce", result="LOSS", pnl_pct=-0.5,
            entry_price=100, exit_price=99.5,
            confidence=60, opened_at="2026-03-25T03:00:00+00:00",
            closed_at="2026-03-25T03:30:00+00:00",
            btc_trend="BEAR", htf_bias="BEAR",
        )
    top = log.get_top_mistakes(3)
    assert len(top) > 0
    assert top[0]["count"] >= 3
    assert "mistake" in top[0]
    assert "examples" in top[0]
