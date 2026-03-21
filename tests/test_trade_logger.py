import json
import pytest
from utils.trade_logger import TradeLogger
from analysis.claude_brain import TradeSignal


def make_signal():
    return TradeSignal(
        pair="BTC/USDT", decision="BUY", confidence=85,
        entry=84000.0, stop_loss=82000.0, take_profit=87000.0,
        risk_reward="2:1", bias="Bullish", retail_trap=False,
        retail_trap_explanation="", whale_activity="Accumulating",
        psychology="Mild greed", reasoning="BOS confirmed.", raw_response=""
    )


def test_log_entry_writes_jsonl(tmp_path):
    logger = TradeLogger(log_dir=str(tmp_path))
    logger.log_entry(signal=make_signal(), size_usdt=500.0)
    log_file = tmp_path / "trades.jsonl"
    assert log_file.exists()
    with open(log_file) as f:
        record = json.loads(f.readline())
    assert record["action"] == "ENTRY"
    assert record["pair"] == "BTC/USDT"
    assert record["confidence"] == 85


def test_log_close_appends(tmp_path):
    logger = TradeLogger(log_dir=str(tmp_path))
    logger.log_entry(signal=make_signal(), size_usdt=500.0)
    logger.log_close(pair="BTC/USDT", exit_price=87000.0,
                     pnl_usdt=178.0, pnl_pct=0.35, result="WIN")
    log_file = tmp_path / "trades.jsonl"
    with open(log_file) as f:
        lines = f.readlines()
    assert len(lines) == 2
    close_record = json.loads(lines[1])
    assert close_record["action"] == "CLOSE"
    assert close_record["result"] == "WIN"
