"""
Polymarket executor — paper mode with target/stop exit logic.
Mirrors kalshi/executor.py pattern for consistency.
"""
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

MAX_STAKE_DOLLARS = 10.0
MAX_OPEN_POSITIONS = 3
DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "positions.json")


@dataclass
class PolyPosition:
    slug: str
    side: str           # "up" or "down"
    token_id: str
    entry_price: float
    shares: float
    stake: float
    btc_at_entry: float
    target_price: float
    stop_price: float
    close_ts: int       # market close unix timestamp
    opened_at: str = ""


@dataclass
class PolyRecord:
    slug: str
    side: str
    entry_price: float
    exit_price: float
    shares: float
    stake: float
    pnl: float
    result: str         # TARGET / STOP / EXPIRED
    closed_at: str = ""


class PolymarketExecutor:
    def __init__(self, initial_balance: float = 100.0, paper: bool = True):
        self.balance = initial_balance
        self.paper = paper
        self.positions: dict = {}   # {pos_key: PolyPosition}
        self.history: list = []     # [PolyRecord, ...]
        os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
        self._load()

    @property
    def staked(self) -> float:
        return sum(p.stake for p in self.positions.values())

    @property
    def total_pnl(self) -> float:
        return sum(r.pnl for r in self.history)

    @property
    def win_rate(self) -> float:
        wins = [r for r in self.history if r.result == "TARGET"]
        if not self.history:
            return 0.0
        return round(len(wins) / len(self.history) * 100, 1)

    def open(self, candidate, close_ts: int = 0) -> Optional[dict]:
        """Open a position from a PolyCandidate."""
        from polymarket.screener import PolyCandidate

        pos_key = f"{candidate.slug}:{candidate.side}"

        if len(self.positions) >= MAX_OPEN_POSITIONS:
            logger.debug("Poly: max positions reached (%d)", MAX_OPEN_POSITIONS)
            return None
        if pos_key in self.positions:
            logger.debug("Poly: already open: %s", pos_key)
            return None

        stake = min(MAX_STAKE_DOLLARS, max(0.0, self.balance - self.staked))
        if stake < 0.50:
            logger.debug("Poly: insufficient balance %.2f", self.balance)
            return None

        entry = candidate.ask_price
        shares = round(stake / entry, 2)

        self.balance -= stake
        self.positions[pos_key] = PolyPosition(
            slug=candidate.slug,
            side=candidate.side,
            token_id=candidate.token_id,
            entry_price=entry,
            shares=shares,
            stake=stake,
            btc_at_entry=candidate.btc_price,
            target_price=candidate.target_price,
            stop_price=candidate.stop_price,
            close_ts=close_ts,
            opened_at=datetime.now(timezone.utc).isoformat(),
        )
        self._save()
        logger.info(
            "POLY OPEN %s %s | entry=%.3f tgt=%.3f stop=%.3f | "
            "$%.2f stake | %.0f shares",
            candidate.side.upper(), candidate.slug[-10:],
            entry, candidate.target_price, candidate.stop_price,
            stake, shares,
        )
        return {"status": "filled", "pos_key": pos_key,
                "entry": entry, "stake": stake, "shares": shares}

    def close(self, pos_key: str, exit_price: float, reason: str) -> Optional[PolyRecord]:
        """Close an open position."""
        if pos_key not in self.positions:
            return None
        pos = self.positions.pop(pos_key)
        payout = pos.shares * exit_price
        pnl = round(payout - pos.stake, 4)

        if self.paper:
            self.balance += payout

        record = PolyRecord(
            slug=pos.slug,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            shares=pos.shares,
            stake=pos.stake,
            pnl=pnl,
            result=reason,
            closed_at=datetime.now(timezone.utc).isoformat(),
        )
        self.history.append(record)
        self._save()

        icon = "+" if pnl > 0 else ""
        logger.info(
            "POLY CLOSE %s | %s %s | entry=%.3f exit=%.3f | P&L=%s%.4f | bal=%.2f",
            reason, pos.side.upper(), pos.slug[-10:],
            pos.entry_price, exit_price, icon, pnl, self.balance,
        )
        return record

    def check_expirations(self) -> list:
        """Close positions whose market has expired."""
        settled = []
        now = int(datetime.now(timezone.utc).timestamp())

        for pos_key, pos in list(self.positions.items()):
            if pos.close_ts and now > pos.close_ts + 60:
                # Market expired — treat as loss (we didn't exit in time)
                record = self.close(pos_key, 0.0, "EXPIRED")
                if record:
                    settled.append(record)

        return settled

    def status(self) -> dict:
        return {
            "balance": round(self.balance, 2),
            "staked": round(self.staked, 2),
            "open_positions": len(self.positions),
            "total_pnl": round(self.total_pnl, 4),
            "trades": len(self.history),
            "win_rate_pct": self.win_rate,
            "paper": self.paper,
        }

    def print_positions(self):
        if not self.positions:
            return
        for pk, pos in self.positions.items():
            logger.info(
                "  POLY OPEN | %s %s | entry=%.3f tgt=%.3f stop=%.3f | $%.2f",
                pos.side.upper(), pos.slug[-10:],
                pos.entry_price, pos.target_price, pos.stop_price, pos.stake,
            )

    def _save(self):
        try:
            with open(DATA_PATH, "w") as f:
                json.dump({
                    "balance": self.balance,
                    "positions": {k: asdict(v) for k, v in self.positions.items()},
                    "history": [asdict(r) for r in self.history],
                }, f, indent=2)
        except Exception as e:
            logger.warning("Poly save failed: %s", e)

    def _load(self):
        if not os.path.exists(DATA_PATH):
            return
        try:
            with open(DATA_PATH) as f:
                data = json.load(f)
            self.balance = float(data.get("balance", self.balance))
            for k, v in data.get("positions", {}).items():
                self.positions[k] = PolyPosition(**v)
            for r in data.get("history", []):
                self.history.append(PolyRecord(**r))
            logger.info("Poly loaded: bal=%.2f pos=%d hist=%d",
                        self.balance, len(self.positions), len(self.history))
        except Exception as e:
            logger.warning("Poly load failed: %s", e)
