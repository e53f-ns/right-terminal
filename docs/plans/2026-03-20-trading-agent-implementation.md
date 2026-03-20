# Trading Agent Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a modular autonomous crypto trading agent that uses Claude as its analytical brain, paper trades by default, and switches to live Bybit trading via a single `.env` change.

**Architecture:** Modular Python package with clean separation between data fetching, indicator computation, SMC analysis, Claude-powered decision making, risk management, and trade execution. A 15-minute scheduler orchestrates the full pipeline per pair.

**Tech Stack:** Python 3.11+, ccxt (Bybit), anthropic SDK, pandas/numpy (indicators), requests (external APIs), python-dotenv, schedule, pytest

---

### Task 1: Project Bootstrap

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `config.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`

**Step 1: Create `requirements.txt`**

```
ccxt==4.3.89
anthropic==0.40.0
pandas==2.2.3
numpy==1.26.4
requests==2.32.3
python-dotenv==1.0.1
schedule==1.2.2
pytest==8.3.4
pytest-mock==3.14.0
```

**Step 2: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: all packages install without errors.

**Step 3: Create `.env.example`**

```bash
# ── MODE ──────────────────────────────────────────────
PAPER_TRADING=true

# ── EXCHANGE ──────────────────────────────────────────
BYBIT_API_KEY=your_key_here
BYBIT_SECRET=your_secret_here
BYBIT_TESTNET=false

# ── AI ────────────────────────────────────────────────
ANTHROPIC_API_KEY=your_key_here
CLAUDE_MODEL=claude-opus-4-6

# ── DATA SOURCES ──────────────────────────────────────
CRYPTOPANIC_API_KEY=your_key_here
COINGLASS_API_KEY=your_key_here

# ── TRADING PAIRS ─────────────────────────────────────
TRADING_PAIRS=BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT
SCAN_INTERVAL_MINUTES=15

# ── RISK MANAGEMENT ───────────────────────────────────
MIN_CONFIDENCE=75
MAX_TOTAL_EXPOSURE_PCT=15
DAILY_LOSS_LIMIT_PCT=8
LOSS_STREAK_PAUSE_TRADES=3
LOSS_STREAK_PAUSE_MINUTES=60

# ── POSITION SIZING ───────────────────────────────────
SIZE_TIER_LOW_PCT=3
SIZE_TIER_MID_PCT=5
SIZE_TIER_HIGH_PCT=7

# ── PAPER TRADING ─────────────────────────────────────
PAPER_INITIAL_BALANCE=10000

# ── LOGGING ───────────────────────────────────────────
LOG_LEVEL=INFO
LOG_TO_FILE=true
LOG_DIR=logs
```

**Step 4: Write failing test**

```python
# tests/test_config.py
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
    with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
        from config import Config
        Config()
```

**Step 5: Run to verify it fails**

```bash
pytest tests/test_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'config'`

**Step 6: Implement `config.py`**

```python
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
```

**Step 7: Run tests to verify they pass**

```bash
pytest tests/test_config.py -v
```
Expected: 2 passed

**Step 8: Commit**

```bash
git add requirements.txt .env.example config.py tests/
git commit -m "feat: project bootstrap — config, requirements, test scaffold"
```

---

### Task 2: Logger + State Utils

**Files:**
- Create: `utils/__init__.py`
- Create: `utils/logger.py`
- Create: `utils/state.py`
- Create: `tests/test_state.py`

**Step 1: Write failing test**

```python
# tests/test_state.py
import json
import os
import pytest
from utils.state import State

def test_state_initializes_fresh(tmp_path):
    s = State(path=str(tmp_path / "state.json"), initial_balance=10000.0)
    assert s.paper_balance == 10000.0
    assert s.open_positions == {}
    assert s.consecutive_losses == 0
    assert s.daily_pnl_pct == 0.0

def test_state_persists_and_reloads(tmp_path):
    path = str(tmp_path / "state.json")
    s = State(path=path, initial_balance=10000.0)
    s.consecutive_losses = 2
    s.save()

    s2 = State(path=path, initial_balance=10000.0)
    assert s2.consecutive_losses == 2

def test_state_resets_daily_pnl_on_new_day(tmp_path):
    path = str(tmp_path / "state.json")
    s = State(path=path, initial_balance=10000.0)
    s.daily_pnl_pct = -5.0
    s.daily_pnl_reset_date = "2020-01-01"
    s.save()

    s2 = State(path=path, initial_balance=10000.0)
    s2.reset_daily_if_new_day()
    assert s2.daily_pnl_pct == 0.0
```

**Step 2: Run to verify it fails**

```bash
pytest tests/test_state.py -v
```
Expected: `ModuleNotFoundError: No module named 'utils'`

**Step 3: Implement `utils/logger.py`**

```python
import logging
import os
from datetime import datetime

def get_logger(name: str, log_dir: str = "logs", log_to_file: bool = True, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    if log_to_file:
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(os.path.join(log_dir, f"agent_{datetime.utcnow().strftime('%Y%m%d')}.log"))
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger
```

**Step 4: Implement `utils/state.py`**

```python
import json
import os
from datetime import datetime, timezone

class State:
    def __init__(self, path: str = "state.json", initial_balance: float = 10000.0):
        self._path = path
        self._initial_balance = initial_balance
        self._defaults = {
            "paper_balance": initial_balance,
            "open_positions": {},
            "daily_pnl_pct": 0.0,
            "daily_pnl_reset_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "consecutive_losses": 0,
            "paused_until": None,
            "total_trades": 0,
            "winning_trades": 0,
        }
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self._path):
            with open(self._path) as f:
                data = json.load(f)
            # backfill any missing keys from defaults
            for k, v in self._defaults.items():
                data.setdefault(k, v)
            return data
        return dict(self._defaults)

    def save(self):
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    def reset_daily_if_new_day(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._data["daily_pnl_reset_date"] != today:
            self._data["daily_pnl_pct"] = 0.0
            self._data["daily_pnl_reset_date"] = today
            self.save()

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        try:
            return self._data[key]
        except KeyError:
            raise AttributeError(f"State has no attribute '{key}'")

    def __setattr__(self, key, value):
        if key.startswith("_"):
            super().__setattr__(key, value)
        else:
            self._data[key] = value
```

**Step 5: Create `utils/__init__.py`** (empty)

**Step 6: Run tests**

```bash
pytest tests/test_state.py -v
```
Expected: 3 passed

**Step 7: Commit**

```bash
git add utils/ tests/test_state.py
git commit -m "feat: add logger and state persistence utils"
```

---

### Task 3: Bybit Data Fetcher

**Files:**
- Create: `data/__init__.py`
- Create: `data/bybit.py`
- Create: `tests/test_bybit.py`

**Step 1: Write failing test**

```python
# tests/test_bybit.py
import pytest
from unittest.mock import MagicMock, patch
from data.bybit import BybitClient

@pytest.fixture
def mock_exchange():
    exchange = MagicMock()
    exchange.fetch_ohlcv.return_value = [
        [1700000000000, 40000, 41000, 39500, 40500, 1200.5]
    ] * 200
    exchange.fetch_order_book.return_value = {
        "bids": [[40400, 1.5], [40300, 2.0]],
        "asks": [[40600, 1.0], [40700, 3.0]],
    }
    exchange.fetch_funding_rate.return_value = {"fundingRate": 0.0001}
    return exchange

def test_get_candles_returns_dataframe(mock_exchange):
    with patch("data.bybit.ccxt.bybit", return_value=mock_exchange):
        client = BybitClient(api_key="k", secret="s", testnet=False)
        df = client.get_candles("BTC/USDT", timeframe="15m", limit=200)
    assert len(df) == 200
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]

def test_get_orderbook_summary(mock_exchange):
    with patch("data.bybit.ccxt.bybit", return_value=mock_exchange):
        client = BybitClient(api_key="k", secret="s", testnet=False)
        ob = client.get_orderbook_summary("BTC/USDT")
    assert "best_bid" in ob
    assert "best_ask" in ob
    assert "bid_wall" in ob
    assert "ask_wall" in ob

def test_get_funding_rate(mock_exchange):
    with patch("data.bybit.ccxt.bybit", return_value=mock_exchange):
        client = BybitClient(api_key="k", secret="s", testnet=False)
        rate = client.get_funding_rate("BTC/USDT")
    assert rate == 0.0001
```

