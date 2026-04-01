# utils/learning_log.py
import json
import os
from collections import Counter
from datetime import datetime, timezone


# Asia session: 0-7 UTC (low liquidity for crypto)
ASIA_LOW_VOLUME_HOURS = range(0, 8)
LOW_CONFIDENCE_THRESHOLD = 68
OVERTRADING_WINDOW_MINUTES = 60
OVERTRADING_THRESHOLD = 3


class LearningLog:
    def __init__(self, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        self._path = os.path.join(log_dir, "learning_log.jsonl")

    def record_trade(self, *, pair: str, strategy: str, side: str,
                     entry_reason: str, result: str, pnl_pct: float,
                     entry_price: float, exit_price: float,
                     confidence: int, opened_at: str, closed_at: str,
                     btc_trend: str = "UNKNOWN", htf_bias: str = "UNKNOWN"):
        """Record a closed trade with automatic mistake detection."""
        mistakes = self._detect_mistakes(
            side=side, result=result, confidence=confidence,
            opened_at=opened_at, btc_trend=btc_trend, htf_bias=htf_bias,
        )
        what_went_right = self._detect_wins(
            side=side, result=result, confidence=confidence,
            opened_at=opened_at, btc_trend=btc_trend, htf_bias=htf_bias,
            pnl_pct=pnl_pct,
        )
        record = {
            "pair": pair,
            "strategy": strategy,
            "side": side,
            "entry_reason": entry_reason,
            "result": result,
            "pnl_pct": round(pnl_pct, 3),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "confidence": confidence,
            "opened_at": opened_at,
            "closed_at": closed_at,
            "btc_trend": btc_trend,
            "htf_bias": htf_bias,
            "mistakes": mistakes,
            "what_went_right": what_went_right,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with open(self._path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def read_recent(self, n: int = 20) -> list[dict]:
        """Read the last n entries from the learning log."""
        if not os.path.exists(self._path):
            return []
        entries = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries[-n:]

    def get_pattern_summary(self, n: int = 20) -> str:
        """Summarize mistake patterns from recent trades for Claude's prompt."""
        entries = self.read_recent(n)
        if not entries:
            return "none"

        mistake_counts = Counter()
        for e in entries:
            for m in e.get("mistakes", []):
                mistake_counts[m] += 1

        losses = [e for e in entries if e["result"] == "LOSS"]
        wins = [e for e in entries if e["result"] == "WIN"]
        total = len(entries)
        win_rate = len(wins) / total * 100 if total else 0

        parts = [f"Last {total} trades: {win_rate:.0f}% win rate"]
        for mistake, count in mistake_counts.most_common(5):
            parts.append(f"{mistake}:{count}")

        # Pair-specific patterns
        pair_losses = Counter(e["pair"] for e in losses)
        for pair, count in pair_losses.most_common(2):
            if count >= 2:
                parts.append(f"{pair} lost {count}x")

        # Winning patterns
        win_tags = Counter()
        for e in wins:
            for t in e.get("what_went_right", []):
                win_tags[t] += 1
        if win_tags:
            top = win_tags.most_common(3)
            parts.append("WINS:" + ",".join(f"{t}:{c}" for t, c in top))

        return " | ".join(parts)

    def get_top_mistakes(self, n: int = 3) -> list[dict]:
        """Get top n most frequent recent mistakes with details."""
        entries = self.read_recent(20)
        mistake_details = {}
        for e in entries:
            for m in e.get("mistakes", []):
                if m not in mistake_details:
                    mistake_details[m] = {"count": 0, "examples": []}
                mistake_details[m]["count"] += 1
                if len(mistake_details[m]["examples"]) < 2:
                    mistake_details[m]["examples"].append(
                        f"{e['pair']} {e['side']} {e['pnl_pct']:+.1f}%"
                    )
        sorted_mistakes = sorted(mistake_details.items(),
                                 key=lambda x: x[1]["count"], reverse=True)
        return [{"mistake": k, **v} for k, v in sorted_mistakes[:n]]

    def _detect_wins(self, *, side: str, result: str, confidence: int,
                     opened_at: str, btc_trend: str, htf_bias: str,
                     pnl_pct: float) -> list[str]:
        """Tag winning trades with what went right to build pattern library."""
        if result != "WIN":
            return []
        tags = []
        # Trend aligned
        if side == "BUY" and btc_trend in ("BULL", "NEUTRAL"):
            tags.append("trend_aligned")
        elif side == "SELL" and btc_trend in ("BEAR", "NEUTRAL"):
            tags.append("trend_aligned")
        # HTF confirms direction
        if (side == "BUY" and htf_bias == "BULL") or (side == "SELL" and htf_bias == "BEAR"):
            tags.append("htf_confirmed")
        # High conviction entry
        if confidence >= 80:
            tags.append("high_conviction")
        # London/NY session (8-17 UTC) — best liquidity
        try:
            hour = datetime.fromisoformat(opened_at).hour
            if 8 <= hour <= 17:
                tags.append("good_session")
        except (ValueError, TypeError):
            pass
        # Strong move captured
        if pnl_pct >= 1.5:
            tags.append("strong_move")
        return tags

    def _detect_mistakes(self, *, side: str, result: str, confidence: int,
                         opened_at: str, btc_trend: str,
                         htf_bias: str) -> list[str]:
        """Rule-based mistake detection."""
        mistakes = []
        if result != "LOSS":
            return mistakes

        # Counter-trend: bought in bear or sold in bull
        if side == "BUY" and btc_trend == "BEAR":
            mistakes.append("counter_trend")
        elif side == "SELL" and btc_trend == "BULL":
            mistakes.append("counter_trend")
        # Also check HTF bias
        if side == "BUY" and htf_bias == "BEAR":
            if "counter_trend" not in mistakes:
                mistakes.append("counter_trend")
        elif side == "SELL" and htf_bias == "BULL":
            if "counter_trend" not in mistakes:
                mistakes.append("counter_trend")

        # Low confidence entry that lost
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            mistakes.append("low_confidence")

        # Wrong session (Asia low-volume hours)
        try:
            hour = datetime.fromisoformat(opened_at).hour
            if hour in ASIA_LOW_VOLUME_HOURS:
                mistakes.append("wrong_session")
        except (ValueError, TypeError):
            pass

        # Overtrading: check recent entries for trades in same hour
        recent = self.read_recent(10)
        try:
            trade_time = datetime.fromisoformat(opened_at)
            trades_in_window = 0
            for e in recent:
                try:
                    t = datetime.fromisoformat(e["opened_at"])
                    diff_minutes = abs((trade_time - t).total_seconds()) / 60
                    if diff_minutes <= OVERTRADING_WINDOW_MINUTES:
                        trades_in_window += 1
                except (ValueError, KeyError):
                    pass
            if trades_in_window >= OVERTRADING_THRESHOLD:
                mistakes.append("overtrading")
        except (ValueError, TypeError):
            pass

        return mistakes
