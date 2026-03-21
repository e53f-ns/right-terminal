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