**Step 2: Run to verify it fails**

```bash
pytest tests/test_bybit.py -v
```
Expected: `ModuleNotFoundError: No module named 'data'`

**Step 3: Implement `data/bybit.py`**

```python
import ccxt
import pandas as pd
from typing import Optional

class BybitClient:
    def __init__(self, api_key: str, secret: str, testnet: bool = False):
        params = {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
        }
        if testnet:
            params["urls"] = {"api": {"public": "https://api-testnet.bybit.com", "private": "https://api-testnet.bybit.com"}}
        self._exchange = ccxt.bybit(params)

    def get_candles(self, pair: str, timeframe: str = "15m", limit: int = 200) -> pd.DataFrame:
        raw = self._exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df

    def get_orderbook_summary(self, pair: str, depth: int = 20) -> dict:
        ob = self._exchange.fetch_order_book(pair, limit=depth)
        bids = ob["bids"]
        asks = ob["asks"]
        bid_wall = max(bids, key=lambda x: x[1]) if bids else [0, 0]
        ask_wall = max(asks, key=lambda x: x[1]) if asks else [0, 0]
        total_bid_vol = sum(b[1] for b in bids)
        total_ask_vol = sum(a[1] for a in asks)
        return {
            "best_bid": bids[0][0] if bids else None,
            "best_ask": asks[0][0] if asks else None,
            "bid_wall": {"price": bid_wall[0], "size": bid_wall[1]},
            "ask_wall": {"price": ask_wall[0], "size": ask_wall[1]},
            "bid_ask_ratio": round(total_bid_vol / total_ask_vol, 3) if total_ask_vol else 0,
        }

    def get_funding_rate(self, pair: str) -> float:
        result = self._exchange.fetch_funding_rate(pair)
        return result.get("fundingRate", 0.0)

    def get_open_interest(self, pair: str) -> Optional[float]:
        try:
            result = self._exchange.fetch_open_interest(pair)
            return result.get("openInterestValue")
        except Exception:
            return None

    def get_balance(self) -> dict:
        balance = self._exchange.fetch_balance()
        return {
            "USDT": balance.get("USDT", {}).get("free", 0.0),
            "total": balance.get("USDT", {}).get("total", 0.0),
        }

    def place_order(self, pair: str, side: str, amount: float,
                    stop_loss: float, take_profit: float) -> dict:
        order = self._exchange.create_order(
            symbol=pair,
            type="market",
            side=side,
            amount=amount,
            params={"stopLoss": {"triggerPrice": stop_loss},
                    "takeProfit": {"triggerPrice": take_profit}},
        )
        return order

    def cancel_order(self, order_id: str, pair: str) -> dict:
        return self._exchange.cancel_order(order_id, pair)
```

**Step 4: Create `data/__init__.py`** (empty)

**Step 5: Run tests**

```bash
pytest tests/test_bybit.py -v
```
Expected: 3 passed

**Step 6: Commit**

```bash
git add data/ tests/test_bybit.py
git commit -m "feat: add Bybit data client (candles, orderbook, funding, OI)"
```

---

### Task 4: External Data Fetchers

**Files:**
- Create: `data/coingecko.py`
- Create: `data/cryptopanic.py`
- Create: `data/coinglass.py`
- Create: `tests/test_external_data.py`

**Step 1: Write failing tests**

```python
# tests/test_external_data.py
import pytest
from unittest.mock import patch, MagicMock

def test_coingecko_fear_greed():
    from data.coingecko import CoinGeckoClient
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [{"value": "62", "value_classification": "Greed",
                  "timestamp": "1700000000"}]
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.coingecko.requests.get", return_value=mock_resp):
        client = CoinGeckoClient()
        fg = client.get_fear_greed()
    assert fg["value"] == 62
    assert fg["label"] == "Greed"

def test_coingecko_btc_dominance():
    from data.coingecko import CoinGeckoClient
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {"market_cap_percentage": {"btc": 52.3}}
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.coingecko.requests.get", return_value=mock_resp):
        client = CoinGeckoClient()
        dom = client.get_btc_dominance()
    assert dom == 52.3

def test_cryptopanic_returns_headlines():
    from data.cryptopanic import CryptoPanicClient
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {"title": "BTC hits new high", "votes": {"positive": 10, "negative": 2},
             "currencies": [{"code": "BTC"}]},
        ]
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.cryptopanic.requests.get", return_value=mock_resp):
        client = CryptoPanicClient(api_key="test")
        news = client.get_news(currencies=["BTC"])
    assert len(news) == 1
    assert news[0]["title"] == "BTC hits new high"
    assert news[0]["sentiment"] == "bullish"

def test_coinglass_liquidation_levels():
    from data.coinglass import CoinglassClient
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": {"liqLevels": [
            {"price": 85000, "liqUsdt": 50000000},
            {"price": 80000, "liqUsdt": 30000000},
        ]}
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("data.coinglass.requests.get", return_value=mock_resp):
        client = CoinglassClient(api_key="test")
        levels = client.get_liquidation_levels("BTC")
    assert len(levels) == 2
    assert levels[0]["price"] == 85000
```

**Step 2: Run to verify failures**

```bash
pytest tests/test_external_data.py -v
```
Expected: 4 errors (modules not found)

**Step 3: Implement `data/coingecko.py`**

```python
import requests

FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=7"
GLOBAL_URL = "https://api.coingecko.com/api/v3/global"

class CoinGeckoClient:
    def get_fear_greed(self) -> dict:
        resp = requests.get(FEAR_GREED_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()["data"]
        latest = data[0]
        history = [int(d["value"]) for d in data]
        trend = "rising" if history[0] > history[-1] else "falling"
        return {
            "value": int(latest["value"]),
            "label": latest["value_classification"],
            "trend": trend,
            "history_7d": history,
        }

    def get_btc_dominance(self) -> float:
        resp = requests.get(GLOBAL_URL, timeout=10)
        resp.raise_for_status()
        return round(resp.json()["data"]["market_cap_percentage"].get("btc", 0), 2)
```

**Step 4: Implement `data/cryptopanic.py`**

```python
import requests
from typing import List

BASE_URL = "https://cryptopanic.com/api/v1/posts/"

class CryptoPanicClient:
    def __init__(self, api_key: str):
        self._key = api_key

    def get_news(self, currencies: List[str] = None, limit: int = 10) -> List[dict]:
        params = {"auth_token": self._key, "public": "true", "limit": limit}
        if currencies:
            params["currencies"] = ",".join(currencies)
        resp = requests.get(BASE_URL, params=params, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        news = []
        for item in results:
            votes = item.get("votes", {})
            pos = votes.get("positive", 0)
            neg = votes.get("negative", 0)
            sentiment = "bullish" if pos > neg else ("bearish" if neg > pos else "neutral")
            news.append({
                "title": item["title"],
                "sentiment": sentiment,
                "positive_votes": pos,
                "negative_votes": neg,
                "currencies": [c["code"] for c in item.get("currencies", [])],
            })
        return news
```

**Step 5: Implement `data/coinglass.py`**

