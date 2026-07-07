import json
import re
from dataclasses import dataclass, field
from typing import Optional, List

import anthropic

SYSTEM_PROMPT = """You are a crypto SCALP trading bot. Respond ONLY in JSON.
RULES:
1. Prefer trades aligned with BTC_TREND. BEAR→prefer SELL, BULL→prefer BUY. Exception: extreme F&G (<20 or >80) allows counter-trend scalps.
2. HTF_BIAS=BULL/BEAR or BULL_LEAN/BEAR_LEAN → trade aligned direction. HTF_BIAS=MIXED → reduce confidence by 15 but still analyze; only HOLD if no other edge.
3. Volume >0.7x average is acceptable for scalps. Low volume alone is not a HOLD.
4. SL MUST be within 1.5% of entry. If proper SL needs >1.5% → HOLD.
5. R:R must be ≥1.5:1.
6. Strategy is SCALP only. Max hold 4 hours.
7. F&G <20 (Extreme Fear) = strong mean-reversion signal. F&G >80 = strong fade signal.

PRE-TRADE CHECKLIST:
✓ Clear entry level with nearby support/resistance?
✓ SL within 1.5% of entry?
✓ R:R ≥ 1.5:1?
✓ Not fighting a strong opposing trend?
If setup is valid, trade it. Bias toward action in extreme F&G conditions.

{"action":"BUY/SELL/HOLD","confidence":0-100,"entry":price,"sl":price,"tp":price,"strategy":"SCALP","leverage":1,"reason":"max 20 words"}"""


@dataclass
class TradeSignal:
    pair: str
    decision: str           # BUY / SELL / HOLD
    confidence: int         # signal score 0-100
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]   # target 1
    risk_reward: Optional[str]
    bias: str
    retail_trap: bool
    retail_trap_explanation: str
    whale_activity: str
    psychology: str
    reasoning: str          # thesis
    raw_response: str
    strategy: str = "NONE"          # SCALP / SWING / POSITION / NONE
    trade_tier: str = "NONE"        # kept for backward compat
    signal_breakdown: str = ""
    hold_time: str = ""
    target_2: Optional[float] = None
    size_pct: float = 0.0
    leverage: int = 1
    margin: Optional[float] = None
    notional: Optional[float] = None
    liquidation_price: Optional[float] = None


