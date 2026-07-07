from datetime import datetime, timezone
from typing import Optional, Tuple


# Strategy-specific position sizing tables
# SWING disabled — SCALP only until win rate > 50%
SIZING = {
    "SCALP": {
        "tiers": [(60, 69, 3), (70, 79, 3), (80, 89, 3), (90, 100, 3)],
        "min_score": 60,
        "max_positions": 5,
    },
    "SWING": {
        "tiers": [(65, 74, 3), (75, 84, 3), (85, 89, 3), (90, 100, 3)],
        "min_score": 65,
        "max_positions": 0,  # disabled
    },
    "POSITION": {
        "tiers": [(70, 79, 3), (80, 89, 3), (90, 94, 3), (95, 100, 3)],
        "min_score": 70,
        "max_positions": 0,  # disabled
    },
}

# SCALP gets +20% size bonus — best performing strategy
SCALP_SIZE_BONUS = 1.2
# Max 3.6% per trade (3% * 1.2 SCALP bonus; SWING/POSITION disabled anyway)
MAX_SIZE_PCT = 3.6

# Partial position: below strategy min but above partial threshold → half size
PARTIAL_THRESHOLD_OFFSET = 10  # e.g. SCALP min 60, partial at 50


class RiskManager:
    def __init__(self, config):
        self._cfg = config

    def calculate_position_size(self, balance: float, confidence: int,
                                strategy: str = "SCALP",
                                partial: bool = False) -> float:
        """Size based on strategy + confidence score. Capped at MAX_SIZE_PCT."""
        spec = SIZING.get(strategy, SIZING["SCALP"])
        pct = spec["tiers"][0][2]  # default to lowest tier
        for lo, hi, tier_pct in spec["tiers"]:
            if lo <= confidence <= hi:
                pct = tier_pct
                break
        pct = min(pct, MAX_SIZE_PCT)
        size = round(balance * pct / 100, 2)
        # Apply SCALP bonus after base calculation
        if strategy == "SCALP":
            size = round(size * SCALP_SIZE_BONUS, 2)
        if partial:
            size = round(size * 0.5, 2)
        return size

    def can_trade(
        self,
        signal,
        daily_pnl_pct: float,
        consecutive_losses: int,
        paused_until: Optional[str],
        current_exposure_pct: float,
        strategy_counts: Optional[dict] = None,
        confidence_adjustment: int = 0,
        total_open_positions: int = 0,
        btc_trend: str = "UNKNOWN",
        fg_value: int = 50,
    ) -> Tuple[bool, str]:
        if signal.decision == "HOLD":
            return False, "Signal is HOLD"

        # FIX 1: Trend filter — never trade against BTC macro trend
        if btc_trend == "BEAR" and signal.decision == "BUY":
            return False, f"Trend filter: BUY blocked in BTC BEAR trend"
        if btc_trend == "BULL" and signal.decision == "SELL":
            return False, f"Trend filter: SELL blocked in BTC BULL trend"

        # Hard safety limits — always enforced regardless of confidence
        if daily_pnl_pct <= -self._cfg.daily_loss_limit_pct:
            return False, f"Daily loss limit reached: {daily_pnl_pct:.2f}%"

        if paused_until:
            pause_dt = datetime.fromisoformat(paused_until)
            if datetime.now(timezone.utc) < pause_dt:
                return False, f"Loss streak pause active until {paused_until}"

        # FIX 3: Force SCALP only
        strategy = getattr(signal, "strategy", "SCALP")
        if strategy in ("SWING", "POSITION"):
            strategy = "SCALP"
        spec = SIZING.get(strategy, SIZING["SCALP"])

        # FIX 4: SL must be within 1% of entry — always enforced
        if signal.entry and signal.stop_loss:
            sl_pct = abs(signal.entry - signal.stop_loss) / signal.entry * 100
            if sl_pct > 1.0:
                return False, f"SL too wide: {sl_pct:.2f}% > 1% max"

        # Global position limit — always enforced
        max_pos = getattr(self._cfg, "max_simultaneous_positions", 8)
        if total_open_positions >= max_pos:
            return False, f"Max positions reached: {total_open_positions}/{max_pos}"

        # Strategy slot limits — always enforced
        if strategy_counts:
            current_count = strategy_counts.get(strategy, 0)
            if current_count >= spec["max_positions"]:
                return False, (
                    f"{strategy} slot limit: {current_count}/{spec['max_positions']}"
                )

        # Exposure limit — always enforced
        new_trade_pct = self._get_size_pct(signal.confidence, strategy)
        if current_exposure_pct + new_trade_pct > self._cfg.max_total_exposure_pct:
            return False, (
                f"Exposure limit: adding {new_trade_pct}% would exceed "
                f"{self._cfg.max_total_exposure_pct}%"
            )

        # Pre-filter decides WHETHER to call Claude.
        # Claude's decision is final — score never blocks it.
        # Only hard safety limits above (loss limit, SL width, position caps) apply.
        bear_market = fg_value < 25 and btc_trend == "BEAR"
        bear_tag = " [BEAR MODE]" if bear_market else ""
        if signal.confidence >= 65:
            return True, f"OK{bear_tag}"
        # Below 65%: still allow — don't block Claude's output on score alone
        return True, f"OK (low-confidence){bear_tag}"

    def is_partial_candidate(self, signal, confidence_adjustment: int = 0) -> bool:
        """Check if signal qualifies for partial position (below min but close)."""
        strategy = getattr(signal, "strategy", "SCALP")
        spec = SIZING.get(strategy, SIZING["SCALP"])
        effective_min = spec["min_score"] - confidence_adjustment
        partial_min = effective_min - PARTIAL_THRESHOLD_OFFSET
        return partial_min <= signal.confidence < effective_min

    def _get_size_pct(self, confidence: int, strategy: str) -> float:
        spec = SIZING.get(strategy, SIZING["SCALP"])
        for lo, hi, pct in spec["tiers"]:
            if lo <= confidence <= hi:
                return min(pct, MAX_SIZE_PCT)
        return min(spec["tiers"][0][2], MAX_SIZE_PCT)

    def should_pause_after_loss(self, consecutive_losses: int) -> bool:
        return consecutive_losses >= self._cfg.loss_streak_pause_trades

    @staticmethod
    def compute_confidence_adjustment(daily_trades: int, target: int,
                                       hour_utc: int) -> int:
        """Lower threshold if too few trades by midday."""
        adj = 0
        # If past noon UTC and below half target → lower by 5
        if hour_utc >= 12 and daily_trades < target // 2:
            adj = 5
        # If past 18:00 UTC and still below target → lower by 8
        if hour_utc >= 18 and daily_trades < target:
            adj = 8
        return adj
