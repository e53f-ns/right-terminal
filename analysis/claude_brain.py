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
            f"  - ${l['price']:,.0f} → ${l['liq_usdt'] / 1e6:.1f}M liquidations"
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
Open Interest: ${ctx.get('open_interest', 0) / 1e9:.2f}B

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
            m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            return m.group(1).strip() if m else default

        def parse_price(s: str) -> Optional[float]:
            cleaned = s.replace(",", "").replace("$", "")
            m = re.search(r"([\d]+\.?\d*)", cleaned)
            return float(m.group(1)) if m else None

        decision = extract(r"\[DECISION\]:\s*(BUY|SELL|HOLD)").upper() or "HOLD"
        confidence_str = extract(r"\[CONFIDENCE\]:\s*(\d+)")
        confidence = int(confidence_str) if confidence_str.isdigit() else 0
        bias = extract(r"\[SMART MONEY BIAS\]:\s*(\w+)")
        retail_trap_full = extract(r"\[RETAIL TRAP DETECTED\]:\s*(.*?)(?=\n\[|\Z)")
        retail_trap = retail_trap_full.lower().startswith("yes")
        retail_trap_explanation = re.sub(r"^(Yes|No)\s*[—\-]?\s*", "", retail_trap_full, flags=re.IGNORECASE).strip()

        entry_str = extract(r"\[ENTRY\]:\s*\$?([\d,\.]+)")
        sl_str = extract(r"\[STOP-LOSS\]:\s*\$?([\d,\.]+)")
        tp_str = extract(r"\[TARGET\]:\s*\$?([\d,\.]+)")
        rr = extract(r"\[RISK/REWARD\]:\s*([\d\.]+:\d+)")
        reasoning = extract(r"\[REASONING\]:\s*(.*?)(?:\n\n|\Z)")

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
            whale_activity=extract(r"\[WHALE ACTIVITY\]:\s*(.*?)(?=\n\[|\Z)"),
            psychology=extract(r"\[PSYCHOLOGY\]:\s*(.*?)(?=\n\[|\Z)"),
            reasoning=reasoning,
            raw_response=text,
        )