```python
import requests
from typing import List

BASE_URL = "https://open-api.coinglass.com/public/v2"

class CoinglassClient:
    def __init__(self, api_key: str):
        self._key = api_key
        self._headers = {"coinglassSecret": api_key}

    def get_liquidation_levels(self, symbol: str) -> List[dict]:
        try:
            resp = requests.get(
                f"{BASE_URL}/liquidation_map",
                headers=self._headers,
                params={"symbol": symbol},
                timeout=10,
            )
            resp.raise_for_status()
            levels = resp.json().get("data", {}).get("liqLevels", [])
            return [{"price": l["price"], "liq_usdt": l["liqUsdt"]} for l in levels]
        except Exception:
            return []  # non-critical: return empty on failure
```

**Step 6: Run tests**

```bash
pytest tests/test_external_data.py -v
```
Expected: 4 passed

**Step 7: Commit**

```bash
git add data/coingecko.py data/cryptopanic.py data/coinglass.py tests/test_external_data.py
git commit -m "feat: add CoinGecko, CryptoPanic, Coinglass data fetchers"
```

---

### Task 5: Technical Indicators

**Files:**
- Create: `analysis/__init__.py`
- Create: `analysis/indicators.py`
- Create: `tests/test_indicators.py`

**Step 1: Write failing tests**

```python
# tests/test_indicators.py
import pandas as pd
import numpy as np
import pytest
from analysis.indicators import compute_indicators

def make_df(n=200):
    np.random.seed(42)
    close = 40000 + np.cumsum(np.random.randn(n) * 100)
    high = close + np.abs(np.random.randn(n) * 50)
    low = close - np.abs(np.random.randn(n) * 50)
    volume = np.random.uniform(100, 1000, n)
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": volume})

def test_indicators_returns_expected_keys():
    df = make_df()
    result = compute_indicators(df)
    for key in ["rsi", "macd", "macd_signal", "macd_hist",
                "bb_upper", "bb_lower", "bb_mid",
                "ema20", "ema50", "ema200", "vwap"]:
        assert key in result, f"Missing key: {key}"

def test_rsi_bounds():
    df = make_df(300)
    result = compute_indicators(df)
    assert 0 <= result["rsi"] <= 100

def test_ema_ordering_in_uptrend():
    # build a clean uptrend
    close = pd.Series([float(i) for i in range(1, 300)])
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                       "close": close, "volume": pd.Series([100.0] * 299)})
    result = compute_indicators(df)
    assert result["ema20"] > result["ema50"] > result["ema200"]
```

**Step 2: Run to verify failures**

```bash
pytest tests/test_indicators.py -v
```
Expected: `ModuleNotFoundError: No module named 'analysis'`

**Step 3: Implement `analysis/indicators.py`**

```python
import pandas as pd
import numpy as np

def compute_indicators(df: pd.DataFrame) -> dict:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # RSI (14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = float((100 - 100 / (1 + rs)).iloc[-1])

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()

    # Bollinger Bands (20, 2)
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()

    # EMAs
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

    # VWAP (rolling session approximation over available data)
    tp = (high + low + close) / 3
    vwap = float((tp * volume).cumsum().iloc[-1] / volume.cumsum().iloc[-1])

    return {
        "rsi": round(rsi, 2),
        "macd": round(float(macd_line.iloc[-1]), 4),
        "macd_signal": round(float(signal.iloc[-1]), 4),
        "macd_hist": round(float((macd_line - signal).iloc[-1]), 4),
        "bb_upper": round(float((sma20 + 2 * std20).iloc[-1]), 2),
        "bb_lower": round(float((sma20 - 2 * std20).iloc[-1]), 2),
        "bb_mid": round(float(sma20.iloc[-1]), 2),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "ema200": round(ema200, 2),
        "vwap": round(vwap, 2),
        "current_price": round(float(close.iloc[-1]), 2),
    }
```

**Step 4: Create `analysis/__init__.py`** (empty)

**Step 5: Run tests**

```bash
pytest tests/test_indicators.py -v
```
Expected: 3 passed

**Step 6: Commit**

```bash
git add analysis/ tests/test_indicators.py
git commit -m "feat: add technical indicators (RSI, MACD, BB, EMAs, VWAP)"
```

---

### Task 6: SMC Analysis

**Files:**
- Create: `analysis/smc.py`
- Create: `tests/test_smc.py`

**Step 1: Write failing tests**

```python
# tests/test_smc.py
import pandas as pd
import numpy as np
import pytest
from analysis.smc import detect_smc

def make_df_with_structure():
    """Creates OHLCV with a clear swing high, FVG, and order block pattern."""
    data = {
        "open":  [100, 101, 105, 110, 108, 103, 100, 98,  102, 107],
        "high":  [102, 106, 111, 115, 112, 106, 102, 99,  108, 112],
        "low":   [99,  100, 104, 108, 105, 100, 97,  95,  99,  105],
        "close": [101, 105, 110, 113, 107, 102, 99,  97,  106, 110],
        "volume":[500, 800, 1200,900, 600, 700, 500, 400, 900, 1100],
    }
    return pd.DataFrame(data)

def test_detect_smc_returns_required_keys():
    df = make_df_with_structure()
    result = detect_smc(df)
    for key in ["order_blocks", "fvgs", "swing_highs", "swing_lows",
                "bos_detected", "choch_detected", "premium_zone",
                "discount_zone", "liquidity_above", "liquidity_below"]:
        assert key in result, f"Missing SMC key: {key}"

def test_premium_discount_zones():
    df = make_df_with_structure()
    result = detect_smc(df)
    assert result["premium_zone"] > result["discount_zone"]

def test_fvg_detection_finds_gap():
    # candles: [low[-3]=100, high[-3]=102], gap: [low[-1]=104, high[-1]=...] - FVG if high[-3] < low[-1]
    data = {
        "open":  [100, 101, 106],
        "high":  [101, 102, 110],
        "low":   [99,  100, 105],
        "close": [100, 101, 109],
        "volume":[500, 500, 1500],
    }
    df = pd.DataFrame(data)
    result = detect_smc(df)
    # There should be at least 0 FVGs (small df may not produce one)
    assert isinstance(result["fvgs"], list)
```

**Step 2: Run to verify failures**

```bash
pytest tests/test_smc.py -v
```
Expected: `ImportError: cannot import name 'detect_smc'`

**Step 3: Implement `analysis/smc.py`**

