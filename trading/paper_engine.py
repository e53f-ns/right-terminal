from datetime import datetime, timezone, timedelta
from typing import Optional


# Leverage tiers based on confidence
LEVERAGE_TIERS = [
    (94, 100, 20),
    (88, 93, 10),
    (80, 87, 5),
    (72, 79, 2),
]

# Strategy leverage caps
STRATEGY_LEVERAGE_CAP = {
    "SCALP": 10,
    "SWING": 5,
    "POSITION": 3,
}

# Max SL distance by leverage
MAX_SL_PCT_BY_LEVERAGE = {
    20: 0.5,
    10: 1.0,
    5: 2.0,
    3: 3.0,
    2: 4.0,
    1: 5.0,
}


def compute_leverage(confidence: int, strategy: str = "SCALP") -> int:
    """Determine leverage from confidence score, capped by strategy."""
    lev = 1
    for lo, hi, tier_lev in LEVERAGE_TIERS:
        if lo <= confidence <= hi:
            lev = tier_lev
            break
    cap = STRATEGY_LEVERAGE_CAP.get(strategy, 5)
    return min(lev, cap)


def compute_liquidation_price(entry: float, side: str, leverage: int) -> float:
    """Estimate liquidation price (simplified: 100%/leverage away from entry)."""
    margin_pct = 1.0 / leverage
    if side in ("buy", "long"):
        return round(entry * (1 - margin_pct), 6)
    else:
        return round(entry * (1 + margin_pct), 6)


def max_sl_distance_pct(leverage: int) -> float:
    """Max allowed stop-loss distance as % from entry."""
    for lev_threshold in sorted(MAX_SL_PCT_BY_LEVERAGE.keys(), reverse=True):
        if leverage >= lev_threshold:
            return MAX_SL_PCT_BY_LEVERAGE[lev_threshold]
    return 5.0


