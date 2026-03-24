import json
import os
from datetime import datetime, timezone

class State:
    def __init__(self, path: str = "state.json", initial_balance: float = 10000.0):
        self._path = path
        self._initial_balance = initial_balance
        self._defaults = {
            "paper_balance": initial_balance,
            "open_positions": {},
            "daily_pnl_pct": 0.0,
            "daily_pnl_reset_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "consecutive_losses": 0,
            "paused_until": None,
            "total_trades": 0,
            "winning_trades": 0,
            "daily_trades_today": 0,
            "confidence_adjustment": 0,
        }
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self._path):
            with open(self._path) as f:
                data = json.load(f)
            for k, v in self._defaults.items():
                data.setdefault(k, v)
            return data
        return dict(self._defaults)

    def save(self):
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    def reset_daily_if_new_day(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._data["daily_pnl_reset_date"] != today:
            self._data["daily_pnl_pct"] = 0.0
            self._data["daily_pnl_reset_date"] = today
            self._data["daily_trades_today"] = 0
            self._data["confidence_adjustment"] = 0
            self.save()

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        try:
            return self._data[key]
        except KeyError:
            raise AttributeError(f"State has no attribute '{key}'")

    def __setattr__(self, key, value):
        if key.startswith("_"):
            super().__setattr__(key, value)
        else:
            self._data[key] = value
