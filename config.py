import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    def __init__(self):
        self.anthropic_api_key = self._get("ANTHROPIC_API_KEY")
        self.bybit_api_key = self._get("BYBIT_API_KEY")
        self.bybit_secret = self._get("BYBIT_SECRET")
        self.cryptopanic_api_key = self._get("CRYPTOPANIC_API_KEY")
        self.okx_api_key = os.getenv("OKX_API_KEY", "")
        self.okx_secret = os.getenv("OKX_SECRET", "")
        self.okx_passphrase = os.getenv("OKX_PASSPHRASE", "")

        self.paper_trading = os.getenv("PAPER_TRADING", "true").lower() == "true"
        self.bybit_testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
        self.claude_model = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")
        self.trading_pairs = [p.strip() for p in os.getenv(
            "TRADING_PAIRS",
            "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,"
            "ADA/USDT,AVAX/USDT,DOT/USDT,LINK/USDT,MATIC/USDT,"
            "UNI/USDT,ATOM/USDT,LTC/USDT,BCH/USDT,NEAR/USDT,"
            "APT/USDT,ARB/USDT,OP/USDT,FIL/USDT,DOGE/USDT"
        ).split(",")]
        self.scan_interval_minutes = int(os.getenv("SCAN_INTERVAL_MINUTES", "15"))
        self.min_confidence = int(os.getenv("MIN_CONFIDENCE", "60"))
        self.max_simultaneous_positions = int(os.getenv("MAX_SIMULTANEOUS_POSITIONS", "8"))
        self.max_total_exposure_pct = float(os.getenv("MAX_TOTAL_EXPOSURE_PCT", "60"))
        self.daily_loss_limit_pct = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "12"))
        self.loss_streak_pause_trades = int(os.getenv("LOSS_STREAK_PAUSE_TRADES", "3"))
        self.loss_streak_pause_minutes = int(os.getenv("LOSS_STREAK_PAUSE_MINUTES", "60"))
        self.size_tier_low_pct = float(os.getenv("SIZE_TIER_LOW_PCT", "5"))
        self.size_tier_mid_pct = float(os.getenv("SIZE_TIER_MID_PCT", "10"))
        self.size_tier_high_pct = float(os.getenv("SIZE_TIER_HIGH_PCT", "15"))
        # Futures / leverage
        self.futures_enabled = os.getenv("FUTURES_ENABLED", "true").lower() == "true"
        self.max_leverage = int(os.getenv("MAX_LEVERAGE", "20"))
        self.default_leverage = int(os.getenv("DEFAULT_LEVERAGE", "5"))
        self.max_margin_per_trade_pct = float(os.getenv("MAX_MARGIN_PER_TRADE_PCT", "50"))
        self.free_margin_buffer_pct = float(os.getenv("FREE_MARGIN_BUFFER_PCT", "30"))
        self.emergency_close_loss_pct = float(os.getenv("EMERGENCY_CLOSE_LOSS_PCT", "3"))

        self.paper_initial_balance = float(os.getenv("PAPER_INITIAL_BALANCE", "10000"))
        # Stock pairs
        self.stock_symbols = [s.strip() for s in os.getenv(
            "STOCK_SYMBOLS",
            "AAPL,TSLA,NVDA,MSFT,GOOGL,AMZN,META,SPY,QQQ"
        ).split(",")]
        self.stock_scan_interval_minutes = int(os.getenv("STOCK_SCAN_INTERVAL_MINUTES", "60"))

        # Forex pairs
        self.forex_pairs = [p.strip() for p in os.getenv(
            "FOREX_PAIRS",
            "EURUSD=X,GBPUSD=X,USDJPY=X,AUDUSD=X,USDCHF=X"
        ).split(",")]
        self.forex_scan_interval_minutes = int(os.getenv("FOREX_SCAN_INTERVAL_MINUTES", "30"))

        # Performance targets
        self.daily_trade_target = int(os.getenv("DAILY_TRADE_TARGET", "5"))
        self.daily_profit_target_pct = float(os.getenv("DAILY_PROFIT_TARGET_PCT", "2"))

        # Polymarket BTC 5-min bot
        self.polymarket_paper = os.getenv("POLYMARKET_PAPER", "true").lower() == "true"
        self.polymarket_initial_balance = float(os.getenv("POLYMARKET_INITIAL_BALANCE", "100"))
        self.polymarket_scan_interval = int(os.getenv("POLYMARKET_SCAN_INTERVAL", "5"))
        self.polymarket_entry_threshold = float(os.getenv("POLYMARKET_ENTRY_THRESHOLD", "0.92"))
        self.polymarket_exit_threshold = float(os.getenv("POLYMARKET_EXIT_THRESHOLD", "0.97"))
        self.polymarket_stop_loss = float(os.getenv("POLYMARKET_STOP_LOSS", "0.88"))
        self.polymarket_max_stake = float(os.getenv("POLYMARKET_MAX_STAKE", "10"))
        self.polymarket_max_positions = int(os.getenv("POLYMARKET_MAX_POSITIONS", "3"))
        self.polymarket_private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "")

        # Telegram alerts (optional — disabled if not set)
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

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
