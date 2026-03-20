import pytest
import os

def test_config_loads_required_vars(monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("BYBIT_API_KEY", "bk")
    monkeypatch.setenv("BYBIT_SECRET", "bs")
    monkeypatch.setenv("CRYPTOPANIC_API_KEY", "cp")
    monkeypatch.setenv("COINGLASS_API_KEY", "cg")

    from config import Config
    cfg = Config()
    assert cfg.paper_trading is True
    assert cfg.anthropic_api_key == "test-key"
    assert cfg.trading_pairs == ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
    assert cfg.min_confidence == 75
    assert cfg.size_tier_low_pct == 3

def test_config_raises_on_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("BYBIT_API_KEY", "bk")
    monkeypatch.setenv("BYBIT_SECRET", "bs")
    monkeypatch.setenv("CRYPTOPANIC_API_KEY", "cp")
    monkeypatch.setenv("COINGLASS_API_KEY", "cg")
    import importlib
    import config
    importlib.reload(config)
    with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
        config.Config()