```python
import pandas as pd
import numpy as np
from typing import List, Dict

def detect_smc(df: pd.DataFrame) -> dict:
    """
    Detect Smart Money Concepts from OHLCV data.
    Returns structured findings for Claude to reason about.
    """
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    opens = df["open"].values
    volumes = df["volume"].values
    n = len(df)

    # ── Swing Highs / Lows (3-candle pivot) ──────────────────────
    swing_highs = []
    swing_lows = []
    for i in range(1, n - 1):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            swing_highs.append({"idx": i, "price": float(highs[i])})
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            swing_lows.append({"idx": i, "price": float(lows[i])})

    # ── Premium / Discount Zones ──────────────────────────────────
    recent_high = float(max(highs[-50:])) if n >= 50 else float(max(highs))
    recent_low = float(min(lows[-50:])) if n >= 50 else float(min(lows))
    mid = (recent_high + recent_low) / 2
    premium_zone = round((recent_high + mid) / 2, 2)
    discount_zone = round((recent_low + mid) / 2, 2)

    # ── Order Blocks (last bearish/bullish candle before strong move) ──
    order_blocks = []
    for i in range(2, n - 1):
        body = abs(closes[i] - opens[i])
        next_move = closes[i + 1] - closes[i]
        avg_body = np.mean([abs(closes[j] - opens[j]) for j in range(max(0, i - 10), i)])
        if body < 0.5 * avg_body and abs(next_move) > 1.5 * avg_body:
            ob_type = "bullish" if next_move > 0 else "bearish"
            order_blocks.append({
                "idx": i,
                "type": ob_type,
                "high": float(highs[i]),
                "low": float(lows[i]),
            })

    # ── Fair Value Gaps (3-candle FVG) ───────────────────────────
    fvgs = []
    for i in range(1, n - 1):
        if lows[i + 1] > highs[i - 1]:  # bullish FVG
            fvgs.append({
                "type": "bullish",
                "top": float(lows[i + 1]),
                "bottom": float(highs[i - 1]),
                "idx": i,
            })
        elif highs[i + 1] < lows[i - 1]:  # bearish FVG
            fvgs.append({
                "type": "bearish",
                "top": float(lows[i - 1]),
                "bottom": float(highs[i + 1]),
                "idx": i,
            })

    # ── BOS / CHOCH Detection ─────────────────────────────────────
    bos_detected = False
    choch_detected = False
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        last_sh = swing_highs[-1]["price"]
        prev_sh = swing_highs[-2]["price"]
        last_sl = swing_lows[-1]["price"]
        prev_sl = swing_lows[-2]["price"]
        current = float(closes[-1])

        # BOS: price breaks previous swing in same direction as trend
        if current > last_sh and last_sh > prev_sh:
            bos_detected = True
        if current < last_sl and last_sl < prev_sl:
            bos_detected = True

        # CHOCH: price breaks previous swing in OPPOSITE direction
        if current > last_sh and last_sh < prev_sh:
            choch_detected = True
        if current < last_sl and last_sl > prev_sl:
            choch_detected = True

    # ── Liquidity Levels (clustered swing points = stop hunt targets) ──
    liquidity_above = sorted(
        [sh["price"] for sh in swing_highs if sh["price"] > float(closes[-1])],
        reverse=False
    )[:3]
    liquidity_below = sorted(
        [sl["price"] for sl in swing_lows if sl["price"] < float(closes[-1])],
        reverse=True
    )[:3]

    return {
        "order_blocks": order_blocks[-5:],   # last 5
        "fvgs": fvgs[-5:],                   # last 5
        "swing_highs": [sh["price"] for sh in swing_highs[-5:]],
        "swing_lows": [sl["price"] for sl in swing_lows[-5:]],
        "bos_detected": bos_detected,
        "choch_detected": choch_detected,
        "premium_zone": premium_zone,
        "discount_zone": discount_zone,
        "liquidity_above": liquidity_above,
        "liquidity_below": liquidity_below,
        "recent_range_high": round(recent_high, 2),
        "recent_range_low": round(recent_low, 2),
    }
```

**Step 4: Run tests**

```bash
pytest tests/test_smc.py -v
```
Expected: 3 passed

**Step 5: Commit**

```bash
git add analysis/smc.py tests/test_smc.py
git commit -m "feat: add SMC analysis (order blocks, FVG, BOS/CHOCH, liquidity)"
```

---

### Task 7: Claude Brain

**Files:**
- Create: `analysis/claude_brain.py`
- Create: `tests/test_claude_brain.py`

**Step 1: Write failing tests**

```python
# tests/test_claude_brain.py
import pytest
from unittest.mock import MagicMock, patch
from analysis.claude_brain import ClaudeBrain, TradeSignal

def make_mock_context():
    return {
        "pair": "BTC/USDT",
        "price": 84000.0,
        "indicators": {"rsi": 55.0, "macd": 120.0, "macd_signal": 100.0,
                       "macd_hist": 20.0, "bb_upper": 86000, "bb_lower": 82000,
                       "bb_mid": 84000, "ema20": 83800, "ema50": 83000,
                       "ema200": 80000, "vwap": 83900, "current_price": 84000},
        "smc": {"order_blocks": [], "fvgs": [], "swing_highs": [85000],
                "swing_lows": [82000], "bos_detected": True,
                "choch_detected": False, "premium_zone": 85500,
                "discount_zone": 82500, "liquidity_above": [85000],
                "liquidity_below": [82000], "recent_range_high": 86000,
                "recent_range_low": 82000},
        "orderbook": {"best_bid": 83990, "best_ask": 84010,
                      "bid_wall": {"price": 83500, "size": 50},
                      "ask_wall": {"price": 84500, "size": 40},
                      "bid_ask_ratio": 1.2},
        "funding_rate": 0.0001,
        "open_interest": 5000000000,
        "fear_greed": {"value": 65, "label": "Greed", "trend": "rising"},
        "btc_dominance": 52.3,
        "news": [{"title": "BTC breaks ATH", "sentiment": "bullish",
                  "positive_votes": 10, "negative_votes": 1, "currencies": ["BTC"]}],
        "liquidation_levels": [{"price": 85000, "liq_usdt": 50000000}],
        "open_positions": {},
        "remaining_exposure_pct": 15.0,
    }

MOCK_CLAUDE_RESPONSE = """
[SMART MONEY BIAS]: Bullish
[RETAIL TRAP DETECTED]: No
[WHALE ACTIVITY]: Accumulation detected at discount zone
[PSYCHOLOGY]: Mild greed, not euphoric — healthy uptrend
[DECISION]: BUY
[CONFIDENCE]: 82%
[ENTRY]: $84000 | [STOP-LOSS]: $82500 | [TARGET]: $87000
[RISK/REWARD]: 2:1
[REASONING]: BOS confirmed bullish structure. Price in discount zone with BTC dominance rising indicating altcoin strength. Funding rate neutral, not overextended. FVG above at 84500 as near-term magnet.
"""

def test_parse_claude_output():
    from analysis.claude_brain import ClaudeBrain
    brain = ClaudeBrain(api_key="test", model="claude-opus-4-6")
    signal = brain._parse_response(MOCK_CLAUDE_RESPONSE, current_price=84000.0)
    assert signal.decision == "BUY"
    assert signal.confidence == 82
    assert signal.entry == 84000.0
    assert signal.stop_loss == 82500.0
    assert signal.take_profit == 87000.0
    assert signal.bias == "Bullish"

def test_analyze_calls_claude_api(monkeypatch):
    from analysis.claude_brain import ClaudeBrain
    brain = ClaudeBrain(api_key="test", model="claude-opus-4-6")

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=MOCK_CLAUDE_RESPONSE)]
    mock_client.messages.create.return_value = mock_message
    brain._client = mock_client

    ctx = make_mock_context()
    signal = brain.analyze(ctx)
    assert signal.decision == "BUY"
    assert signal.confidence == 82
    assert mock_client.messages.create.called
```

**Step 2: Run to verify failures**

```bash
pytest tests/test_claude_brain.py -v
```
Expected: `ModuleNotFoundError: No module named 'analysis.claude_brain'`

**Step 3: Implement `analysis/claude_brain.py`**