class PaperEngine:
    def __init__(self, initial_balance: float = 10000.0,
                 futures_enabled: bool = True,
                 max_margin_per_trade_pct: float = 50.0,
                 free_margin_buffer_pct: float = 30.0,
                 emergency_close_loss_pct: float = 3.0):
        self.balance = initial_balance
        self.positions: dict = {}
        self.futures_enabled = futures_enabled
        self.max_margin_per_trade_pct = max_margin_per_trade_pct
        self.free_margin_buffer_pct = free_margin_buffer_pct
        self.emergency_close_loss_pct = emergency_close_loss_pct

    @property
    def locked_margin(self) -> float:
        return sum(p.get("margin", p.get("size_usdt", 0)) for p in self.positions.values())

    @property
    def free_margin(self) -> float:
        return self.balance - self.locked_margin

    @property
    def equity(self) -> float:
        return self.balance

    def open_position(self, pair: str, side: str, size_usdt: float,
                      entry: float, stop_loss: float, take_profit: float,
                      confidence: int, leverage: int = 1,
                      strategy: str = "SCALP") -> dict:
        if pair in self.positions:
            raise ValueError(f"Position already open for {pair}")

        # HARD BLOCK — SL and TP are mandatory, no exceptions
        if not stop_loss or stop_loss == 0:
            raise ValueError(f"[REJECTED] {pair}: no SL set — trade blocked")
        if not take_profit or take_profit == 0:
            raise ValueError(f"[REJECTED] {pair}: no TP set — trade blocked")

        # Confidence-based max leverage — absolute ceiling 8x
        if confidence >= 90:
            max_conf_lev = 8
        elif confidence >= 80:
            max_conf_lev = 5
        elif confidence >= 70:
            max_conf_lev = 3
        else:
            max_conf_lev = 2
        leverage = min(leverage, max_conf_lev, 8)

        # Max 3% loss per trade: margin × leverage × sl_pct ≤ 3% of balance
        # Auto-reduce leverage until risk fits; if still over at 1x, reduce size
        is_long = side in ("buy", "long")
        sl_pct = abs(entry - stop_loss) / entry if entry else 0
        if sl_pct > 0:
            max_risk_usdt = self.balance * 0.03
            while leverage > 1 and size_usdt * leverage * sl_pct > max_risk_usdt:
                leverage -= 1
            # Final check — if even 1x is too large, trim size
            if size_usdt * sl_pct > max_risk_usdt:
                size_usdt = round(max_risk_usdt / sl_pct, 2)

        if self.futures_enabled and leverage > 1:
            # size_usdt is margin; notional = margin * leverage
            margin = size_usdt
            notional = margin * leverage
        else:
            margin = size_usdt
            notional = size_usdt
            leverage = 1

        # Check margin limits
        equity = self.balance
        max_margin = equity * self.max_margin_per_trade_pct / 100
        if margin > max_margin:
            return {"status": "rejected", "reason": f"Margin ${margin:.0f} exceeds {self.max_margin_per_trade_pct}% limit (${max_margin:.0f})"}

        buffer_needed = equity * self.free_margin_buffer_pct / 100
        if (self.balance - self.locked_margin - margin) < buffer_needed:
            return {"status": "rejected", "reason": f"Free margin would drop below {self.free_margin_buffer_pct}% buffer"}

        if margin > self.balance - self.locked_margin:
            return {"status": "rejected", "reason": "Insufficient free margin"}

        # Validate SL tightness for leveraged trades
        if leverage > 1 and stop_loss and entry:
            sl_dist_pct = abs(entry - stop_loss) / entry * 100
            max_sl = max_sl_distance_pct(leverage)
            if sl_dist_pct > max_sl * 1.5:  # allow some slack
                # Tighten SL automatically
                if side in ("buy", "long"):
                    stop_loss = round(entry * (1 - max_sl / 100), 6)
                else:
                    stop_loss = round(entry * (1 + max_sl / 100), 6)

        liq_price = compute_liquidation_price(entry, side, leverage) if leverage > 1 else None

        self.balance -= margin
        self.positions[pair] = {
            "side": side,
            "entry": entry,
            "size_usdt": notional,  # notional position size
            "margin": margin,       # actual margin locked
            "leverage": leverage,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "confidence": confidence,
            "liquidation_price": liq_price,
            "strategy": strategy,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "status": "filled",
            "entry": entry,
            "size_usdt": notional,
            "margin": margin,
            "leverage": leverage,
            "liquidation_price": liq_price,
        }

    def close_position(self, pair: str, exit_price: float) -> dict:
        if pair not in self.positions:
            return {"status": "error", "reason": "No open position"}
        pos = self.positions.pop(pair)
        margin = pos.get("margin", pos["size_usdt"])
        notional = pos["size_usdt"]
        entry = pos["entry"]
        side = pos["side"]
        leverage = pos.get("leverage", 1)

        price_change_pct = (exit_price - entry) / entry
        if side in ("sell", "short"):
            price_change_pct = -price_change_pct

        # P&L on notional, but capped at margin (can't lose more than margin)
        pnl_usdt = round(notional * price_change_pct, 2)
        if pnl_usdt < -margin:
            pnl_usdt = -margin  # liquidation cap

        self.balance += margin + pnl_usdt

        return {
            "status": "closed",
            "pair": pair,
            "side": side,
            "entry": entry,
            "exit": exit_price,
            "size_usdt": notional,
            "margin": margin,
            "leverage": leverage,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": round(price_change_pct * 100, 3),
            "pnl_on_margin_pct": round(pnl_usdt / margin * 100, 3) if margin else 0,
            "result": "WIN" if pnl_usdt > 0 else "LOSS",
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }

    def check_stop_take(self, pair: str, current_price: float) -> Optional[dict]:
        """Check SL, TP, trailing stop, liquidation, and emergency close."""
        if pair not in self.positions:
            return None
        pos = self.positions[pair]
        side = pos["side"]
        leverage = pos.get("leverage", 1)
        margin = pos.get("margin", pos["size_usdt"])
        notional = pos["size_usdt"]
        entry = pos["entry"]

        # Check liquidation price
        liq_price = pos.get("liquidation_price")
        if liq_price and leverage > 1:
            if side in ("buy", "long") and current_price <= liq_price:
                return self.close_position(pair, liq_price)
            if side in ("sell", "short") and current_price >= liq_price:
                return self.close_position(pair, liq_price)

        # Check emergency close: if unrealized loss > X% of total equity
        price_change_pct = (current_price - entry) / entry
        if side in ("sell", "short"):
            price_change_pct = -price_change_pct
        unrealized_pnl = notional * price_change_pct
        equity = self.balance + self.locked_margin
        if equity > 0 and unrealized_pnl < 0:
            loss_pct_of_equity = abs(unrealized_pnl) / equity * 100
            if loss_pct_of_equity >= self.emergency_close_loss_pct:
                return self.close_position(pair, current_price)

        # Trailing stop logic
        profit_pct = price_change_pct * 100
        if profit_pct >= 1.5:
            # Move SL to breakeven once +1.5%
            if side in ("buy", "long"):
                new_sl = max(pos["stop_loss"] or 0, entry)
                pos["stop_loss"] = new_sl
            else:
                new_sl = min(pos["stop_loss"] or float("inf"), entry)
                pos["stop_loss"] = new_sl
        if profit_pct >= 3.0:
            # Trail SL at 1% below peak price
            peak = pos.get("peak_price", entry)
            if side in ("buy", "long"):
                if current_price > peak:
                    pos["peak_price"] = current_price
                    peak = current_price
                trail_sl = round(peak * 0.99, 6)
                pos["stop_loss"] = max(pos["stop_loss"] or 0, trail_sl)
            else:
                if current_price < peak:
                    pos["peak_price"] = current_price
                    peak = current_price
                trail_sl = round(peak * 1.01, 6)
                pos["stop_loss"] = min(pos["stop_loss"] or float("inf"), trail_sl)

        # Normal SL/TP
        if side in ("buy", "long"):
            if pos["stop_loss"] and current_price <= pos["stop_loss"]:
                return self.close_position(pair, pos["stop_loss"])
            if pos["take_profit"] and current_price >= pos["take_profit"]:
                return self.close_position(pair, pos["take_profit"])
        else:
            if pos["stop_loss"] and current_price >= pos["stop_loss"]:
                return self.close_position(pair, pos["stop_loss"])
            if pos["take_profit"] and current_price <= pos["take_profit"]:
                return self.close_position(pair, pos["take_profit"])
        return None

    def check_orphans(self, max_age_hours: int = 48) -> list:
        """Return list of pairs open longer than max_age_hours."""
        now = datetime.now(timezone.utc)
        orphans = []
        for pair, pos in self.positions.items():
            opened = pos.get("opened_at", "")
            if not opened:
                orphans.append(pair)
                continue
            try:
                opened_dt = datetime.fromisoformat(opened)
                if (now - opened_dt) > timedelta(hours=max_age_hours):
                    orphans.append(pair)
            except (ValueError, TypeError):
                orphans.append(pair)
        return orphans

    def check_margin_health(self) -> Optional[str]:
        """If free margin < buffer, return pair of smallest position to close."""
        if not self.positions:
            return None
        equity = self.balance + self.locked_margin
        buffer_needed = equity * self.free_margin_buffer_pct / 100
        if self.free_margin < buffer_needed:
            smallest = min(self.positions.items(),
                           key=lambda x: x[1].get("margin", x[1]["size_usdt"]))
            return smallest[0]
        return None
