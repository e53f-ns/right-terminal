import json
import os
import pytest
from utils.state import State

def test_state_initializes_fresh(tmp_path):
    s = State(path=str(tmp_path / "state.json"), initial_balance=10000.0)
    assert s.paper_balance == 10000.0
    assert s.open_positions == {}
    assert s.consecutive_losses == 0
    assert s.daily_pnl_pct == 0.0

def test_state_persists_and_reloads(tmp_path):
    path = str(tmp_path / "state.json")
    s = State(path=path, initial_balance=10000.0)
    s.consecutive_losses = 2
    s.save()

    s2 = State(path=path, initial_balance=10000.0)
    assert s2.consecutive_losses == 2

def test_state_resets_daily_pnl_on_new_day(tmp_path):
    path = str(tmp_path / "state.json")
    s = State(path=path, initial_balance=10000.0)
    s.daily_pnl_pct = -5.0
    s.daily_pnl_reset_date = "2020-01-01"
    s.save()

    s2 = State(path=path, initial_balance=10000.0)
    s2.reset_daily_if_new_day()
    assert s2.daily_pnl_pct == 0.0
