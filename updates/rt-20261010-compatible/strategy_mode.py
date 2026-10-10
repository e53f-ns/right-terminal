"""Strategy mode switches (ENV-driven, read at call time).

Default (ALLOWED_STRATEGIES unset) == legacy behaviour: SCALP only.

ENV:
  ALLOWED_STRATEGIES          comma list from SCALP,SWING,POSITION (default SCALP)
  MIN_RR                      minimum reward:risk enforced by the brain parser (default 2.0)
  SWING_SCAN_INTERVAL_MINUTES scan interval when SCALP is NOT allowed (default 60)
  SCALP_MAX_HOURS             default 2
  SWING_MAX_HOURS             default 72
  POSITION_MAX_HOURS          default 72
"""
import os

VALID = ("SCALP", "SWING", "POSITION")
DEFAULT = ("SCALP",)


def allowed() -> tuple:
    """Allowed strategies, validated, in canonical order. Falls back to SCALP."""
    raw = os.getenv("ALLOWED_STRATEGIES", "") or ""
    items = {s.strip().upper() for s in raw.split(",") if s.strip()}
    got = tuple(s for s in VALID if s in items)  # unknown names are ignored
    return got or DEFAULT


def is_allowed(name) -> bool:
    return str(name or "").strip().upper() in allowed()


def swing_mode() -> bool:
    """True when anything besides SCALP is allowed (brain gets the swing prompt)."""
    return allowed() != DEFAULT


def min_rr() -> float:
    try:
        v = float(os.getenv("MIN_RR", "2.0"))
    except ValueError:
        return 2.0
    return v if v > 0 else 2.0


def min_sl_pct() -> float:
    """Minimum stop distance (% of entry) in BRAIN_STYLE=smc_flow. A 0.3-0.6% stop on a
    15m zone is inside normal noise; 0 disables. Default 1.0."""
    try:
        v = float(os.getenv("SMC_MIN_SL_PCT", "1.0"))
    except ValueError:
        return 1.0
    return v if v >= 0 else 1.0


def smc_margin_pct() -> float:
    """smc_flow: fixed margin per trade as % of balance (0 = use the risk manager's tiers)."""
    try:
        v = float(os.getenv("SMC_MARGIN_PCT", "0"))
    except ValueError:
        return 0.0
    return v if v > 0 else 0.0


def smc_leverage() -> int:
    """smc_flow: fixed leverage (0 = use the confidence tiers)."""
    try:
        v = int(float(os.getenv("SMC_LEVERAGE", "0")))
    except ValueError:
        return 0
    return v if v > 0 else 0


def max_sl_pct() -> float:
    """Widest stop the risk manager accepts (same env as risk/manager.py)."""
    try:
        return float(os.getenv("MAX_SL_PCT", "1.2"))
    except ValueError:
        return 1.2


def smc_min_confidence() -> int:
    """smc_flow: lowest confidence at which a BUY/SELL is acted on. Claude still decides
    the number itself; this is only a floor. Default 55."""
    try:
        v = int(float(os.getenv("SMC_MIN_CONFIDENCE", "55")))
    except ValueError:
        return 55
    return max(0, min(100, v))


def entry_window_open(now=None) -> bool:
    """smc_flow: is it inside the entry window (same SESSION_START_UTC/SESSION_END_UTC the
    engine uses, default 07-20 UTC = London + New York)? Outside it NEW entries are not
    sent to the brain, so no subscription limit is spent at night. SESSION_FILTER=false
    or a non-smc_flow style always returns True."""
    if not smc_flow():
        return True
    if os.getenv("SESSION_FILTER", "true").lower() == "false":
        return True
    from datetime import datetime, timezone
    h = (now or datetime.now(timezone.utc)).hour
    try:
        a, b = int(os.getenv("SESSION_START_UTC", "7")), int(os.getenv("SESSION_END_UTC", "20"))
    except ValueError:
        a, b = 7, 20
    return a <= h < b


def _hours(var: str, default: float) -> float:
    try:
        v = float(os.getenv(var, str(default)))
    except ValueError:
        return float(default)
    return v if v > 0 else float(default)


def max_hours(strategy: str) -> float:
    """Time-stop limit in hours for a position of the given strategy."""
    s = str(strategy or "").upper()
    if s == "SCALP":
        return _hours("SCALP_MAX_HOURS", 2)
    if s == "SWING":
        return _hours("SWING_MAX_HOURS", 72)
    if s == "POSITION":
        return _hours("POSITION_MAX_HOURS", 72)
    return 72.0


ORPHAN_BASE_HOURS = 48
ORPHAN_GRACE_HOURS = 24