class ClaudeBrain:
    def __init__(self, api_key: str, model: str = "claude-opus-4-6"):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def analyze(self, context: dict) -> TradeSignal:
        pair = context.get("pair", "")
        prompt = self._build_prompt(context)

        for attempt in range(2):
            try:
                message = self._client.messages.create(
                    model=self._model,
                    max_tokens=200,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = message.content[0].text
            except Exception as e:
                return self._hold_signal(pair, f"API error: {e}")

            signal = self._parse_response(raw, current_price=context["price"], pair=pair)

            # If Claude omitted SL or TP, retry once with an explicit reminder
            needs_retry = (
                attempt == 0
                and signal.decision == "HOLD"
                and signal.reasoning
                and ("no SL" in signal.reasoning or "no TP" in signal.reasoning)
            )
            if needs_retry:
                prompt = (
                    prompt
                    + "\n\nRETRY: Your previous response was missing sl or tp fields. "
                    "You MUST include both sl and tp in your JSON response, or respond HOLD."
                )
                continue
            return signal

        return self._hold_signal(pair, "no SL/TP after retry — HOLD")

    def _build_prompt(self, ctx: dict) -> str:
        ind = ctx["indicators"]
        smc = ctx["smc"]
        ob = ctx["orderbook"]
        fg = ctx["fear_greed"]

        price = ctx["price"]
        ema20 = ind.get("ema20", 0)
        ema50 = ind.get("ema50", 0)
        ema200 = ind.get("ema200", 0)
        rsi = ind.get("rsi", 50)
        macd = ind.get("macd", 0)
        vol_ratio = ind.get("volume_ratio", 1.0)
        funding = ctx.get("funding_rate", 0)
        oi = (ctx.get('open_interest') or 0) / 1e9
        fg_val = fg.get("value", 50)
        bos = smc.get("bos_detected", False)
        choch = smc.get("choch_detected", False)
        fvg = bool(smc.get("fvgs"))

        bid_ratio = ob.get("bid_ask_ratio", 1.0)
        bid_walls = len(ob.get("top_bid_walls", []))
        ask_walls = len(ob.get("top_ask_walls", []))

        open_pos = ctx.get("open_positions", {})
        open_count = len(open_pos)
        balance = ctx.get("remaining_exposure_pct", 60)

        trend = "BULL" if price > ema200 and ema200 > 0 else "BEAR"
        macd_dir = "POS" if macd > 0 else "NEG"
        vol_label = "SPIKE" if vol_ratio > 2 else "NORMAL"

        # BTC macro trend from context
        btc_trend = ctx.get("btc_trend", "UNKNOWN")
        htf_bias = ctx.get("htf_bias", "UNKNOWN")

        # News summary (compact)
        news = ctx.get("news", [])
        news_summary = "none"
        if news:
            sentiments = [n.get("sentiment", "neutral").upper() for n in news[:3]]
            news_summary = "/".join(sentiments)

        # MiroFish scenario summary
        scenario = ctx.get("scenario_summary", "none")

        # Learning from past mistakes
        learning = ctx.get("learning_summary", "none")

        return (
            f"MARKET: {ctx['pair']} | ${price:,.2f}\n"
            f"BTC_TREND: {btc_trend} | HTF_BIAS: {htf_bias}\n"
            f"TREND: EMA20={ema20:,.2f} EMA50={ema50:,.2f} EMA200={ema200:,.2f} | {trend}\n"
            f"MOMENTUM: RSI={rsi:.0f} | MACD={macd_dir}\n"
            f"VOLUME: {vol_ratio:.1f}x avg | {vol_label}\n"
            f"SMC: BOS={'Y' if bos else 'N'} CHOCH={'Y' if choch else 'N'} FVG={'Y' if fvg else 'N'}\n"
            f"FUNDING: {funding:.4%} | OI: ${oi:.2f}B | BID/ASK={bid_ratio:.2f}\n"
            f"SENTIMENT: F&G={fg_val} | NEWS={news_summary}\n"
            f"SCENARIO: {scenario}\n"
            f"MISTAKES: {learning}\n"
            f"LIQUIDITY: walls_above={ask_walls} walls_below={bid_walls}\n"
            f"OPEN_POSITIONS: {open_count}/8 | EXPOSURE_LEFT: {balance:.0f}%\n"
            f"RULE: SL must be within 1% of entry. Only SCALP."
        )

    def _parse_response(self, text: str, current_price: float, pair: str = "") -> TradeSignal:
        # Try JSON parse first
        try:
            # Find JSON in response (may have surrounding text)
            json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                action = data.get("action", "HOLD").upper()
                if action not in ("BUY", "SELL", "HOLD"):
                    action = "HOLD"
                confidence = int(data.get("confidence", 0))
                entry = float(data.get("entry", current_price)) if data.get("entry") else current_price
                sl = float(data.get("sl", 0)) if data.get("sl") else None
                tp = float(data.get("tp", 0)) if data.get("tp") else None
                # Force SCALP only
                strategy = "SCALP"
                leverage = int(data.get("leverage", 1))
                reason = data.get("reason", "")

                # Safety gates: BUY/SELL with no SL, no TP, or confidence=0 → HOLD
                if action != "HOLD":
                    if confidence == 0:
                        return self._hold_signal(pair, "confidence=0 from Claude — defaulting HOLD")
                    if sl is None or sl == 0:
                        return self._hold_signal(pair, "no SL in Claude response — defaulting HOLD")
                    if tp is None or tp == 0:
                        return self._hold_signal(pair, "no TP in Claude response — defaulting HOLD")

                return TradeSignal(
                    pair=pair,
                    decision=action,
                    confidence=confidence,
                    entry=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward="",
                    bias="",
                    retail_trap=False,
                    retail_trap_explanation="",
                    whale_activity="",
                    psychology="",
                    reasoning=reason,
                    raw_response=text,
                    strategy=strategy,
                    trade_tier=strategy,
                    leverage=leverage,
                )
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # Fallback: try old regex parsing
        def extract(pattern, default=""):
            m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            return m.group(1).strip() if m else default

        def parse_price(s: str) -> Optional[float]:
            cleaned = s.replace(",", "").replace("$", "")
            m = re.search(r"([\d]+\.?\d*)", cleaned)
            return float(m.group(1)) if m else None

        decision = extract(r"\b(BUY|SELL|HOLD)\b").upper() or "HOLD"
        score_str = extract(r"(\d{2,3})\s*[/%]")
        confidence = int(score_str) if score_str.isdigit() else 0

        # Regex fallback never has SL/TP — always treat non-HOLD as HOLD
        if decision != "HOLD":
            return self._hold_signal(pair, f"regex fallback (no SL/TP parseable) — defaulting HOLD")

        return TradeSignal(
            pair=pair,
            decision=decision,
            confidence=confidence,
            entry=current_price,
            stop_loss=None,
            take_profit=None,
            risk_reward="",
            bias="",
            retail_trap=False,
            retail_trap_explanation="",
            whale_activity="",
            psychology="",
            reasoning=text[:100],
            raw_response=text,
            strategy="SCALP",
        )

    @staticmethod
    def _hold_signal(pair: str, reason: str) -> TradeSignal:
        return TradeSignal(
            pair=pair,
            decision="HOLD",
            confidence=0,
            entry=None,
            stop_loss=None,
            take_profit=None,
            risk_reward="",
            bias="",
            retail_trap=False,
            retail_trap_explanation="",
            whale_activity="",
            psychology="",
            reasoning=reason,
            raw_response=reason,
            strategy="NONE",
        )
