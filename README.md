# Right Terminal

Autonomous crypto trading agent: combines quantitative technical analysis, Smart Money Concepts (SMC), liquidity/whale context and macro data into an LLM-powered decision engine. Paper trading by default — switching to live is one `.env` change.

## How it works

1. Pulls market data: Bybit & OKX klines, funding, open interest; Coinglass liquidation levels; CoinGecko dominance; CryptoPanic news
2. Computes indicators (EMA/RSI/ATR, volume profile), SMC structures (order blocks, FVG, liquidity sweeps) and scenario trees
3. Assembles full context and queries the Claude API (persona: ICT analyst + behavioral finance + quant) for bias, decision, confidence, entry/SL/TP
4. Risk manager enforces exposure budget, daily loss limit and loss-streak pauses
5. Paper engine executes and logs trades; a learning log feeds past mistakes back into the next prompt

## Modules

- `analysis/` — indicators, SMC, scenario engine, LLM brain
- `data/` — Bybit, OKX, Coinglass, CoinGecko, CryptoPanic clients
- `polymarket/`, `kalshi/` — prediction-market screeners & executors
- `risk/`, `trading/` — risk manager, paper engine
- `web_dashboard.py` — live dashboard
- `tests/` — pytest suite

## Run

```bash
pip install -r requirements.txt
cp .env.example .env    # API keys; PAPER_TRADING=true by default
python main.py
```
