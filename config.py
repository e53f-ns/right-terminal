import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    def __init__(self):
        self.anthropic_api_key = self._get("ANTHROPIC_API_KEY")
        self.bybit_api_key = self._get("BYBIT_API_KEY")
        self.bybit_secret = self._get("BYBIT_SECRET")
        self.cryptopanic_api_key = self._get("CRYPTOPANIC_API_KEY")

        self.paper_trading = os.getenv("PAPER_TRADING", "true").lower() == "true"
        self.bybit_testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
        self.claude_model = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")
        self.trading_pairs = [p.strip() for p in os.getenv(
            "TRADING_PAIRS", "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT"
        ).split(",")]
        self.scan_interval_minutes = int(os.getenv("SCAN_INTERVAL_MINUTES", "15"))
        self.min_confidence = int(os.getenv("MIN_CONFIDENCE", "75"))
        self.max_total_exposure_pct = float(os.getenv("MAX_TOTAL_EXPOSURE_PCT", "15"))
        self.daily_loss_limit_pct = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "8"))
        self.loss_streak_pause_trades = int(os.getenv("LOSS_STREAK_PAUSE_TRADES", "3"))
        self.loss_streak_pause_minutes = int(os.getenv("LOSS_STREAK_PAUSE_MINUTES", "60"))
        self.size_tier_low_pct = float(os.getenv("SIZE_TIER_LOW_PCT", "3"))
        self.size_tier_mid_pct = float(os.getenv("SIZE_TIER_MID_PCT", "5"))
        self.size_tier_high_pct = float(os.getenv("SIZE_TIER_HIGH_PCT", "7"))
        self.paper_initial_balance = float(os.getenv("PAPER_INITIAL_BALANCE", "10000"))
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.log_to_file = os.getenv("LOG_TO_FILE", "true").lower() == "true"
        self.log_dir = os.getenv("LOG_DIR", "logs")

    def _get(self, key: str, default=None):
        value = os.getenv(key, default)
        if value is None:
            raise EnvironmentError(f"Required environment variable '{key}' is not set.")
        return value

    def __repr__(self):
        return (f"Config(paper_trading={self.paper_trading}, "
                f"pairs={self.trading_pairs}, "
                f"model={self.claude_model}, "
                f"min_confidence={self.min_confidence})")
