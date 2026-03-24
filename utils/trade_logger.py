import json
import os
from datetime import datetime, timezone, timedelta

from analysis.claude_brain import TradeSignal


class TradeLogger:
    def __init__(self, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        self._path = os.path.join(log_dir, "trades.jsonl")
        self._recent: dict = {}  # {(pair,action): timestamp} for dedup

    def _is_duplicate(self, pair: str, action: str) -> bool:
        """Reject same pair+action within 60 seconds."""
        key = (pair, action)
        now = datetime.now(timezone.utc)
        last = self._recent.get(key)
        if last and (now - last) < timedelta(seconds=60):
            return True
        self._recent[key] = now
        return False

    def _append(self, record: dict):
        record["ts"] = datetime.now(timezone.utc).isoformat()
        with open(self._path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def log_entry(self, signal: TradeSignal, size_usdt: float,
                  asset_class: str = "crypto",
                  leverage: int = 1, margin: float = None):
        if self._is_duplicate(signal.pair, "ENTRY"):
            return
        self._append({
            "action": "ENTRY",
            "pair": signal.pair,
            "decision": signal.decision,
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "size_usdt": size_usdt,
            "margin": margin or size_usdt,
            "leverage": leverage,
            "confidence": signal.confidence,
            "bias": signal.bias,
            "strategy_type": getattr(signal, "strategy", "SCALP"),
            "asset_class": asset_class,
            "reasoning": signal.reasoning,
            "risk_reward": signal.risk_reward,
        })

    def log_close(self, pair: str, exit_price: float,
                  pnl_usdt: float, pnl_pct: float, result: str,
                  entry_price: float = None,
                  strategy_type: str = "SCALP",
                  asset_class: str = "crypto",
                  leverage: int = 1, margin: float = None):
        if self._is_duplicate(pair, "CLOSE"):
            return
        self._append({
            "action": "CLOSE",
            "pair": pair,
            "entry": entry_price,
            "exit_price": exit_price,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct,
            "result": result,
            "strategy_type": strategy_type,
            "asset_class": asset_class,
            "leverage": leverage,
            "margin": margin,
        })

    def log_hold(self, pair: str, signal: TradeSignal,
                 asset_class: str = "crypto"):
        pass

    def log_blocked(self, pair: str, signal: TradeSignal, reason: str,
                    asset_class: str = "crypto"):
        pass
