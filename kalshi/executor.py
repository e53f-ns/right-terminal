"""
Kalshi executor — paper mode with target/stop exit logic.

Positions carry target_price and stop_price set at entry.
monitor_positions() checks live yes_bid each scan and closes when hit.
"""
import json
import logging
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

MAX_STAKE_DOLLARS  = 50.0   # max $ per trade
MAX_OPEN_POSITIONS = 5      # never hold more than 5 contracts at once
DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "positions.json")


@dataclass
class KalshiPosition:
    ticker:       str
    subtitle:     str
    side:         str = "yes"
    entry_price:  float = 0.0    # $/share paid (yes_ask at entry)
    shares:       float = 0.0
    stake:        float = 0.0    # total $ committed
    strike:       float = 0.0
    safety_margin: float = 0.0
    target_price: float = 0.0   # yes_bid to take profit
    stop_price:   float = 0.0   # yes_bid to stop out
    hours_at_open: float = 0.0
    close_time:   str = ""
    opened_at:    str = ""


@dataclass
class KalshiRecord:
    ticker:      str
    subtitle:    str
    entry_price: float
    exit_price:  float
    shares:      float
    stake:       float
    pnl:         float
    result:      str    # TARGET / STOP / EXPIRED_WIN / EXPIRED_LOSS
    closed_at:   str


class KalshiExecutor:
    def __init__(self, initial_balance: float = 1000.0, paper: bool = True, api_key: str = ""):
        self.balance    = initial_balance
        self.paper      = paper
        self._api_key   = api_key
        self.positions: dict = {}
        self.history:   list = []
        os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
        self._load()

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def staked(self) -> float:
        return sum(p.stake for p in self.positions.values())

    @property
    def total_pnl(self) -> float:
        return sum(r.pnl for r in self.history)

    @property
    def win_rate(self) -> float:
        settled = [r for r in self.history if r.result in ("TARGET", "EXPIRED_WIN")]
        if not self.history:
            return 0.0
        return round(len(settled) / len(self.history) * 100, 1)

    # ── Entry ───────────────────────────────────────────────────────────────────

    def open(self, candidate, close_time: str = "") -> Optional[dict]:
        """Open a position from a Candidate. Returns fill dict or None."""
        from kalshi.screener import Candidate

        if len(self.positions) >= MAX_OPEN_POSITIONS:
            logger.debug("max positions reached (%d)", MAX_OPEN_POSITIONS)
            return None
        if candidate.ticker in self.positions:
            logger.debug("already open: %s", candidate.ticker)
            return None

        stake  = min(MAX_STAKE_DOLLARS, max(0.0, self.balance - self.staked))
        if stake < 1.0:
            logger.debug("insufficient balance %.2f", self.balance)
            return None

        entry  = candidate.yes_ask
        shares = round(stake / entry, 2)

        self.balance -= stake
        self.positions[candidate.ticker] = KalshiPosition(
            ticker         = candidate.ticker,
            subtitle       = candidate.subtitle,
            side           = "yes",
            entry_price    = entry,
            shares         = shares,
            stake          = stake,
            strike         = candidate.strike,
            safety_margin  = candidate.safety_margin,
            target_price   = candidate.target_price,
            stop_price     = candidate.stop_price,
            hours_at_open  = candidate.hours_left,
            close_time     = close_time,
            opened_at      = datetime.now(timezone.utc).isoformat(),
        )
        self._save()
        logger.info(
            "OPEN YES | %s | entry=%.2f target=%.2f stop=%.2f | "
            "$%.2f stake | %.0f shares | margin=%.1f%%",
            candidate.subtitle[:35], entry, candidate.target_price,
            candidate.stop_price, stake, shares, candidate.safety_margin * 100,
        )
        return {"status": "filled", "ticker": candidate.ticker,
                "entry": entry, "stake": stake, "shares": shares}

    # ── Exit ────────────────────────────────────────────────────────────────────

    def close(self, ticker: str, exit_price: float, reason: str) -> Optional[KalshiRecord]:
        """Close an open position at exit_price."""
        if ticker not in self.positions:
            return None
        pos    = self.positions.pop(ticker)
        payout = pos.shares * exit_price
        pnl    = round(payout - pos.stake, 2)

        if self.paper:
            self.balance += payout

        record = KalshiRecord(
            ticker      = ticker,
            subtitle    = pos.subtitle,
            entry_price = pos.entry_price,
            exit_price  = exit_price,
            shares      = pos.shares,
            stake       = pos.stake,
            pnl         = pnl,
            result      = reason,
            closed_at   = datetime.now(timezone.utc).isoformat(),
        )
        self.history.append(record)
        self._save()

        icon = "✅" if pnl > 0 else "❌"
        logger.info(
            "%s %s | %s | entry=%.2f exit=%.2f | P&L=%+.2f | bal=%.2f",
            icon, reason, pos.subtitle[:35],
            pos.entry_price, exit_price, pnl, self.balance,
        )
        return record

    def check_expirations(self, fetcher) -> list:
        """Settle positions whose close_time has passed via Kalshi API."""
        import requests
        settled = []
        now     = datetime.now(timezone.utc)

        for ticker, pos in list(self.positions.items()):
            if not pos.close_time:
                continue
            try:
                close_dt = datetime.fromisoformat(pos.close_time.replace("Z", "+00:00"))
                if now < close_dt:
                    continue
            except (ValueError, TypeError):
                continue

            try:
                r = requests.get(
                    f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}",
                    timeout=8,
                )
                result = r.json().get("market", {}).get("result", "")
                if result == "yes":
                    record = self.close(ticker, 1.0, "EXPIRED_WIN")
                elif result == "no":
                    record = self.close(ticker, 0.0, "EXPIRED_LOSS")
                else:
                    continue
                if record:
                    settled.append(record)
            except Exception as e:
                logger.warning("expiry check failed %s: %s", ticker, e)

        return settled

    # ── Status ──────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "balance":        round(self.balance, 2),
            "staked":         round(self.staked, 2),
            "open_positions": len(self.positions),
            "total_pnl":      round(self.total_pnl, 2),
            "trades":         len(self.history),
            "win_rate_pct":   self.win_rate,
            "paper":          self.paper,
        }

    def print_positions(self):
        if not self.positions:
            return
        for tk, pos in self.positions.items():
            logger.info(
                "  OPEN | %s | entry=%.2f tgt=%.2f stop=%.2f | "
                "$%.2f | margin=%.1f%%",
                pos.subtitle[:35], pos.entry_price,
                pos.target_price, pos.stop_price,
                pos.stake, pos.safety_margin * 100,
            )

    # ── Persistence ────────────────────────────────────────────────────────────

    def _save(self):
        try:
            with open(DATA_PATH, "w") as f:
                json.dump({
                    "balance":   self.balance,
                    "positions": {k: asdict(v) for k, v in self.positions.items()},
                    "history":   [asdict(r) for r in self.history],
                }, f, indent=2)
        except Exception as e:
            logger.warning("save failed: %s", e)

    def _load(self):
        if not os.path.exists(DATA_PATH):
            return
        try:
            with open(DATA_PATH) as f:
                data = json.load(f)
            self.balance = float(data.get("balance", self.balance))
            for k, v in data.get("positions", {}).items():
                self.positions[k] = KalshiPosition(**v)
            for r in data.get("history", []):
                self.history.append(KalshiRecord(**r))
            logger.info("Loaded: bal=%.2f pos=%d hist=%d",
                        self.balance, len(self.positions), len(self.history))
        except Exception as e:
            logger.warning("load failed: %s", e)