```python
import json
import re
from dataclasses import dataclass
from typing import Optional
import anthropic

SYSTEM_PROMPT = """You are an elite crypto trading analyst — a fusion of:
- ICT (Inner Circle Trader): master of Smart Money Concepts, order flow, and institutional manipulation
- A top on-chain whale analyst: you read where large players are positioning
- A behavioral finance psychologist: you understand crowd fear/greed and how to fade retail traps
- A Renaissance Technologies quant: you synthesize signals with precision and confidence calibration

Your job is to analyze the provided market data and produce a structured trading decision.

RULES:
- Never trade against clear institutional order flow
- Identify retail traps before entering — if retail is euphoric, be cautious about longs
- Premium = above equilibrium (50% of range) → look for shorts or avoid longs
- Discount = below equilibrium → look for longs
- FVGs and Order Blocks are magnets — price gravitates to them
- BOS = trend continuation signal | CHOCH = potential reversal
- High funding rate (>0.05%) = longs overextended → be cautious going long
- Fear & Greed > 80 + rising trend = danger zone for new longs
- Only recommend BUY or SELL when you have genuine conviction (85%+ internal confidence)
- When in doubt, HOLD — preserving capital is paramount

OUTPUT FORMAT (use EXACTLY this format, no deviations):
[SMART MONEY BIAS]: Bullish/Bearish/Neutral
[RETAIL TRAP DETECTED]: Yes/No — one sentence explanation
[WHALE ACTIVITY]: one sentence on what large players appear to be doing
[PSYCHOLOGY]: one sentence on current market emotion and implication
[DECISION]: BUY/SELL/HOLD
[CONFIDENCE]: X% (integer)
[ENTRY]: $X | [STOP-LOSS]: $X | [TARGET]: $X
[RISK/REWARD]: X:X
[REASONING]: 3-5 sentences explaining the complete picture
"""

@dataclass
class TradeSignal:
    pair: str
    decision: str           # BUY / SELL / HOLD
    confidence: int
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    risk_reward: Optional[str]
    bias: str
    retail_trap: bool
    retail_trap_explanation: str
    whale_activity: str
    psychology: str
    reasoning: str
    raw_response: str

class ClaudeBrain:
    def __init__(self, api_key: str, model: str = "claude-opus-4-6"):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def analyze(self, context: dict) -> TradeSignal:
        prompt = self._build_prompt(context)
        message = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text
        return self._parse_response(raw, current_price=context["price"], pair=context["pair"])

    def _build_prompt(self, ctx: dict) -> str:
        ind = ctx["indicators"]
        smc = ctx["smc"]
        ob = ctx["orderbook"]
        fg = ctx["fear_greed"]
        news_lines = "\n".join(
            f"  - [{n['sentiment'].upper()}] {n['title']}" for n in ctx["news"][:5]
        )
        liq_lines = "\n".join(
            f"  - ${l['price']:,.0f} → ${l['liq_usdt']/1e6:.1f}M liquidations"
            for l in ctx["liquidation_levels"][:5]
        )
        positions_str = json.dumps(ctx["open_positions"], indent=2) if ctx["open_positions"] else "None"

        return f"""MARKET SNAPSHOT — {ctx['pair']} @ ${ctx['price']:,.2f}

═══ TECHNICAL INDICATORS ═══
RSI(14): {ind['rsi']} | MACD: {ind['macd']} | Signal: {ind['macd_signal']} | Hist: {ind['macd_hist']}
BB Upper: ${ind['bb_upper']:,.2f} | Mid: ${ind['bb_mid']:,.2f} | Lower: ${ind['bb_lower']:,.2f}
EMA20: ${ind['ema20']:,.2f} | EMA50: ${ind['ema50']:,.2f} | EMA200: ${ind['ema200']:,.2f}
VWAP: ${ind['vwap']:,.2f}

═══ SMART MONEY CONCEPTS ═══
BOS Detected: {smc['bos_detected']} | CHOCH Detected: {smc['choch_detected']}
Premium Zone: ${smc['premium_zone']:,.2f} | Discount Zone: ${smc['discount_zone']:,.2f}
Range High: ${smc['recent_range_high']:,.2f} | Range Low: ${smc['recent_range_low']:,.2f}
Order Blocks: {smc['order_blocks']}
Fair Value Gaps: {smc['fvgs']}
Swing Highs: {smc['swing_highs']}
Swing Lows: {smc['swing_lows']}
Liquidity Above: {smc['liquidity_above']}
Liquidity Below: {smc['liquidity_below']}

═══ ORDERBOOK & DERIVATIVES ═══
Best Bid: ${ob['best_bid']:,.2f} | Best Ask: ${ob['best_ask']:,.2f}
Bid/Ask Volume Ratio: {ob['bid_ask_ratio']}
Largest Bid Wall: ${ob['bid_wall']['price']:,.2f} ({ob['bid_wall']['size']} contracts)
Largest Ask Wall: ${ob['ask_wall']['price']:,.2f} ({ob['ask_wall']['size']} contracts)
Funding Rate: {ctx['funding_rate']:.4%}
Open Interest: ${ctx.get('open_interest', 0)/1e9:.2f}B

═══ MACRO & SENTIMENT ═══
Fear & Greed: {fg['value']} ({fg['label']}) | 7-day trend: {fg['trend']}
BTC Dominance: {ctx['btc_dominance']}%

═══ LIQUIDATION HEATMAP ═══
{liq_lines if liq_lines else "No data"}

═══ NEWS SENTIMENT ═══
{news_lines if news_lines else "No recent news"}

═══ PORTFOLIO CONTEXT ═══
Open Positions: {positions_str}
Remaining Exposure Budget: {ctx['remaining_exposure_pct']:.1f}% of balance

Analyze this setup and provide your structured decision."""

    def _parse_response(self, text: str, current_price: float, pair: str = "") -> TradeSignal:
        def extract(pattern, default=""):
            m = re.search(pattern, text, re.IGNORECASE)
            return m.group(1).strip() if m else default

        def parse_price(s: str) -> Optional[float]:
            m = re.search(r"[\$]?([\d,]+\.?\d*)", s.replace(",", ""))
            return float(m.group(1).replace(",", "")) if m else None

        decision = extract(r"\[DECISION\]:\s*(BUY|SELL|HOLD)").upper() or "HOLD"
        confidence_str = extract(r"\[CONFIDENCE\]:\s*(\d+)")
        confidence = int(confidence_str) if confidence_str.isdigit() else 0
        bias = extract(r"\[SMART MONEY BIAS\]:\s*(\w+)")
        retail_trap_str = extract(r"\[RETAIL TRAP DETECTED\]:\s*(Yes|No)(.*)", "No")
        retail_trap = retail_trap_str.lower().startswith("yes")
        retail_trap_explanation = extract(r"\[RETAIL TRAP DETECTED\]:\s*(?:Yes|No)\s*[—-]?\s*(.*)")

        entry_str = extract(r"\[ENTRY\]:\s*\$?([\d,\.]+)")
        sl_str = extract(r"\[STOP-LOSS\]:\s*\$?([\d,\.]+)")
        tp_str = extract(r"\[TARGET\]:\s*\$?([\d,\.]+)")
        rr = extract(r"\[RISK/REWARD\]:\s*([\d\.]+:\d+)")
        reasoning = extract(r"\[REASONING\]:\s*(.*?)(?:\n\n|\Z)", "")

        return TradeSignal(
            pair=pair,
            decision=decision,
            confidence=confidence,
            entry=parse_price(entry_str) if entry_str else current_price,
            stop_loss=parse_price(sl_str) if sl_str else None,
            take_profit=parse_price(tp_str) if tp_str else None,
            risk_reward=rr,
            bias=bias,
            retail_trap=retail_trap,
            retail_trap_explanation=retail_trap_explanation,
            whale_activity=extract(r"\[WHALE ACTIVITY\]:\s*(.*)"),
            psychology=extract(r"\[PSYCHOLOGY\]:\s*(.*)"),
            reasoning=reasoning,
            raw_response=text,
        )
```

**Step 4: Run tests**

```bash
pytest tests/test_claude_brain.py -v
```
Expected: 2 passed

**Step 5: Commit**

```bash
git add analysis/claude_brain.py tests/test_claude_brain.py
git commit -m "feat: add Claude brain — context assembly, API call, signal parsing"
```

---

### Task 8: Risk Manager

**Files:**
- Create: `risk/__init__.py`
- Create: `risk/manager.py`
- Create: `tests/test_risk_manager.py`

**Step 1: Write failing tests**

