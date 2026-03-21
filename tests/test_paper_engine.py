import pytest
from trading.paper_engine import PaperEngine


def test_paper_buy_opens_position():
    engine = PaperEngine(initial_balance=10000.0)
    result = engine.open_position(
        pair="BTC/USDT", side="buy", size_usdt=500.0,
        entry=84000.0, stop_loss=82000.0, take_profit=87000.0, confidence=85
    )
    assert result["status"] == "filled"
    assert "BTC/USDT" in engine.positions
    assert engine.balance == 9500.0


def test_paper_close_win():
    engine = PaperEngine(initial_balance=10000.0)
    engine.open_position("BTC/USDT", "buy", 500.0, 84000.0, 82000.0, 87000.0, 85)
    result = engine.close_position("BTC/USDT", exit_price=87000.0)
    assert result["pnl_usdt"] > 0
    assert "BTC/USDT" not in engine.positions
    assert engine.balance > 10000.0


def test_paper_close_loss():
    engine = PaperEngine(initial_balance=10000.0)
    engine.open_position("BTC/USDT", "buy", 500.0, 84000.0, 82000.0, 87000.0, 85)
    result = engine.close_position("BTC/USDT", exit_price=82000.0)
    assert result["pnl_usdt"] < 0
    assert engine.balance < 10000.0


def test_cannot_open_duplicate_position():
    engine = PaperEngine(initial_balance=10000.0)
    engine.open_position("BTC/USDT", "buy", 500.0, 84000.0, 82000.0, 87000.0, 85)
    with pytest.raises(ValueError, match="already open"):
        engine.open_position("BTC/USDT", "buy", 500.0, 84000.0, 82000.0, 87000.0, 85)