def orphan_max_age_hours(strategy: str) -> float:
    """Age after which a position is considered an orphan. Always strictly
    later than its time stop for SWING/POSITION so legit holds survive;
    SCALP / unknown keep the legacy 48h."""
    s = str(strategy or "").upper()
    if s in ("SWING", "POSITION"):
        return max(ORPHAN_BASE_HOURS, max_hours(s) + ORPHAN_GRACE_HOURS)
    return float(ORPHAN_BASE_HOURS)


def swing_scan_interval_minutes() -> int:
    try:
        v = int(float(os.getenv("SWING_SCAN_INTERVAL_MINUTES", "60")))
    except ValueError:
        return 60
    return v if v > 0 else 60


def scan_interval_override():
    """Minutes to use instead of the 5/10/15 scalp cadence, or None."""
    if is_allowed("SCALP"):
        return None
    return swing_scan_interval_minutes()


# ── BRAIN_STYLE=smc_flow (opt-in; default == legacy behaviour) ───────────────
#   BRAIN_STYLE              "" (default) | smc_flow
#   SMC_SWEEP_LOOKBACK       candles (default 12): sweep counts as a pre-filter reason
#   SMC_ZONE_PROX_PCT        % (default 0.4): price this close to an unmitigated FVG/OB
#   SMC_EVENT_LOOKBACK       candles (default 6): BOS/CHOCH counts as "fresh"
#   SMC_LEVEL_TOUCH_PCT      % (default 0.15): price this close to PDH/PDL/EQH/EQL
#   SMC_FUNDING_EXTREME      abs funding rate treated as extreme (default 0.0005)
def brain_style() -> str:
    return (os.getenv("BRAIN_STYLE", "") or "").strip().lower()


def smc_flow() -> bool:
    return brain_style() == "smc_flow"


def multi_strategy() -> bool:
    """Brain may choose its own strategy: swing mode OR smc_flow."""
    return swing_mode() or smc_flow()


def _fenv(var: str, default: float) -> float:
    try:
        v = float(os.getenv(var, str(default)))
    except ValueError:
        return float(default)
    return v if v > 0 else float(default)


def smc_sweep_lookback() -> int:
    return int(_fenv("SMC_SWEEP_LOOKBACK", 12))


def smc_zone_prox_pct() -> float:
    return _fenv("SMC_ZONE_PROX_PCT", 0.4)


def smc_event_lookback() -> int:
    return int(_fenv("SMC_EVENT_LOOKBACK", 6))


def smc_level_touch_pct() -> float:
    return _fenv("SMC_LEVEL_TOUCH_PCT", 0.15)


def smc_funding_extreme() -> float:
    return _fenv("SMC_FUNDING_EXTREME", 0.0005)


# ── smc_flow for stocks / forex (opt-in; only read when BRAIN_STYLE=smc_flow) ──
#   SMC_STOCK_SCAN_MIN        minutes between stock scans (default 30)
#   SMC_FOREX_SCAN_MIN        minutes between forex scans (default 30)
#   SMC_STOCK_MAX_LEVERAGE    leverage ceiling for stocks (default 2)
#   SMC_FOREX_MAX_LEVERAGE    leverage ceiling for forex (default 5)
#   SMC_FOREX_MIN_SL_PCT      minimum stop distance for forex, % of entry (default 0.25)
def _fenv0(var: str, default: float) -> float:
    """Like _fenv but 0 is a valid value (disables)."""
    try:
        v = float(os.getenv(var, str(default)))
    except ValueError:
        return float(default)
    return v if v >= 0 else float(default)


def smc_stock_scan_min() -> int:
    return max(1, int(_fenv("SMC_STOCK_SCAN_MIN", 30)))


def smc_forex_scan_min() -> int:
    return max(1, int(_fenv("SMC_FOREX_SCAN_MIN", 30)))


def smc_stock_max_leverage() -> int:
    return max(1, int(_fenv("SMC_STOCK_MAX_LEVERAGE", 2)))


def smc_forex_max_leverage() -> int:
    return max(1, int(_fenv("SMC_FOREX_MAX_LEVERAGE", 5)))


def smc_forex_min_sl_pct() -> float:
    return _fenv0("SMC_FOREX_MIN_SL_PCT", 0.25)


def min_sl_pct_for(asset_class=None) -> float:
    """Minimum stop distance for the asset class: forex has its own floor, everything else
    (crypto, stocks, unknown) keeps SMC_MIN_SL_PCT."""
    if str(asset_class or "").lower() == "forex":
        return smc_forex_min_sl_pct()
    return min_sl_pct()
