"""Macro event calendar (FOMC / CPI) and the news blackout window.

DATA CAVEAT: data/macro_calendar.json was built for the 2020-2026 backtest from FOMC/BLS
pages; the dates are NOT independently verified. Treat it as a guard rail, not as truth,
and re-check upcoming dates before relying on it. Missing/unreadable file -> no events
(blackout never fires), and a warning is logged once.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_calendar.json")
_cache: dict = {"path": None, "mtime": None, "events": []}
_warned = False
_status = {"status": "unknown", "loaded_at": None, "error": None}


def _path() -> str:
    return os.getenv("MACRO_CALENDAR_FILE") or _DEFAULT


def events() -> List[Tuple[str, int]]:
    """[(name, unix_ts)] sorted by time. Reloaded when the file changes."""
    global _warned
    p = _path()
    try:
        mt = os.path.getmtime(p)
        if _cache["path"] == p and _cache["mtime"] == mt:
            _status.update(status="available_unverified", error=None)
            return list(_cache["events"])
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        ev = sorted(((str(n), int(t)) for n, t in raw), key=lambda x: x[1])
        _cache.update(path=p, mtime=mt, events=ev)
        _status.update(status="available_unverified", loaded_at=time.time(), error=None)
        return ev
    except Exception as e:
        _status.update(status="unknown", error=type(e).__name__)
        if not _warned:
            logger.warning("macro calendar unavailable (%s): %s", p, str(e)[:120])
            _warned = True
        return []


def upcoming(now: Optional[float] = None, hours: float = 48.0) -> List[Tuple[str, int]]:
    now = time.time() if now is None else now
    return [(n, t) for n, t in events() if now <= t <= now + hours * 3600]


def blackout(now: Optional[float], hours_before: float, hours_after: float) -> Optional[Tuple[str, int]]:
    """The event whose window [t - before, t + after] contains `now`, else None.
    Both edges are inclusive. Zero/negative window on both sides disables the filter."""
    if hours_before <= 0 and hours_after <= 0:
        return None
    now = time.time() if now is None else now
    for n, t in events():
        if t - max(hours_before, 0) * 3600 <= now <= t + max(hours_after, 0) * 3600:
            return (n, t)
    return None


def calendar_snapshot() -> dict:
    """Availability is not completeness/freshness/verification of macro dates."""
    ev = events()
    return {**_status, "path": _path(), "source": "local_file",
            "source_url": None, "verified": False, "events": ev}


def blackout_status(now: Optional[float], hours_before: float, hours_after: float) -> dict:
    """Typed alternative; legacy blackout(None) remains ambiguous on missing file."""
    snap = calendar_snapshot()
    if hours_before <= 0 and hours_after <= 0:
        return {"status": "disabled", "calendar_status": snap["status"], "event": None}
    if snap["status"] == "unknown":
        return {"status": "unknown", "calendar_status": "unknown", "event": None}
    now = time.time() if now is None else now
    event = next(((n, t) for n, t in snap["events"]
                  if t - max(hours_before, 0) * 3600 <= now <= t + max(hours_after, 0) * 3600), None)
    return {"status": "active" if event else "none_in_unverified_calendar",
            "calendar_status": snap["status"], "event": event}


def prompt_block(now: Optional[float] = None, hours: float = 48.0) -> str:
    now = time.time() if now is None else now
    snap = calendar_snapshot()
    if snap["status"] == "unknown":
        return "MACRO status=unknown source=local_file source_url=null; unavailable is not no events; do not invent dates"
    parts = [f"{n} at {datetime.fromtimestamp(t, timezone.utc).isoformat()} in {(t-now)/3600:.1f}h"
             for n, t in snap["events"] if now <= t <= now + hours * 3600][:4]
    return ("MACRO status=available_unverified source=local_file source_url=null verified=false; "
            + (", ".join(parts) or "no events in supplied window (coverage unknown)")
            + "; dates not independently verified; do not invent dates")
