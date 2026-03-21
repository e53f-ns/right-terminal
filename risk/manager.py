from datetime import datetime, timezone
from typing import Optional, Tuple


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
        if signal.decision == "HOLD":
            return False, "Signal is HOLD"

        if signal.confidence < self._cfg.min_confidence:
            return False, f"Confidence {signal.confidence}% below minimum {self._cfg.min_confidence}%"

        if daily_pnl_pct <= -self._cfg.daily_loss_limit_pct:
            return False, f"Daily loss limit reached: {daily_pnl_pct:.2f}%"

        if paused_until:
            pause_dt = datetime.fromisoformat(paused_until)
            if datetime.now(timezone.utc) < pause_dt:
                return False, f"Loss streak pause active until {paused_until}"

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
