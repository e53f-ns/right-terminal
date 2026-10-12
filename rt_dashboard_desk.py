"""F1 DESK snapshot: one read-only JSON for the dashboard's main tab.

Reads only: Upscale GET data (via rt_upscale_actual.fetch, which is cached and GET-only),
logs/closed_trades.jsonl, logs/review_state.json, logs/playbook_review.md and the tail of the agent log.
It sends no orders and changes no files.
"""
import glob
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
TTL = 8
TAIL_BYTES = 400_000
ALIVE_SECONDS = 900  # agent cycles every ~5 min; >15 min of log silence = not alive
REVIEW_EVERY = int(os.getenv("REVIEW_EVERY_TRADES", "10") or 10)
_cache = {"t": 0.0, "v": None}


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except Exception:
        return None


def _jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    return out


def _tail(path, nbytes=TAIL_BYTES):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - nbytes))
            data = fh.read()
        lines = data.decode("utf-8", "replace").splitlines()
        return lines[1:] if size > nbytes else lines
    except Exception:
        return []


def _line_ts(line):
    try:
        return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").timestamp()  # agent logs use local time
    except Exception:
        return None


def _iso_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _agent_log():
    files = glob.glob(str(LOGS / "agent_2026*.log"))
    return max(files, key=os.path.getmtime) if files else None


_TAG = re.compile(r"\]\s+agent:\s+\[([^\]]+)\]\s*(.*)$")


def _feed_from_log(path):
    holds, blocked, rejected, sizing, review = {}, [], [], [], []
    for line in _tail(path):
        m = _TAG.search(line)
        if not m:
            continue
        tag, rest = m.group(1), m.group(2).strip()
        ts = _line_ts(line)
        if ts is None:
            continue
        if tag == "HOLD":
            sym, _, txt = rest.partition(":")
            holds[sym.strip()] = {"ts": ts, "kind": "hold", "sym": sym.strip(), "text": txt.strip()[:170]}
        elif tag == "BLOCKED":
            blocked.append({"ts": ts, "kind": "blocked", "sym": "", "text": rest[:170]})
        elif tag == "REJECTED":
            rejected.append({"ts": ts, "kind": "rejected", "sym": "", "text": rest[:170]})
        elif tag.startswith("SIZING"):
            sym = rest.split(":", 1)[0].strip()
            sizing.append({"ts": ts, "kind": "entry", "sym": sym, "text": rest.partition(":")[2].strip()[:150]})
        elif tag == "REVIEW":
            review.append({"ts": ts, "kind": "review", "sym": "", "text": rest[:170]})
    hs = sorted(holds.values(), key=lambda x: -x["ts"])[:4]
    return hs + blocked[-3:] + rejected[-3:] + sizing[-3:] + review[-2:]


def _closed():
    recs = [r for r in _jsonl(LOGS / "closed_trades.jsonl") if not r.get("technical")]
    recs.sort(key=lambda r: r.get("closed_at") or "")
    return recs


def _short_reason(r):
    t = r.get("setup") or r.get("reason") or r.get("why_now") or ""
    return str(t)[:120]


def _review_info(n_closed):
    done = 0
    try:
        done = int(json.load(open(LOGS / "review_state.json", encoding="utf-8")).get("reviewed_upto", 0))
    except Exception:
        pass
    excerpt = None
    try:
        txt = open(LOGS / "playbook_review.md", encoding="utf-8").read()
        parts = txt.split("\n## ")
        if len(parts) > 1:
            excerpt = ("## " + parts[-1])[:900]
    except Exception:
        pass
    return {"every": REVIEW_EVERY, "reviewed_upto": done, "pending": max(0, n_closed - done),
            "remaining": max(0, REVIEW_EVERY - (n_closed - done)), "latest": excerpt}


def _positions(v):
    out = []
    for p in v.get("positions") or []:
        size = _f(p.get("size")) or 0.0
        stop = take = None
        covered = {"stop": 0.0, "take": 0.0}
        for o in p.get("protective_orders") or []:
            t = o.get("type")
            if t in ("stop", "take"):
                if t == "stop":
                    stop = _f(o.get("trigger_price"))
                else:
                    take = _f(o.get("trigger_price"))
                covered[t] += abs(_f(o.get("size")) or 0.0)
        full = abs(size) > 0 and all(abs(covered[k] - abs(size)) <= max(1e-6, abs(size) * 1e-4) for k in covered)
        mark = _f(p.get("mark"))
        out.append({
            "symbol": p.get("symbol"), "direction": p.get("direction"), "size": abs(size), "entry": _f(p.get("entry")),
            "mark": mark, "upnl": _f(p.get("unrealized_pnl")), "leverage": _f(p.get("leverage")),
            "stop": stop, "take": take, "protected": bool(stop is not None and take is not None and full),
            "dist_stop_pct": round(abs(stop - mark) / mark * 100, 2) if (stop and mark) else None,
            "dist_take_pct": round(abs(take - mark) / mark * 100, 2) if (take and mark) else None,
            "opened_at": p.get("opened_at"), "asset_class": p.get("asset_class"),
        })
    return out


