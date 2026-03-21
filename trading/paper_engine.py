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
