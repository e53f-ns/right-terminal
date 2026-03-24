import pytest
from trading.paper_engine import PaperEngine, compute_leverage


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


def test_leverage_opens_with_notional():
    engine = PaperEngine(initial_balance=10000.0)
    result = engine.open_position(
        pair="BTC/USDT", side="buy", size_usdt=500.0,
        entry=84000.0, stop_loss=83200.0, take_profit=87000.0,
        confidence=85, leverage=5
    )
    assert result["status"] == "filled"
    assert result["leverage"] == 5
    assert result["size_usdt"] == 2500.0  # 500 * 5x
    assert result["margin"] == 500.0
    assert engine.balance == 9500.0  # only margin deducted
    pos = engine.positions["BTC/USDT"]
    assert pos["size_usdt"] == 2500.0
    assert pos["margin"] == 500.0
    assert pos["liquidation_price"] is not None


def test_leverage_pnl_amplified():
    engine = PaperEngine(initial_balance=10000.0)
    engine.open_position(
        "BTC/USDT", "buy", 500.0, 84000.0, 83200.0, 87000.0,
        confidence=85, leverage=5
    )
    # 1% price rise on 5x = 5% on margin
    result = engine.close_position("BTC/USDT", exit_price=84840.0)
    assert result["pnl_usdt"] == 25.0  # 2500 * 1% = $25
    assert result["leverage"] == 5


def test_leverage_loss_capped_at_margin():
    engine = PaperEngine(initial_balance=10000.0)
    engine.open_position(
        "BTC/USDT", "buy", 500.0, 84000.0, 83200.0, 87000.0,
        confidence=85, leverage=10
    )
    # Price drops 15% — loss would be 150% of margin, capped at margin
    result = engine.close_position("BTC/USDT", exit_price=71400.0)
    assert result["pnl_usdt"] == -500.0  # capped at margin


def test_margin_buffer_rejects_trade():
    engine = PaperEngine(initial_balance=1000.0, free_margin_buffer_pct=30,
                         max_margin_per_trade_pct=90)
    # Use 750 margin — leaves 250 free (< 30% of 1000 = 300)
    result = engine.open_position(
        "BTC/USDT", "buy", 750.0, 84000.0, 83000.0, 87000.0,
        confidence=85, leverage=1
    )
    assert result["status"] == "rejected"
    assert "buffer" in result["reason"]


def test_max_margin_per_trade_rejects():
    engine = PaperEngine(initial_balance=1000.0, max_margin_per_trade_pct=50)
    result = engine.open_position(
        "BTC/USDT", "buy", 600.0, 84000.0, 83000.0, 87000.0,
        confidence=85, leverage=1
    )
    assert result["status"] == "rejected"
    assert "limit" in result["reason"]


def test_compute_leverage_confidence_tiers():
    assert compute_leverage(75, "SCALP") == 2
    assert compute_leverage(82, "SCALP") == 5
    assert compute_leverage(90, "SCALP") == 10  # cap for SCALP
    assert compute_leverage(95, "SCALP") == 10  # capped at SCALP max
    assert compute_leverage(95, "SWING") == 5   # capped at SWING max
    assert compute_leverage(95, "POSITION") == 3  # capped at POSITION max


def test_emergency_close():
    engine = PaperEngine(initial_balance=10000.0, emergency_close_loss_pct=3)
    engine.open_position(
        "BTC/USDT", "buy", 1000.0, 84000.0, 80000.0, 90000.0,
        confidence=85, leverage=10
    )
    # Notional = 10000. 4% drop = $400 loss > 3% of equity (~$300)
    result = engine.check_stop_take("BTC/USDT", 80640.0)
    assert result is not None
    assert result["status"] == "closed"