def _build():
    out = {"ok": True, "ts": int(time.time()), "errors": []}
    try:
        import rt_upscale_actual
        v = rt_upscale_actual.fetch()
    except Exception as e:  # noqa: BLE001
        v = {"status": "UNAVAILABLE", "error": type(e).__name__}
    out["venue_status"] = v.get("status")
    if v.get("status") not in ("LIVE",):
        out["errors"].append("Upscale: %s %s" % (v.get("status"), v.get("error") or ""))
    acc = v.get("account") or {}
    eq, free, d0 = _f(acc.get("equity")), _f(acc.get("free_balance")), _f(acc.get("day_start_equity"))
    curve = [[p.get("t"), _f(p.get("equity"))] for p in (v.get("equity_history") or []) if _f(p.get("equity")) is not None]
    out["account"] = {"equity": eq, "free": free, "day_start": d0, "phase": acc.get("phase"),
                      "start": curve[0][1] if curve else None, "start_t": curve[0][0] if curve else None,
                      "day_pnl": round(eq - d0, 2) if (eq is not None and d0 is not None) else None}
    out["unrealized"] = _f(v.get("unrealized_total"))
    pos = _positions(v)
    out["positions"] = pos
    out["protected"] = {"ok": sum(1 for p in pos if p["protected"]), "total": len(pos)}
    out["equity_curve"] = curve[-96:]

    closed = _closed()
    pn = [r["pnl"] for r in closed if isinstance(r.get("pnl"), (int, float))]
    Rs = [r["R"] for r in closed if isinstance(r.get("R"), (int, float))]
    out["closed_stats"] = {"n": len(closed), "wins": sum(1 for x in pn if x > 0), "sum_pnl": round(sum(pn), 2),
                           "avg_R": round(sum(Rs) / len(Rs), 2) if Rs else None,
                           "scope": "реальные сделки Upscale из журнала, без технических микро-тестов"}
    out["closed"] = [{"symbol": r.get("symbol"), "direction": r.get("direction"), "closed_by": r.get("closed_by"),
                      "pnl": r.get("pnl"), "R": r.get("R"), "hold_min": r.get("hold_min"), "why": _short_reason(r),
                      "reason_source": r.get("reason_source"), "closed_at": r.get("closed_at")}
                     for r in reversed(closed[-10:])]
    out["review"] = _review_info(len(closed))

    log = _agent_log()
    age = (time.time() - os.path.getmtime(log)) if log else None
    out["agent"] = {"alive": bool(age is not None and age < ALIVE_SECONDS), "log_age_s": int(age) if age is not None else None}
    feed = _feed_from_log(log) if log else []
    for r in closed[-4:]:
        ts = _iso_ts(r.get("closed_at"))
        if ts:
            how = {"stop": "по стопу", "take": "по тейку"}.get(r.get("closed_by"), "закрыта")
            feed.append({"ts": ts, "kind": "close", "sym": r.get("symbol"),
                         "text": "%s %s, %s, R %s" % ("Лонг" if r.get("direction") == "long" else "Шорт", how,
                                                       "%+.2f" % r["pnl"] if isinstance(r.get("pnl"), (int, float)) else "?", r.get("R"))})
    feed.sort(key=lambda x: -x["ts"])
    out["feed"] = feed[:14]
    return out


_lock = threading.Lock()
_state = {"running": False}


def _refresh():
    try:
        v = _build()
    except Exception as e:  # noqa: BLE001
        v = {"ok": False, "ts": int(time.time()), "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
    _cache.update(t=time.time(), v=v)
    _state["running"] = False


def snapshot(force=False):
    """Never blocks the request: the venue fetch takes ~20 s, so it runs in one background thread and
    the last good snapshot is returned immediately (first call before any data: {"loading": true})."""
    now = time.time()
    if force or now - _cache["t"] >= TTL:
        with _lock:
            if not _state["running"]:
                _state["running"] = True
                threading.Thread(target=_refresh, daemon=True, name="desk-refresh").start()
    v = _cache["v"]
    if v is None:
        return {"ok": False, "loading": True, "ts": int(now)}
    return v