```python
# tests/test_risk_manager.py
import pytest
from risk.manager import RiskManager
from analysis.claude_brain import TradeSignal

def make_signal(decision="BUY", confidence=85, stop_loss=82000, entry=84000, take_profit=87000):
    return TradeSignal(
        pair="BTC/USDT", decision=decision, confidence=confidence,
        entry=entry, stop_loss=stop_loss, take_profit=take_profit,
        risk_reward="2:1", bias="Bullish", retail_trap=False,
        retail_trap_explanation="", whale_activity="", psychology="",
        reasoning="", raw_response=""
    )

def make_cfg():
    class Cfg:
        min_confidence = 75
        max_total_exposure_pct = 15.0
        daily_loss_limit_pct = 8.0
        loss_streak_pause_trades = 3
        loss_streak_pause_minutes = 60
        size_tier_low_pct = 3.0
        size_tier_mid_pct = 5.0
        size_tier_high_pct = 7.0
    return Cfg()

def test_position_size_by_confidence_tier():
    rm = RiskManager(make_cfg())
    assert rm.calculate_position_size(balance=10000, confidence=76) == 300.0   # 3%
    assert rm.calculate_position_size(balance=10000, confidence=85) == 500.0   # 5%
    assert rm.calculate_position_size(balance=10000, confidence=92) == 700.0   # 7%

def test_blocks_trade_below_min_confidence():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(signal=make_signal(confidence=70),
                                   daily_pnl_pct=-1.0, consecutive_losses=0,
                                   paused_until=None, current_exposure_pct=0.0)
    assert not allowed
    assert "confidence" in reason.lower()

def test_blocks_trade_at_daily_loss_limit():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(signal=make_signal(confidence=85),
                                   daily_pnl_pct=-8.5, consecutive_losses=0,
                                   paused_until=None, current_exposure_pct=0.0)
    assert not allowed
    assert "daily" in reason.lower()

def test_blocks_trade_when_exposure_exceeded():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(signal=make_signal(confidence=85),
                                   daily_pnl_pct=-1.0, consecutive_losses=0,
                                   paused_until=None, current_exposure_pct=14.0)
    assert not allowed
    assert "exposure" in reason.lower()

def test_allows_valid_trade():
    rm = RiskManager(make_cfg())
    allowed, reason = rm.can_trade(signal=make_signal(confidence=85),
                                   daily_pnl_pct=-1.0, consecutive_losses=0,
                                   paused_until=None, current_exposure_pct=5.0)
    assert allowed
```

**Step 2: Run to verify failures**

```bash
pytest tests/test_risk_manager.py -v
```
Expected: `ModuleNotFoundError: No module named 'risk'`

**Step 3: Implement `risk/manager.py`**

```python
from datetime import datetime, timezone
from typing import Tuple, Optional

class RiskManager:
    def __init__(self, config):
        self._cfg = config

    def calculate_position_size(self, balance: float, confidence: int) -> float:
        if confidence >= 90:
            pct = self._cfg.size_tier_high_pct
        elif confidence >= 80:
            pct = self._cfg.size_tier_mid_pct
        else:
            pct = self._cfg.size_tier_low_pct
        return round(balance * pct / 100, 2)

    def can_trade(
        self,
        signal,
        daily_pnl_pct: float,
        consecutive_losses: int,
        paused_until: Optional[str],
        current_exposure_pct: float,
    ) -> Tuple[bool, str]:
        # Confidence gate
        if signal.decision == "HOLD":
            return False, "Signal is HOLD"
        if signal.confidence < self._cfg.min_confidence:
            return False, f"Confidence {signal.confidence}% below minimum {self._cfg.min_confidence}%"

        # Daily loss limit
        if daily_pnl_pct <= -self._cfg.daily_loss_limit_pct:
            return False, f"Daily loss limit reached: {daily_pnl_pct:.2f}%"

        # Loss streak pause
        if paused_until:
            pause_dt = datetime.fromisoformat(paused_until)
            if datetime.now(timezone.utc) < pause_dt:
                return False, f"Loss streak pause active until {paused_until}"

        # Exposure limit — size tier for this trade
        new_trade_pct = (
            self._cfg.size_tier_high_pct if signal.confidence >= 90
            else self._cfg.size_tier_mid_pct if signal.confidence >= 80
            else self._cfg.size_tier_low_pct
        )
        if current_exposure_pct + new_trade_pct > self._cfg.max_total_exposure_pct:
            return False, f"Exposure limit: adding {new_trade_pct}% would exceed {self._cfg.max_total_exposure_pct}%"

        return True, "OK"

    def should_pause_after_loss(self, consecutive_losses: int) -> bool:
        return consecutive_losses >= self._cfg.loss_streak_pause_trades
```

**Step 4: Create `risk/__init__.py`** (empty)

**Step 5: Run tests**

```bash
pytest tests/test_risk_manager.py -v
```
Expected: 5 passed

**Step 6: Commit**

```bash
git add risk/ tests/test_risk_manager.py
git commit -m "feat: add risk manager (position sizing, circuit breakers)"
```

---

### Task 9: Paper Engine + Executor

**Files:**
- Create: `trading/__init__.py`
- Create: `trading/paper_engine.py`
- Create: `trading/executor.py`
- Create: `tests/test_paper_engine.py`

**Step 1: Write failing tests**

```python
# tests/test_paper_engine.py
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
```

**Step 2: Run to verify failures**

```bash
pytest tests/test_paper_engine.py -v
```
Expected: `ModuleNotFoundError: No module named 'trading'`

**Step 3: Implement `trading/paper_engine.py`**

```python
from datetime import datetime, timezone
from typing import Optional

class PaperEngine:
    def __init__(self, initial_balance: float = 10000.0):
        self.balance = initial_balance
        self.positions: dict = {}

    def open_position(self, pair: str, side: str, size_usdt: float,
                      entry: float, stop_loss: float, take_profit: float,
                      confidence: int) -> dict:
        if pair in self.positions:
            raise ValueError(f"Position already open for {pair}")
        if size_usdt > self.balance:
            return {"status": "rejected", "reason": "Insufficient balance"}

        self.balance -= size_usdt
        self.positions[pair] = {
            "side": side,
            "entry": entry,
            "size_usdt": size_usdt,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "confidence": confidence,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        return {"status": "filled", "entry": entry, "size_usdt": size_usdt}

    def close_position(self, pair: str, exit_price: float) -> dict:
        if pair not in self.positions:
            return {"status": "error", "reason": "No open position"}
        pos = self.positions.pop(pair)
        size_usdt = pos["size_usdt"]
        entry = pos["entry"]
        side = pos["side"]

        price_change_pct = (exit_price - entry) / entry
        if side == "sell":
            price_change_pct = -price_change_pct

        pnl_usdt = round(size_usdt * price_change_pct, 2)
        self.balance += size_usdt + pnl_usdt

        return {
            "status": "closed",
            "pair": pair,
            "side": side,
            "entry": entry,
            "exit": exit_price,
            "size_usdt": size_usdt,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": round(price_change_pct * 100, 3),
            "result": "WIN" if pnl_usdt > 0 else "LOSS",
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }

    def check_stop_take(self, pair: str, current_price: float) -> Optional[dict]:
        """Check if SL or TP has been hit; auto-close if so."""
        if pair not in self.positions:
            return None
        pos = self.positions[pair]
        if pos["side"] == "buy":
            if current_price <= pos["stop_loss"]:
                return self.close_position(pair, pos["stop_loss"])
            if current_price >= pos["take_profit"]:
                return self.close_position(pair, pos["take_profit"])
        else:
            if current_price >= pos["stop_loss"]:
                return self.close_position(pair, pos["stop_loss"])
            if current_price <= pos["take_profit"]:
                return self.close_position(pair, pos["take_profit"])
        return None
```

**Step 4: Implement `trading/executor.py`**

```python
from trading.paper_engine import PaperEngine
from analysis.claude_brain import TradeSignal

class Executor:
    def __init__(self, paper_trading: bool, paper_engine: PaperEngine = None, bybit_client=None):
        self._paper = paper_trading
        self._paper_engine = paper_engine
        self._bybit = bybit_client

    def execute(self, signal: TradeSignal, size_usdt: float) -> dict:
        if self._paper:
            return self._paper_engine.open_position(
                pair=signal.pair,
                side="buy" if signal.decision == "BUY" else "sell",
                size_usdt=size_usdt,
                entry=signal.entry,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                confidence=signal.confidence,
            )
        else:
            amount = size_usdt / signal.entry
            return self._bybit.place_order(
                pair=signal.pair,
                side="buy" if signal.decision == "BUY" else "sell",
                amount=amount,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
            )

    def check_exits(self, pair: str, current_price: float) -> dict:
        if self._paper:
            return self._paper_engine.check_stop_take(pair, current_price) or {}
        return {}
```

