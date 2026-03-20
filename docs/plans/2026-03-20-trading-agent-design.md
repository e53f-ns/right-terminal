# Trading Agent Design
_Date: 2026-03-20_

## Overview

An autonomous crypto trading agent that combines quantitative technical analysis, Smart Money Concepts (SMC), whale/institutional behavior monitoring, and macro context into a single Claude-powered decision engine. Paper trading by default; switching to live is one `.env` change.

---

## Goals

- Paper trade BTC, ETH, SOL, BNB, XRP on 15-minute cycles
- Use Claude (claude-opus-4-6) as the analytical brain combining ICT/SMC, on-chain whale analysis, behavioral finance, and quant methodology
- Switch to live Bybit trading by setting `PAPER_TRADING=false`
- Never risk more than defined thresholds; hard circuit breakers enforced before every trade

---

## Architecture: Modular Python Package

```
trading-agent/
├── .env                        # All config — single source of truth
├── .env.example                # Documented template
├── main.py                     # Entry point + 15-minute trading loop
├── config.py                   # Loads/validates all env vars
│
├── data/
│   ├── bybit.py                # OHLCV, orderbook, funding rates, open interest
│   ├── coingecko.py            # Fear & Greed index, BTC dominance
│   ├── cryptopanic.py          # News headlines + sentiment
│   └── coinglass.py            # Liquidation heatmap data
│
├── analysis/
│   ├── indicators.py           # RSI, MACD, Bollinger Bands, EMA 20/50/200, VWAP
│   ├── smc.py                  # Order blocks, FVG, BOS/CHOCH, liquidity sweeps
│   └── claude_brain.py         # Context assembly + Claude API call + output parsing
│
├── trading/
│   ├── executor.py             # Unified order interface (routes to paper or live)
│   └── paper_engine.py         # Simulates fills, tracks paper P&L
│
├── risk/
│   └── manager.py              # Position sizing, exposure cap, circuit breakers
│
├── utils/
│   ├── logger.py               # Structured logs to console + file
│   └── state.py                # Persists positions, P&L, loss streak to state.json
│
└── logs/
    └── trades.jsonl            # Append-only structured trade log
```

---

## Trading Loop (every 15 minutes, per pair)

1. **Fetch data** (parallel): Bybit candles/OB/funding/OI, CoinGecko F&G + dominance, CryptoPanic news, Coinglass liquidation levels
2. **Compute indicators**: RSI, MACD, BB, EMA 20/50/200, VWAP
3. **Run SMC analysis**: detect order blocks, FVGs, BOS/CHOCH, liquidity sweeps, premium/discount zones
4. **Assemble Claude context**: all of the above + funding rate + OI trend + F&G trend + news + liq levels + BTC dominance + current positions + remaining exposure budget
5. **Call Claude API**: persona = ICT + whale analyst + behavioral finance psychologist + Renaissance quant
6. **Parse Claude output**: bias, retail trap flag, whale activity, psychology read, decision, confidence, entry, SL, TP, R:R, reasoning
7. **Risk gate**: check circuit breakers → size position → check exposure cap
8. **Execute**: paper engine or Bybit live API
9. **Log + persist state**

---

## Claude Analysis Framework

Every Claude call instructs it to analyze:

**Technical Analysis**
- RSI, MACD, Bollinger Bands, EMA 20/50/200
- Volume profile and VWAP
- Support/resistance key levels

**Smart Money Concepts (SMC)**
- Order blocks (bullish/bearish)
- Fair Value Gaps (FVG) and imbalances
- Break of Structure (BOS) and Change of Character (CHOCH)
- Liquidity sweeps (stop hunts above/below key levels)
- Premium and discount zones
- Inducement traps for retail traders

**Whale & Institutional Behavior**
- Large orderbook walls (hidden liquidity)
- Liquidation heatmaps
- Funding rates (longs/shorts overextended)
- Open interest changes

**Market Psychology & Human Factor**
- Fear & Greed Index with trend
- Retail sentiment via news headlines
- FOMO/panic trap detection
- Smart money engineering detection

**Macro Context**
- BTC dominance trend
- Upcoming high-impact news events

**Required output format:**
```
[SMART MONEY BIAS]: Bullish/Bearish/Neutral
[RETAIL TRAP DETECTED]: Yes/No — explain
[WHALE ACTIVITY]: ...
[PSYCHOLOGY]: ...
[DECISION]: BUY/SELL/HOLD
[CONFIDENCE]: X%
[ENTRY]: $X | [STOP-LOSS]: $X | [TARGET]: $X
[RISK/REWARD]: X:X
[REASONING]: 3-5 sentences
```

---

## Risk Management

| Confidence | Position Size |
|------------|--------------|
| 75–80%     | 3% of balance |
| 80–90%     | 5% of balance |
| 90%+       | 7% of balance |

- Min confidence to trade: 75%
- Max total open exposure: 15% of balance
- Stop-loss: mandatory on every trade
- 3 consecutive losses → pause trading 60 minutes
- Daily P&L ≤ -8% → stop all trading until next UTC day

---

## Configuration (`.env`)

```bash
PAPER_TRADING=true
BYBIT_API_KEY=
BYBIT_SECRET=
BYBIT_TESTNET=false
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-opus-4-6
CRYPTOPANIC_API_KEY=
COINGLASS_API_KEY=
TRADING_PAIRS=BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT
SCAN_INTERVAL_MINUTES=15
MIN_CONFIDENCE=75
MAX_TOTAL_EXPOSURE_PCT=15
DAILY_LOSS_LIMIT_PCT=8
LOSS_STREAK_PAUSE_TRADES=3
LOSS_STREAK_PAUSE_MINUTES=60
SIZE_TIER_LOW_PCT=3
SIZE_TIER_MID_PCT=5
SIZE_TIER_HIGH_PCT=7
PAPER_INITIAL_BALANCE=10000
LOG_LEVEL=INFO
LOG_TO_FILE=true
LOG_DIR=logs
```

---

## State (`state.json`)

```json
{
  "paper_balance": 10000.00,
  "open_positions": {},
  "daily_pnl_pct": 0.0,
  "daily_pnl_reset_date": "2026-03-20",
  "consecutive_losses": 0,
  "paused_until": null,
  "total_trades": 0,
  "winning_trades": 0
}
```

## Trade Log (`logs/trades.jsonl`)

Append-only, one JSON object per line. Entry events and close events logged separately for easy P&L analysis.

---

## Paper → Live Migration

1. Set `PAPER_TRADING=false` in `.env`
2. Add real `BYBIT_API_KEY` and `BYBIT_SECRET`
3. No code changes required
