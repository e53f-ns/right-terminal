import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    def __init__(self):
        self._require("ANTHROPIC_API_KEY")
        self._require("BYBIT_API_KEY")
        self._require("BYBIT_SECRET")
        self._require("CRYPTOPANIC_API_KEY")
        self._require("COINGLASS_API_KEY")

        self.paper_trading = os.getenv("PAPER_TRADING", "true").lower() == "true"
        self.bybit_api_key = os.environ["BYBIT_API_KEY"]
        self.bybit_secret = os.environ["BYBIT_SECRET"]
        self.bybit_testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
        self.anthropic_api_key = os.environ["ANTHROPIC_API_KEY"]
        self.claude_model = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")
        self.cryptopanic_api_key = os.environ["CRYPTOPANIC_API_KEY"]
        self.coinglass_api_key = os.environ["COINGLASS_API_KEY"]
        self.trading_pairs = os.getenv(
            "TRADING_PAIRS", "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT"
        ).split(",")
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

    def _require(self, key: str):
        if not os.getenv(key):
            raise EnvironmentError(f"Required environment variable '{key}' is not set.")