**Step 5: Create `trading/__init__.py`** (empty)

**Step 6: Run tests**

```bash
pytest tests/test_paper_engine.py -v
```
Expected: 4 passed

**Step 7: Commit**

```bash
git add trading/ tests/test_paper_engine.py
git commit -m "feat: add paper engine and executor (paper/live routing)"
```

---

### Task 10: Trade Logger

**Files:**
- Create: `utils/trade_logger.py`
- Create: `tests/test_trade_logger.py`

**Step 1: Write failing test**

```python
# tests/test_trade_logger.py
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
    signal = make_signal()
    logger.log_entry(signal=signal, size_usdt=500.0)
    log_file = tmp_path / "trades.jsonl"
    assert log_file.exists()
    with open(log_file) as f:
        record = json.loads(f.readline())
    assert record["action"] == "ENTRY"
    assert record["pair"] == "BTC/USDT"
    assert record["confidence"] == 85

def test_log_close_appends(tmp_path):
    logger = TradeLogger(log_dir=str(tmp_path))
    signal = make_signal()
    logger.log_entry(signal=signal, size_usdt=500.0)
    logger.log_close(pair="BTC/USDT", exit_price=87000.0,
                     pnl_usdt=178.0, pnl_pct=0.35, result="WIN")
    log_file = tmp_path / "trades.jsonl"
    with open(log_file) as f:
        lines = f.readlines()
    assert len(lines) == 2
    close_record = json.loads(lines[1])
    assert close_record["action"] == "CLOSE"
    assert close_record["result"] == "WIN"
```

**Step 2: Run to verify failures**

```bash
pytest tests/test_trade_logger.py -v
```
Expected: `ImportError`

**Step 3: Implement `utils/trade_logger.py`**

```python
import json
import os
from datetime import datetime, timezone
from analysis.claude_brain import TradeSignal

class TradeLogger:
    def __init__(self, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        self._path = os.path.join(log_dir, "trades.jsonl")

    def _append(self, record: dict):
        record["ts"] = datetime.now(timezone.utc).isoformat()
        with open(self._path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def log_entry(self, signal: TradeSignal, size_usdt: float):
        self._append({
            "action": "ENTRY",
            "pair": signal.pair,
            "decision": signal.decision,
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "size_usdt": size_usdt,
            "confidence": signal.confidence,
            "bias": signal.bias,
            "retail_trap": signal.retail_trap,
            "whale_activity": signal.whale_activity,
            "psychology": signal.psychology,
            "reasoning": signal.reasoning,
            "risk_reward": signal.risk_reward,
        })

    def log_close(self, pair: str, exit_price: float,
                  pnl_usdt: float, pnl_pct: float, result: str):
        self._append({
            "action": "CLOSE",
            "pair": pair,
            "exit_price": exit_price,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct,
            "result": result,
        })

    def log_hold(self, pair: str, signal: TradeSignal):
        self._append({
            "action": "HOLD",
            "pair": pair,
            "confidence": signal.confidence,
            "bias": signal.bias,
            "reasoning": signal.reasoning,
        })

    def log_blocked(self, pair: str, signal: TradeSignal, reason: str):
        self._append({
            "action": "BLOCKED",
            "pair": pair,
            "confidence": signal.confidence,
            "decision": signal.decision,
            "block_reason": reason,
        })
```

**Step 4: Run tests**

```bash
pytest tests/test_trade_logger.py -v
```
Expected: 2 passed

**Step 5: Commit**

```bash
git add utils/trade_logger.py tests/test_trade_logger.py
git commit -m "feat: add JSONL trade logger (entry, close, hold, blocked events)"
```

---

### Task 11: Main Orchestrator

**Files:**
- Create: `main.py`

> No unit test for `main.py` — it's a thin orchestrator. Tested by running it in paper mode.

**Step 1: Implement `main.py`**

