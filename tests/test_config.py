import pytest
import os

def test_config_loads_required_vars(monkeypatch):
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("BYBIT_API_KEY", "bk")
    monkeypatch.setenv("BYBIT_SECRET", "bs")
    monkeypatch.setenv("CRYPTOPANIC_API_KEY", "cp")

    from config import Config
    cfg = Config()
    assert cfg.paper_trading is True
    assert cfg.anthropic_api_key == "test-key"
    assert cfg.trading_pairs == [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
        "ADA/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT", "MATIC/USDT",
        "UNI/USDT", "ATOM/USDT", "LTC/USDT", "BCH/USDT", "NEAR/USDT",
        "APT/USDT", "ARB/USDT", "OP/USDT", "FIL/USDT", "DOGE/USDT",
    ]
    assert cfg.min_confidence == 72
    assert cfg.size_tier_low_pct == 5

def test_config_raises_on_missing_api_key(monkeypatch):
    monkeypatch.setenv("BYBIT_API_KEY", "bk")
    monkeypatch.setenv("BYBIT_SECRET", "bs")
    monkeypatch.setenv("CRYPTOPANIC_API_KEY", "cp")
    import importlib
    import config
    # Reload first so load_dotenv() runs, then delete — Config.__init__ doesn't call load_dotenv again
    importlib.reload(config)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
        config.Config()