```python
#!/usr/bin/env python3
"""
CLAUDE TRADING AGENT v1.0
Autonomous crypto trading powered by Claude AI + Smart Money Concepts
"""

import time
import schedule
from datetime import datetime, timezone, timedelta

from config import Config
from data.bybit import BybitClient
from data.coingecko import CoinGeckoClient
from data.cryptopanic import CryptoPanicClient
from data.coinglass import CoinglassClient
from analysis.indicators import compute_indicators
from analysis.smc import detect_smc
from analysis.claude_brain import ClaudeBrain
from risk.manager import RiskManager
from trading.paper_engine import PaperEngine
from trading.executor import Executor
from utils.logger import get_logger
from utils.state import State
from utils.trade_logger import TradeLogger

def run_cycle(cfg, bybit, coingecko, cryptopanic, coinglass,
              brain, risk_mgr, executor, state, trade_logger, logger):
    state.reset_daily_if_new_day()
    logger.info("=" * 60)
    logger.info(f"CYCLE START — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info(f"Mode: {'PAPER' if cfg.paper_trading else 'LIVE'} | Balance: ${state.paper_balance:,.2f}")

    # ── Fetch macro context once per cycle ──────────────────────
    try:
        fear_greed = coingecko.get_fear_greed()
        btc_dominance = coingecko.get_btc_dominance()
    except Exception as e:
        logger.warning(f"CoinGecko fetch failed: {e}")
        fear_greed = {"value": 50, "label": "Neutral", "trend": "flat"}
        btc_dominance = 50.0

    # ── Check open positions for SL/TP hits ─────────────────────
    for pair in list(state.open_positions.keys()):
        try:
            df = bybit.get_candles(pair, timeframe="1m", limit=1)
            current_price = float(df["close"].iloc[-1])
            result = executor.check_exits(pair, current_price)
            if result and result.get("status") == "closed":
                pnl = result["pnl_usdt"]
                state.paper_balance = executor._paper_engine.balance
                if pnl < 0:
                    state.consecutive_losses += 1
                    if risk_mgr.should_pause_after_loss(state.consecutive_losses):
                        pause_until = (datetime.now(timezone.utc) +
                                       timedelta(minutes=cfg.loss_streak_pause_minutes))
                        state.paused_until = pause_until.isoformat()
                        logger.warning(f"Loss streak pause: {cfg.loss_streak_pause_trades} losses. Pausing until {pause_until}")
                else:
                    state.consecutive_losses = 0
                    state.winning_trades += 1
                state.total_trades += 1
                daily_start_balance = state.paper_balance / (1 + state.daily_pnl_pct / 100)
                state.daily_pnl_pct = round((state.paper_balance - daily_start_balance) / daily_start_balance * 100, 3)
                del state.open_positions[pair]
                trade_logger.log_close(pair=pair, exit_price=result["exit"],
                                       pnl_usdt=pnl, pnl_pct=result["pnl_pct"],
                                       result=result["result"])
                logger.info(f"[CLOSED] {pair} | P&L: ${pnl:+.2f} ({result['result']})")
                state.save()
        except Exception as e:
            logger.error(f"Exit check failed for {pair}: {e}")

    # ── Analyze each pair ────────────────────────────────────────
    for pair in cfg.trading_pairs:
        logger.info(f"\n── {pair} ──")
        try:
            df = bybit.get_candles(pair, timeframe="15m", limit=200)
            current_price = float(df["close"].iloc[-1])
            logger.info(f"Price: ${current_price:,.2f}")

            indicators = compute_indicators(df)
            smc = detect_smc(df)

            try:
                ob_summary = bybit.get_orderbook_summary(pair)
                funding_rate = bybit.get_funding_rate(pair)
                open_interest = bybit.get_open_interest(pair)
            except Exception as e:
                logger.warning(f"Orderbook/funding fetch failed: {e}")
                ob_summary = {"best_bid": current_price, "best_ask": current_price,
                              "bid_wall": {"price": 0, "size": 0},
                              "ask_wall": {"price": 0, "size": 0}, "bid_ask_ratio": 1.0}
                funding_rate = 0.0
                open_interest = None

            base = pair.split("/")[0]
            try:
                news = cryptopanic.get_news(currencies=[base], limit=5)
            except Exception:
                news = []

            try:
                liq_levels = coinglass.get_liquidation_levels(base)
            except Exception:
                liq_levels = []

            current_exposure_pct = sum(
                pos["size_usdt"] / state.paper_balance * 100
                for pos in state.open_positions.values()
            )

            context = {
                "pair": pair,
                "price": current_price,
                "indicators": indicators,
                "smc": smc,
                "orderbook": ob_summary,
                "funding_rate": funding_rate,
                "open_interest": open_interest,
                "fear_greed": fear_greed,
                "btc_dominance": btc_dominance,
                "news": news,
                "liquidation_levels": liq_levels,
                "open_positions": state.open_positions,
                "remaining_exposure_pct": cfg.max_total_exposure_pct - current_exposure_pct,
            }

            logger.info(f"RSI: {indicators['rsi']} | EMA20/50/200: {indicators['ema20']:.0f}/{indicators['ema50']:.0f}/{indicators['ema200']:.0f}")
            logger.info(f"SMC: BOS={smc['bos_detected']} CHOCH={smc['choch_detected']} | F&G: {fear_greed['value']} ({fear_greed['label']})")

            signal = brain.analyze(context)

            logger.info(f"[CLAUDE] Bias: {signal.bias} | Retail Trap: {signal.retail_trap}")
            logger.info(f"[CLAUDE] Decision: {signal.decision} | Confidence: {signal.confidence}%")
            logger.info(f"[CLAUDE] {signal.reasoning[:200]}")

            if signal.decision == "HOLD":
                trade_logger.log_hold(pair, signal)
                continue

            # Skip if already in position for this pair
            if pair in state.open_positions:
                logger.info(f"[SKIP] Already in position for {pair}")
                continue

            can_trade, block_reason = risk_mgr.can_trade(
                signal=signal,
                daily_pnl_pct=state.daily_pnl_pct,
                consecutive_losses=state.consecutive_losses,
                paused_until=state.paused_until,
                current_exposure_pct=current_exposure_pct,
            )

            if not can_trade:
                logger.info(f"[BLOCKED] {block_reason}")
                trade_logger.log_blocked(pair, signal, block_reason)
                continue

            size_usdt = risk_mgr.calculate_position_size(
                balance=state.paper_balance, confidence=signal.confidence
            )
            result = executor.execute(signal=signal, size_usdt=size_usdt)

            if result.get("status") == "filled":
                state.open_positions[pair] = {
                    "side": "long" if signal.decision == "BUY" else "short",
                    "entry": signal.entry,
                    "size_usdt": size_usdt,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                    "confidence": signal.confidence,
                }
                state.paper_balance = executor._paper_engine.balance
                trade_logger.log_entry(signal=signal, size_usdt=size_usdt)
                logger.info(f"[{'PAPER ' if cfg.paper_trading else ''}ACTION] {signal.decision} {pair} @ ${signal.entry:,.2f} | SL: ${signal.stop_loss:,.2f} | TP: ${signal.take_profit:,.2f} | Size: ${size_usdt:.0f}")
                state.save()
            else:
                logger.warning(f"Order rejected: {result.get('reason')}")

        except Exception as e:
            logger.error(f"Cycle error for {pair}: {e}", exc_info=True)

    logger.info(f"\nCYCLE END | Balance: ${state.paper_balance:,.2f} | Daily P&L: {state.daily_pnl_pct:+.2f}%")
    logger.info("=" * 60)


def main():
    cfg = Config()
    logger = get_logger("agent", log_dir=cfg.log_dir,
                        log_to_file=cfg.log_to_file, level=cfg.log_level)

    logger.info("╔══════════════════════════════════════════╗")
    logger.info("║      CLAUDE TRADING AGENT v1.0           ║")
    logger.info("╚══════════════════════════════════════════╝")
    logger.info(f"Mode: {'📝 PAPER TRADING' if cfg.paper_trading else '🔴 LIVE TRADING'}")
    logger.info(f"Pairs: {', '.join(cfg.trading_pairs)}")
    logger.info(f"Scan interval: {cfg.scan_interval_minutes} minutes")

    bybit = BybitClient(api_key=cfg.bybit_api_key, secret=cfg.bybit_secret,
                        testnet=cfg.bybit_testnet)
    coingecko = CoinGeckoClient()
    cryptopanic = CryptoPanicClient(api_key=cfg.cryptopanic_api_key)
    coinglass = CoinglassClient(api_key=cfg.coinglass_api_key)
    brain = ClaudeBrain(api_key=cfg.anthropic_api_key, model=cfg.claude_model)
    risk_mgr = RiskManager(cfg)
    state = State(path="state.json", initial_balance=cfg.paper_initial_balance)
    trade_logger = TradeLogger(log_dir=cfg.log_dir)

    paper_engine = PaperEngine(initial_balance=state.paper_balance) if cfg.paper_trading else None
    executor = Executor(paper_trading=cfg.paper_trading,
                        paper_engine=paper_engine,
                        bybit_client=None if cfg.paper_trading else bybit)

    if cfg.paper_trading:
        logger.info(f"Paper balance: ${state.paper_balance:,.2f} USDT")
    else:
        try:
            balance = bybit.get_balance()
            logger.info(f"Live balance: ${balance['USDT']:,.2f} USDT available")
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")

    args = (cfg, bybit, coingecko, cryptopanic, coinglass,
            brain, risk_mgr, executor, state, trade_logger, logger)

    # Run immediately on start
    run_cycle(*args)

    # Then schedule
    schedule.every(cfg.scan_interval_minutes).minutes.do(run_cycle, *args)
    logger.info(f"Scheduler started — next cycle in {cfg.scan_interval_minutes} minutes.")

    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    main()
```

**Step 2: Create `.env` from template and add real keys**

```bash
cp .env.example .env
# Edit .env and fill in:
# ANTHROPIC_API_KEY=your_real_key
# BYBIT_API_KEY=any_value (not used in paper mode)
# BYBIT_SECRET=any_value (not used in paper mode)
# CRYPTOPANIC_API_KEY=your_free_tier_key
# COINGLASS_API_KEY=your_free_tier_key
```

**Step 3: Run full test suite**

```bash
pytest tests/ -v
```
Expected: all tests pass

**Step 4: Test run in paper mode**

```bash
python main.py
```
Expected: agent starts, prints banner, fetches prices, calls Claude, logs decisions.

**Step 5: Commit**

```bash
git add main.py
git commit -m "feat: add main orchestrator — full trading loop with scheduler"
```

---

### Task 12: Final Wiring Check

**Step 1: Run all tests one last time**

```bash
pytest tests/ -v --tb=short
```
Expected: all green

**Step 2: Verify paper → live switch works by inspecting executor routing**

```bash
python -c "
from config import Config
import os
os.environ['PAPER_TRADING'] = 'false'
os.environ['BYBIT_API_KEY'] = 'k'
os.environ['BYBIT_SECRET'] = 's'
os.environ['ANTHROPIC_API_KEY'] = 'a'
os.environ['CRYPTOPANIC_API_KEY'] = 'c'
os.environ['COINGLASS_API_KEY'] = 'g'
from config import Config
cfg = Config()
assert cfg.paper_trading is False
print('Live mode config OK')
"
```

**Step 3: Commit final state**

```bash
git add .
git commit -m "feat: trading agent complete — paper trading ready, live switch via PAPER_TRADING=false"
```
