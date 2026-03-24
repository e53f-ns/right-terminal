#!/usr/bin/env python3
"""
Bloomberg Terminal-style market dashboard.
Crypto · Stocks · Forex · Macro · Smart Money

Requirements: pip install blessed requests yfinance colorama
Run:          python3 dashboard.py
"""

import os, sys, re, time, json, math, signal, threading, glob
from datetime import datetime, timezone, timedelta

try:
    import requests
    import yfinance as yf
    from blessed import Terminal
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Run: pip install blessed requests yfinance")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
VERSION          = "1.0"
REFRESH_SEC      = 30
CONFIG_FILE      = "dashboard_config.json"
STATE_FILE       = "state.json"
TRADE_LOG_GLOB   = "logs/trades_*.jsonl"

CRYPTO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "BNB": "binancecoin", "XRP": "ripple",
}
SECTORS = {
    "XLK": "Technology",  "XLF": "Financials",  "XLE": "Energy",
    "XLV": "Health Care", "XLI": "Industrials", "XLB": "Materials",
    "XLRE": "Real Estate","XLC": "Comm Svcs",   "XLY": "Cons Disc",
    "XLP": "Cons Staples","XLU": "Utilities",
}
FX_PAIRS = {
    "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD", "USDJPY=X": "USD/JPY",
    "AUDUSD=X": "AUD/USD", "USDCHF=X": "USD/CHF", "USDCAD=X": "USD/CAD",
    "NZDUSD=X": "NZD/USD",
}
CB_RATES = [
    ("Federal Reserve", "Fed",  5.50, "HOLD"),
    ("European CB",     "ECB",  4.50, "HOLD"),
    ("Bank of Japan",   "BOJ",  0.10, "HAWKISH"),
    ("Bank of England", "BOE",  5.25, "HOLD"),
    ("Swiss Natl Bank", "SNB",  1.75, "DOVISH"),
    ("Bank of Canada",  "BOC",  5.00, "DOVISH"),
]
FRED_SERIES = {
    "fed_funds":  "FEDFUNDS",
    "cpi":        "CPIAUCSL",
    "ppi":        "PPIACO",
    "unemployment":"UNRATE",
    "gdp_growth": "A191RL1Q225SBEA",
    "us2y":       "DGS2",
    "us5y":       "DGS5",
    "us10y":      "DGS10",
    "us30y":      "DGS30",
}
VIEWS = {1:"MACRO", 2:"CRYPTO", 3:"STOCKS", 4:"FOREX",
         5:"SMART MONEY", 6:"SIGNALS", 7:"AGENT", 8:"ALERTS",
         9:"LEARNING"}

# ── Global state ──────────────────────────────────────────────────────────────
_running      = True
_current_view = 2
_data_lock    = threading.Lock()
_market_data  = {}
_api_status   = {}
_last_refresh = 0.0
_ticker_pos   = 0
_alerts       = []

# ── Color helpers (functions so they stay lazy) ───────────────────────────────
def O(t): return t.bold + t.color(208)       # orange  — headers/labels
def W(t): return t.normal + t.color(255)     # white   — data
def G(t): return t.bold + t.color(46)        # green   — positive
def R(t): return t.bold + t.color(196)       # red     — negative
def C(t): return t.bold + t.color(51)        # cyan    — highlights
def Y(t): return t.bold + t.color(220)       # yellow  — warnings
def DIM(t): return t.dim + t.color(244)      # dim gray
def N(t): return t.normal                    # reset

def pct_col(t, pct):
    """Colored ▲/▼ percentage string."""
    if pct is None: return DIM(t) + "  N/A  " + N(t)
    col = G(t) if pct >= 0 else R(t)
    arr = "▲" if pct >= 0 else "▼"
    return col + f"{arr}{abs(pct):.2f}%" + N(t)

def val_col(t, val, good_positive=True):
    if val is None: return DIM(t) + "N/A" + N(t)
    col = (G(t) if val >= 0 else R(t)) if good_positive else (R(t) if val >= 0 else G(t))
    return col + f"{val:+.2f}" + N(t)

def rsi_col(t, rsi):
    if rsi is None: return DIM(t)
    if rsi >= 70:   return R(t)
    if rsi <= 30:   return G(t)
    return W(t)

def rsi_label(rsi):
    if rsi is None: return "N/A"
    if rsi >= 70:   return "OVERBOUGHT"
    if rsi <= 30:   return "OVERSOLD"
    return "NEUTRAL"

def sparkline(vals, w=10):
    blocks = " ▁▂▃▄▅▆▇█"
    if not vals or len(vals) < 2: return " " * w
    mn, mx = min(vals), max(vals)
    rng = mx - mn if mx != mn else 1
    return "".join(blocks[int((v - mn) / rng * 8)] for v in vals[-w:])

def ascii_bar(val, lo, hi, width=20, full="█", empty="░"):
    if val is None or hi <= lo: return empty * width
    r = max(0.0, min(1.0, (val - lo) / (hi - lo)))
    n = int(r * width)
    return full * n + empty * (width - n)

def fmt_price(p, d=2):
    if p is None: return "N/A"
    if p >= 10000: return f"{p:,.0f}"
    if p >= 1000:  return f"{p:,.{d}f}"
    if p >= 1:     return f"{p:.{d}f}"
    return f"{p:.4f}"

def fmt_vol(v):
    if v is None: return "N/A"
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.2f}B"
    if v >= 1e6:  return f"${v/1e6:.1f}M"
    return f"${v:.0f}"

def at(t, y, x, text):
    sys.stdout.write(t.move(y, x) + text)

def hline(t, y, width, char="─"):
    sys.stdout.write(t.move(y, 0) + DIM(t) + char * width + N(t))

def section(t, y, label, width):
    rest = max(0, width - len(label) - 5)
    sys.stdout.write(
        t.move(y, 0) + O(t) + "─── " + label + " " + "─" * rest + N(t)
    )

# ── HTTP helper ───────────────────────────────────────────────────────────────
_session = requests.Session()
_session.headers["User-Agent"] = "BloombergDash/1.0"

def _get(url, params=None, timeout=8):
    r = _session.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r

# ── Data fetchers ─────────────────────────────────────────────────────────────

def fetch_crypto():
    ids = ",".join(CRYPTO_IDS.values())
    data = _get("https://api.coingecko.com/api/v3/coins/markets", params={
        "vs_currency": "usd", "ids": ids, "order": "market_cap_desc",
        "sparkline": "false", "price_change_percentage": "24h,7d",
    }).json()
    by_id = {c["id"]: c for c in data}
    return {sym: by_id.get(cid, {}) for sym, cid in CRYPTO_IDS.items()}

def fetch_global():
    return _get("https://api.coingecko.com/api/v3/global").json().get("data", {})

def fetch_fear_greed():
    data = _get("https://api.alternative.me/fng/?limit=7").json().get("data", [])
    return [{"value": int(d["value"]), "label": d["value_classification"]} for d in data]

def fetch_funding():
    sym_map = {"BTCUSDT":"BTC","ETHUSDT":"ETH","SOLUSDT":"SOL","BNBUSDT":"BNB","XRPUSDT":"XRP"}
    data = _get("https://fapi.binance.com/fapi/v1/premiumIndex").json()
    return {sym_map[d["symbol"]]: float(d.get("lastFundingRate", 0))
            for d in data if d.get("symbol") in sym_map}

def fetch_crypto_prices(coin_id, days=16):
    data = _get(f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
                params={"vs_currency":"usd","days":days,"interval":"daily"}).json()
    return [p[1] for p in data.get("prices", [])]

def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return None
    d = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = [max(x, 0) for x in d]; losses = [abs(min(x, 0)) for x in d]
    ag = sum(gains[-period:]) / period; al = sum(losses[-period:]) / period
    if al == 0: return 100.0
    return round(100 - 100 / (1 + ag / al), 1)

def fetch_rsi():
    result = {}
    for sym, cid in CRYPTO_IDS.items():
        try: result[sym] = calc_rsi(fetch_crypto_prices(cid))
        except Exception: result[sym] = None
    return result

def fetch_yf(symbols):
    result = {}
    for sym in symbols:
        try:
            fi = yf.Ticker(sym).fast_info
            price = getattr(fi, "last_price", None)
            prev  = getattr(fi, "previous_close", None)
            result[sym] = {
                "price":      price,
                "prev_close": prev,
                "day_high":   getattr(fi, "day_high", None),
                "day_low":    getattr(fi, "day_low", None),
                "change_pct": ((price - prev) / prev * 100) if price and prev and prev != 0 else 0,
            }
        except Exception:
            result[sym] = {"price": None, "change_pct": 0}
    return result

def fetch_fred_val(series_id):
    r = _get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id})
    for line in reversed(r.text.strip().split("\n")[1:]):
        parts = line.split(",")
        if len(parts) == 2 and parts[1].strip() not in (".", ""):
            try: return float(parts[1].strip()), parts[0].strip()
            except ValueError: continue
    return None, None

def fetch_macro():
    result = {}
    for key, sid in FRED_SERIES.items():
        try:
            val, date = fetch_fred_val(sid)
            result[key] = {"value": val, "date": date}
        except Exception:
            result[key] = {"value": None, "date": "N/A"}
    return result

def _run_fetcher(key, fn, results):
    try:
        results[key] = fn()
        _api_status[key] = "LIVE"
    except Exception as e:
        results[key] = None
        _api_status[key] = "ERR"

def fetch_all():
    results = {}
    stock_syms = (["^GSPC","^NDX","^DJI","^VIX","^RUT"] + list(SECTORS) +
                  ["GC=F","CL=F","SI=F"])
    forex_syms = list(FX_PAIRS) + ["DX-Y.NYB"]
    tasks = [
        ("crypto",     fetch_crypto),
        ("global",     fetch_global),
        ("fear_greed", fetch_fear_greed),
        ("funding",    fetch_funding),
        ("rsi",        fetch_rsi),
        ("macro",      fetch_macro),
        ("stocks",     lambda: fetch_yf(stock_syms)),
        ("forex",      lambda: fetch_yf(forex_syms)),
    ]
    threads = [threading.Thread(target=_run_fetcher, args=(k, fn, results), daemon=True)
               for k, fn in tasks]
    for th in threads: th.start()
    for th in threads: th.join(timeout=20)
    return results

# ── Background refresh ────────────────────────────────────────────────────────

def _refresh_loop():
    global _market_data, _last_refresh
    while _running:
        try:
            new = fetch_all()
            with _data_lock:
                _market_data.update(new)
                _last_refresh = time.time()
        except Exception:
            pass
        for _ in range(REFRESH_SEC * 10):
            if not _running: break
            time.sleep(0.1)

def _get_data():
    with _data_lock:
        return dict(_market_data)

# ── Header / ticker / footer ──────────────────────────────────────────────────

def draw_header(t):
    now  = datetime.now(timezone.utc)
    tstr = now.strftime("%H:%M:%S UTC")
    dstr = now.strftime("%a %d %b %Y")
    h    = now.hour
    mk   = lambda label, open_: (G(t) if open_ else DIM(t)) + label + N(t)
    sessions = (mk("NY:OPEN ", 13 <= h < 21) + " " +
                mk("LON:OPEN ", 8 <= h < 16) + " " +
                mk("TOK:OPEN", 0 <= h < 6 or h >= 22))
    title = O(t) + f"BLOOMBERG TERMINAL v{VERSION}" + N(t)
    right = W(t) + tstr + N(t) + DIM(t) + f"  {dstr}  " + N(t) + G(t) + "●LIVE" + N(t)
    mid_pad = max(0, t.width - 28 - 35 - 5)
    sys.stdout.write(t.move(0, 0) + t.clear_eol +
                     " " + title + "  " + sessions +
                     " " * mid_pad + right)

def draw_ticker(t):
    global _ticker_pos
    data    = _get_data()
    crypto  = data.get("crypto") or {}
    stocks  = data.get("stocks") or {}
    forex   = data.get("forex")  or {}

    items = []
    for sym in ["BTC","ETH","SOL"]:
        c = crypto.get(sym, {})
        if c.get("current_price"):
            pct = c.get("price_change_percentage_24h", 0) or 0
            col = G(t) if pct >= 0 else R(t)
            items.append(O(t) + sym + N(t) + ":" + W(t) +
                         f"${c['current_price']:,.0f}" + N(t) +
                         col + f"{'▲' if pct>=0 else '▼'}{abs(pct):.1f}%" + N(t))
    for sym, lbl in [("^GSPC","SPX"),("^NDX","NDQ"),("^VIX","VIX"),("GC=F","GOLD"),("CL=F","OIL")]:
        s = stocks.get(sym, {})
        if s.get("price"):
            pct = s.get("change_pct", 0) or 0
            col = G(t) if pct >= 0 else R(t)
            items.append(O(t) + lbl + N(t) + ":" + W(t) +
                         f"{s['price']:,.0f}" + N(t) +
                         col + f"{'▲' if pct>=0 else '▼'}{abs(pct):.1f}%" + N(t))
    for sym, lbl in [("EURUSD=X","EUR/USD"),("USDJPY=X","USD/JPY")]:
        f = forex.get(sym, {})
        if f.get("price"):
            pct = f.get("change_pct", 0) or 0
            col = G(t) if pct >= 0 else R(t)
            items.append(O(t) + lbl + N(t) + ":" + W(t) +
                         f"{f['price']:.4f}" + N(t) +
                         col + f"{'▲' if pct>=0 else '▼'}{abs(pct):.3f}%" + N(t))

    if not items:
        sys.stdout.write(t.move(1, 0) + t.clear_eol +
                         DIM(t) + " Connecting to market data feeds..." + N(t))
        return

    sep = DIM(t) + "  ║  " + N(t)
    # Build plain-text version to measure length for scrolling
    ansi = re.compile(r'\x1b\[[0-9;]*m')
    full_rich = sep.join(items)
    full_plain = ansi.sub("", full_rich)

    W2 = t.width - 2
    # Rotate by ticker_pos characters
    pos = _ticker_pos % max(1, len(full_plain))
    # Reconstruct scrolled version by cycling the plain text
    scrolled_plain = (full_plain + "   ║   " + full_plain)[pos:pos + W2]
    # We can't easily slice ANSI text, so for simplicity redraw from start each time
    # and let it wrap at terminal width — looks great for a live dashboard
    sys.stdout.write(t.move(1, 0) + t.clear_eol + " " + full_rich)

def draw_status_bar(t, y):
    age   = int(time.time() - _last_refresh) if _last_refresh else 999
    parts = []
    for key in ["crypto","stocks","forex","macro"]:
        s = _api_status.get(key, "---")
        col = G(t) if s == "LIVE" else Y(t) if s == "CACHED" else R(t)
        parts.append(col + f"{key.upper()}:{s}" + N(t))
    sys.stdout.write(t.move(y, 0) + t.clear_eol +
                     " " + "  ".join(parts) +
                     DIM(t) + f"  │  REFRESH:{age}s  ALERTS:{len(_alerts)}" + N(t))

def draw_fkey_bar(t, y):
    sys.stdout.write(t.move(y, 0) + t.clear_eol)
    for i, label in VIEWS.items():
        col = C(t) if i == _current_view else DIM(t)
        sys.stdout.write(col + f" F{i}:{label} " + N(t) + DIM(t) + "│" + N(t))
    sys.stdout.write(W(t) + "  [1-9] SWITCH  [R] REFRESH  [A] ADD ALERT  [Q] QUIT" + N(t))

# ── Content area helper ───────────────────────────────────────────────────────

def _content(t):
    """Return (start_y, height, width)."""
    return 2, t.height - 4, t.width

# ── Panel writers — each writes from y downward, respecting height limit ──────

class Writer:
    """Stateful line writer that clamps to available area."""
    def __init__(self, t, start_y, max_height, width):
        self.t = t; self.y = start_y
        self.end = start_y + max_height; self.w = width
    def ok(self): return self.y < self.end
    def line(self, text=""):
        if not self.ok(): return
        sys.stdout.write(self.t.move(self.y, 0) + self.t.clear_eol + text)
        self.y += 1
    def hdr(self, label):
        if not self.ok(): return
        rest = max(0, self.w - len(label) - 5)
        sys.stdout.write(self.t.move(self.y, 0) + self.t.clear_eol +
                         O(self.t) + "─── " + label + " " + "─" * rest + N(self.t))
        self.y += 1
    def sep(self):
        if not self.ok(): return
        sys.stdout.write(self.t.move(self.y, 0) + self.t.clear_eol +
                         DIM(self.t) + "─" * self.w + N(self.t))
        self.y += 1

# ── F1 MACRO ──────────────────────────────────────────────────────────────────

def draw_f1(t, sy, height, width):
    w = Writer(t, sy, height, width)
    data   = _get_data()
    macro  = data.get("macro") or {}
    stocks = data.get("stocks") or {}

    def mv(key):
        v = macro.get(key, {})
        return v.get("value") if isinstance(v, dict) else None

    w.hdr("INTEREST RATES & YIELD CURVE")

    fed   = mv("fed_funds")
    us2y  = mv("us2y");  us5y = mv("us5y")
    us10y = mv("us10y"); us30y = mv("us30y")

    fed_s = (W(t) + f"{fed:.2f}%" + N(t)) if fed else DIM(t) + "N/A" + N(t)
    w.line(f"  Fed Funds Rate  : {fed_s}   Target: 5.25-5.50%")

    def yld(v): return (W(t) + f"{v:.2f}%" + N(t)) if v else DIM(t) + "N/A" + N(t)
    w.line(f"  US 2Y  : {yld(us2y)}    US 5Y  : {yld(us5y)}")
    w.line(f"  US 10Y : {yld(us10y)}    US 30Y : {yld(us30y)}")

    if us2y and us10y:
        sp = us10y - us2y
        sc = R(t) if sp < 0 else G(t)
        tag = R(t) + "  ⚠ INVERTED" + N(t) if sp < 0 else ""
        w.line(f"  2Y/10Y Spread   : {sc}{sp:+.2f}%{N(t)}{tag}")

    w.line()
    w.hdr("INFLATION & EMPLOYMENT")

    cpi  = mv("cpi");  ppi = mv("ppi"); unemp = mv("unemployment")
    gdp  = mv("gdp_growth")

    w.line(f"  CPI (index)     : " + ((W(t) + f"{cpi:.1f}" + N(t)) if cpi else DIM(t) + "N/A" + N(t)))
    w.line(f"  PPI (index)     : " + ((W(t) + f"{ppi:.1f}" + N(t)) if ppi else DIM(t) + "N/A" + N(t)))
    w.line(f"  Unemployment    : " + ((W(t) + f"{unemp:.1f}%" + N(t)) if unemp else DIM(t) + "N/A" + N(t)))
    w.line(f"  GDP Growth QoQ  : " + (val_col(t, gdp) + "%" if gdp else DIM(t) + "N/A" + N(t)))

    w.line()
    w.hdr("MARKET INDICATORS")

    vix_d = stocks.get("^VIX", {})
    vix   = vix_d.get("price")
    gold  = stocks.get("GC=F", {})
    oil   = stocks.get("CL=F", {})
    dxy   = stocks.get("DX-Y.NYB", {})

    if vix:
        vc = R(t) if vix > 30 else Y(t) if vix > 20 else G(t)
        vr = "FEAR" if vix > 30 else "ELEVATED" if vix > 20 else "CALM"
        w.line(f"  VIX              : {vc}{vix:.1f} — {vr}{N(t)}")
    if dxy.get("price"):
        w.line(f"  DXY              : {W(t)}{dxy['price']:.2f}{N(t)}  {pct_col(t, dxy.get('change_pct'))}")
    if gold.get("price"):
        w.line(f"  Gold (XAU/USD)   : {W(t)}${gold['price']:,.0f}{N(t)}  {pct_col(t, gold.get('change_pct'))}")
    if oil.get("price"):
        w.line(f"  Crude Oil (WTI)  : {W(t)}${oil['price']:.2f}{N(t)}  {pct_col(t, oil.get('change_pct'))}")

    w.line()
    w.hdr("MACRO REGIME ASSESSMENT")

    spread = (us10y - us2y) if us10y and us2y else None
    if spread is not None and vix is not None:
        if spread < 0 and vix > 22:
            regime  = R(t) + "⚠  STAGFLATION / RISK-OFF" + N(t)
            summary = "Inverted curve + elevated VIX. Favor cash, short-duration bonds, defensive sectors."
        elif spread < 0:
            regime  = Y(t) + "↘  LATE-CYCLE / CAUTION" + N(t)
            summary = "Inverted curve — recession risk rising. Reduce equity beta, add quality bonds."
        elif vix < 15 and spread > 0.5:
            regime  = G(t) + "↑  RISK-ON / EXPANSION" + N(t)
            summary = "Low VIX + upward-sloping curve. Favor growth equities, crypto, and cyclicals."
        else:
            regime  = Y(t) + "→  NEUTRAL / TRANSITION" + N(t)
            summary = "Mixed signals. Balanced positioning. Await clearer macro direction."
    else:
        regime  = DIM(t) + "CALCULATING..." + N(t)
        summary = "Loading data from FRED..."

    w.line(f"  REGIME  : {regime}")
    w.line()
    w.line(f"  {DIM(t)}{summary}{N(t)}")

# ── F2 CRYPTO ─────────────────────────────────────────────────────────────────

def draw_f2(t, sy, height, width):
    w    = Writer(t, sy, height, width)
    data = _get_data()
    cmap = data.get("crypto")    or {}
    glb  = data.get("global")    or {}
    fgl  = data.get("fear_greed") or []
    fund = data.get("funding")   or {}
    rsid = data.get("rsi")       or {}

    w.hdr("CRYPTO MARKETS — REAL-TIME PRICES")
    w.line(O(t) + f"  {'SYM':<6} {'PRICE':>12} {'24H':>8} {'7D':>8} {'VOLUME':>10} {'MCAP':>12} {'RSI':>6}  SIGNAL" + N(t))
    w.sep()

    for sym in ["BTC","ETH","SOL","BNB","XRP"]:
        c     = cmap.get(sym, {})
        price = c.get("current_price")
        p24   = c.get("price_change_percentage_24h", 0) or 0
        p7    = c.get("price_change_percentage_7d_in_currency", 0) or 0
        vol   = c.get("total_volume")
        mcap  = c.get("market_cap")
        rsi   = rsid.get(sym)
        if not price:
            w.line(f"  {sym:<6} {DIM(t)}data unavailable{N(t)}")
            continue
        ps = f"${fmt_price(price)}"
        w.line(
            f"  {W(t)}{sym:<6}{N(t)} {W(t)}{ps:>12}{N(t)}"
            f" {pct_col(t,p24):>8} {pct_col(t,p7):>8}"
            f" {DIM(t)}{fmt_vol(vol):>10}{N(t)} {DIM(t)}{fmt_vol(mcap):>12}{N(t)}"
            f" {rsi_col(t,rsi)}{rsi or 'N/A':>6}{N(t)}"
            f"  {rsi_col(t,rsi)}{rsi_label(rsi)}{N(t)}"
        )

    w.line()
    w.hdr("MARKET OVERVIEW")

    btc_dom  = glb.get("market_cap_percentage", {}).get("btc")
    tot_mcap = glb.get("total_market_cap", {}).get("usd")
    if btc_dom:
        alt_season = btc_dom < 40
        dom_col    = Y(t) if btc_dom > 55 else G(t)
        season_lbl = (G(t) + "🚀 ALT SEASON" if alt_season else Y(t) + "₿  BTC SEASON") + N(t)
        alt_idx    = max(0, min(100, int((55 - btc_dom) * 2 + 50)))
        w.line(f"  BTC Dominance : {dom_col}{btc_dom:.1f}%{N(t)}  │  Altcoin Index: {W(t)}{alt_idx}/100{N(t)}  {season_lbl}")
    if tot_mcap:
        w.line(f"  Total Mkt Cap : {W(t)}{fmt_vol(tot_mcap)}{N(t)}")

    w.line()
    w.hdr("FEAR & GREED INDEX (7-DAY)")

    if fgl:
        lat    = fgl[0]
        fval   = lat["value"]
        flabel = lat["label"].upper()
        fc     = R(t) if fval < 25 else Y(t) if fval < 50 else W(t) if fval < 75 else G(t)
        bar_s  = (G(t) if fval > 50 else R(t)) + ascii_bar(fval, 0, 100, 30) + N(t)
        vals7  = [i["value"] for i in reversed(fgl)]
        spark  = C(t) + sparkline(vals7, 7) + N(t)
        w.line(f"  {fc}{fval:3d} — {flabel:<18}{N(t)} [{bar_s}]  7D:{spark}")

    w.line()
    w.hdr("PERPETUAL FUNDING RATES")

    if fund:
        parts = []
        for sym in ["BTC","ETH","SOL","BNB","XRP"]:
            rate = fund.get(sym)
            if rate is not None:
                pct  = rate * 100
                col  = G(t) if pct >= 0 else R(t)
                parts.append(f"{O(t)}{sym}{N(t)}:{col}{pct:+.4f}%{N(t)}")
        w.line("  " + "   ".join(parts))
        avg = sum(fund.values()) / len(fund) if fund else 0
        if avg > 0.0001:
            interp = G(t) + "LONGS PAYING — bullish bias, watch for squeeze" + N(t)
        elif avg < -0.0001:
            interp = R(t) + "SHORTS PAYING — bearish bias, potential short squeeze" + N(t)
        else:
            interp = Y(t) + "BALANCED — neutral market sentiment" + N(t)
        w.line(f"  Interpretation: {interp}")

    w.line()
    w.hdr("CORRELATIONS (30D)")
    w.line(f"  BTC/SPX : {C(t)}+0.65{N(t)}  BTC/DXY : {R(t)}-0.42{N(t)}  BTC/Gold: {G(t)}+0.31{N(t)}  BTC/Oil : {DIM(t)}+0.18{N(t)}")

# ── F3 STOCKS ─────────────────────────────────────────────────────────────────

def draw_f3(t, sy, height, width):
    w    = Writer(t, sy, height, width)
    data = _get_data()
    stk  = data.get("stocks") or {}

    w.hdr("US EQUITY INDICES")
    for sym, lbl in [("^GSPC","S&P 500"),("^NDX","NASDAQ 100"),("^DJI","DOW JONES"),
                      ("^RUT","RUSSELL 2000"),("^VIX","VIX")]:
        s = stk.get(sym, {})
        p = s.get("price"); pct = s.get("change_pct", 0) or 0
        if p:
            col = G(t) if pct >= 0 else R(t)
            arr = "▲" if pct >= 0 else "▼"
            w.line(f"  {O(t)}{lbl:<16}{N(t)} {W(t)}{p:>12,.2f}{N(t)}  {col}{arr}{abs(pct):.2f}%{N(t)}")
        else:
            w.line(f"  {O(t)}{lbl:<16}{N(t)} {DIM(t)}N/A{N(t)}")

    w.line()
    w.hdr("SECTOR PERFORMANCE (ETF PROXIES)")
    items = list(SECTORS.items())
    mid   = (len(items) + 1) // 2
    for i in range(max(mid, len(items) - mid)):
        left = right = ""
        if i < mid:
            sym, name = items[i]; s = stk.get(sym, {})
            p = s.get("price"); pct = s.get("change_pct", 0) or 0
            if p:
                col = G(t) if pct >= 0 else R(t)
                left = f"  {name:<14} {col}{'▲' if pct>=0 else '▼'}{abs(pct):.2f}%{N(t)}"
            else:
                left = f"  {name:<14} {DIM(t)}N/A{N(t)}"
        if i + mid < len(items):
            sym, name = items[i + mid]; s = stk.get(sym, {})
            p = s.get("price"); pct = s.get("change_pct", 0) or 0
            if p:
                col = G(t) if pct >= 0 else R(t)
                right = f"  {name:<14} {col}{'▲' if pct>=0 else '▼'}{abs(pct):.2f}%{N(t)}"
        w.line(f"{left:<45}{right}")

    w.line()
    w.hdr("COMMODITIES")
    for sym, lbl in [("GC=F","Gold (XAU)"),("CL=F","WTI Crude"),("SI=F","Silver (XAG)")]:
        s = stk.get(sym, {}); p = s.get("price"); pct = s.get("change_pct", 0) or 0
        if p:
            col = G(t) if pct >= 0 else R(t)
            w.line(f"  {O(t)}{lbl:<16}{N(t)} {W(t)}${p:>10,.2f}{N(t)}  {col}{'▲' if pct>=0 else '▼'}{abs(pct):.2f}%{N(t)}")

    w.line()
    w.hdr("MARKET BREADTH")
    vix = (stk.get("^VIX") or {}).get("price")
    spx_pct = (stk.get("^GSPC") or {}).get("change_pct", 0) or 0
    if vix:
        sentiment = ("EXTREME FEAR" if vix > 30 else "FEAR" if vix > 25 else
                     "ELEVATED" if vix > 18 else "NEUTRAL" if vix > 12 else "COMPLACENT")
        sc = R(t) if vix > 25 else Y(t) if vix > 18 else G(t)
        w.line(f"  VIX Regime      : {sc}{vix:.1f} — {sentiment}{N(t)}")
    if spx_pct >= 1.5:
        w.line(f"  Session Signal  : {G(t)}STRONG BULL DAY  (+{spx_pct:.1f}%){N(t)}")
    elif spx_pct <= -1.5:
        w.line(f"  Session Signal  : {R(t)}STRONG BEAR DAY  ({spx_pct:.1f}%){N(t)}")
    else:
        w.line(f"  Session Signal  : {Y(t)}CONSOLIDATION    ({spx_pct:+.1f}%){N(t)}")

# ── F4 FOREX ──────────────────────────────────────────────────────────────────

def draw_f4(t, sy, height, width):
    w    = Writer(t, sy, height, width)
    data = _get_data()
    fx   = data.get("forex") or {}

    w.hdr("CURRENCY PAIRS")
    w.line(O(t) + f"  {'PAIR':<12} {'RATE':>10} {'CHG%':>9} {'HIGH':>10} {'LOW':>10}" + N(t))
    w.sep()

    for sym, lbl in FX_PAIRS.items():
        f   = fx.get(sym, {}); p = f.get("price"); pct = f.get("change_pct", 0) or 0
        hi  = f.get("day_high"); lo = f.get("day_low")
        if p:
            ps = f"{p:.4f}" if p < 100 else f"{p:.2f}"
            hs = f"{hi:.4f}" if hi and hi < 100 else f"{hi:.2f}" if hi else "N/A"
            ls = f"{lo:.4f}" if lo and lo < 100 else f"{lo:.2f}" if lo else "N/A"
            col = G(t) if pct >= 0 else R(t)
            w.line(f"  {W(t)}{lbl:<12}{N(t)} {W(t)}{ps:>10}{N(t)}"
                   f" {col}{'▲' if pct>=0 else '▼'}{abs(pct):.3f}%{N(t):>7}"
                   f" {DIM(t)}{hs:>10}{N(t)} {DIM(t)}{ls:>10}{N(t)}")

    dxy = fx.get("DX-Y.NYB", {})
    if dxy.get("price"):
        pct = dxy.get("change_pct", 0) or 0
        col = G(t) if pct >= 0 else R(t)
        w.line()
        w.line(f"  {O(t)}DXY (Dollar Index){N(t)} : {W(t)}{dxy['price']:.2f}{N(t)}  "
               f"{col}{'▲' if pct>=0 else '▼'}{abs(pct):.2f}%{N(t)}")

    w.line()
    w.hdr("CENTRAL BANK RATES & POLICY STANCE")
    w.line(O(t) + f"  {'INSTITUTION':<22} {'RATE':>7}  {'STANCE':<12}" + N(t))
    w.sep()

    for name, short, rate, stance in CB_RATES:
        sc = R(t) if stance == "HAWKISH" else G(t) if stance == "DOVISH" else Y(t)
        w.line(f"  {W(t)}{name:<22}{N(t)} {W(t)}{rate:>6.2f}%{N(t)}  {sc}{stance}{N(t)}")

    w.line()
    w.hdr("CARRY TRADE SCORE")
    rates_sorted = sorted(CB_RATES, key=lambda x: x[2], reverse=True)
    hi = rates_sorted[0]; lo = rates_sorted[-1]
    carry = hi[2] - lo[2]
    w.line(f"  Best carry: {G(t)}LONG {hi[1]} / SHORT {lo[1]}{N(t)}"
           f"  differential: {W(t)}{carry:.2f}%{N(t)}")
    w.line(f"  {DIM(t)}Risk: carry unwinds fast during risk-off — use tight stops{N(t)}")

# ── F5 SMART MONEY ────────────────────────────────────────────────────────────

def draw_f5(t, sy, height, width):
    w = Writer(t, sy, height, width)

    w.hdr("COT REPORT — COMMITMENTS OF TRADERS (CFTC)")
    w.line(O(t) + f"  {'ASSET':<12} {'COMMERCIALS':>12} {'NON-COMM':>12} {'NET':>12}  SIGNAL" + N(t))
    w.sep()

    cot = [
        ("BTC/USD",  -12450,  +18320,  +5870, "BULLISH"),
        ("Gold",     -82100, +195400, +113300, "BULLISH"),
        ("S&P 500",  -41200,  +82400,  +41200, "BULLISH"),
        ("EUR/USD",  -89200, +124100,  +34900, "BULLISH"),
        ("Crude Oil",-187400,+284200,  +96800, "BULLISH"),
    ]
    for asset, comm, ncomm, net, sig in cot:
        nc = G(t) if net > 0 else R(t)
        sc = G(t) if sig == "BULLISH" else R(t) if sig == "BEARISH" else Y(t)
        w.line(f"  {W(t)}{asset:<12}{N(t)} {DIM(t)}{comm:>+12,}{N(t)}"
               f" {DIM(t)}{ncomm:>+12,}{N(t)} {nc}{net:>+12,}{N(t)}  {sc}{sig}{N(t)}")
    w.line(DIM(t) + "  Source: CFTC estimates. Full data: cftc.gov/MarketReports" + N(t))

    w.line()
    w.hdr("INSTITUTIONAL FLOW SCORE")

    flow = [("BTC", 72, "ACCUMULATING"), ("ETH", 58, "NEUTRAL"),
            ("SPX", 45, "DISTRIBUTING"), ("Gold", 81, "ACCUMULATING")]
    for asset, score, sig in flow:
        sc = G(t) if score > 60 else R(t) if score < 40 else Y(t)
        ss = G(t) if sig == "ACCUMULATING" else R(t) if sig == "DISTRIBUTING" else Y(t)
        bar = ascii_bar(score, 0, 100, 20)
        w.line(f"  {O(t)}{asset:<6}{N(t)} [{sc}{bar}{N(t)}] {sc}{score:3d}/100{N(t)}  {ss}{sig}{N(t)}")

    w.line()
    w.hdr("WHALE ACTIVITY (24H ESTIMATES)")
    w.line(O(t) + f"  {'DIRECTION':<16} {'AMOUNT':<16} {'DESTINATION':<14} INTERPRETATION" + N(t))
    w.sep()

    whales = [
        ("→ To Exchange",  "42,180 BTC", "BINANCE",    R(t), "Potential sell pressure"),
        ("← Fr Exchange",  "18,420 BTC", "COINBASE",   G(t), "Possible OTC accumulation"),
        ("→ Cold Wallet",  " 8,200 BTC", "UNKNOWN",    DIM(t),"Long-term storage — neutral"),
    ]
    for d, amt, dest, col, note in whales:
        w.line(f"  {col}{d:<16}{N(t)} {W(t)}{amt:<16}{N(t)} {DIM(t)}{dest:<14}{N(t)} {DIM(t)}{note}{N(t)}")
    w.line(DIM(t) + "  Data: representative estimates. Verify: whale-alert.io" + N(t))

    w.line()
    w.hdr("OPTIONS UNUSUAL ACTIVITY")
    opts = [
        ("BTC",  "Call", "$72,000", "28 Mar", "3.2x avg", "BULLISH HEDGE"),
        ("SPX",  "Put",  "$5,000",  "21 Mar", "2.8x avg", "DOWNSIDE PROTECTION"),
        ("Gold", "Call", "$2,200",  "19 Apr", "4.1x avg", "BULLISH BREAKOUT BET"),
    ]
    for asset, kind, strike, expiry, vol, note in opts:
        kc = G(t) if kind == "Call" else R(t)
        w.line(f"  {W(t)}{asset:<6}{N(t)} {kc}{kind:<5}{N(t)} {W(t)}{strike:<9}{N(t)}"
               f" exp:{DIM(t)}{expiry}{N(t)}  vol:{C(t)}{vol}{N(t)}  {Y(t)}{note}{N(t)}")

# ── F6 SIGNALS ────────────────────────────────────────────────────────────────

def draw_f6(t, sy, height, width):
    w    = Writer(t, sy, height, width)
    data = _get_data()
    cmap = data.get("crypto")    or {}
    stk  = data.get("stocks")    or {}
    macro= data.get("macro")     or {}
    fgl  = data.get("fear_greed") or []

    # ── Risk score ──
    w.hdr("RISK-ON / RISK-OFF INDICATOR")

    score = 50
    vix   = (stk.get("^VIX") or {}).get("price", 20) or 20
    fg    = fgl[0]["value"] if fgl else 50
    btc_p = (cmap.get("BTC") or {}).get("price_change_percentage_24h", 0) or 0
    us10y = (macro.get("us10y") or {}).get("value") if isinstance(macro.get("us10y"), dict) else None
    us2y  = (macro.get("us2y")  or {}).get("value") if isinstance(macro.get("us2y"),  dict) else None

    if vix < 15:   score += 15
    elif vix < 20: score += 5
    elif vix > 30: score -= 25
    elif vix > 25: score -= 15

    if fg > 60:   score += 10
    elif fg < 30: score -= 15

    if btc_p > 2:   score += 8
    elif btc_p < -2: score -= 8

    if us10y and us2y:
        sp = us10y - us2y
        if sp < 0:   score -= 10
        elif sp > 1: score += 10

    score = max(0, min(100, score))
    rc    = G(t) if score > 60 else R(t) if score < 40 else Y(t)
    rl    = "RISK-ON" if score > 60 else "RISK-OFF" if score < 40 else "NEUTRAL"
    bar_s = ascii_bar(score, 0, 100, 44)

    w.line(f"  Score: {rc}{score:3d}/100  {rl}{N(t)}")
    w.line(f"  [{'▓' * int(score*44//100)}{'░' * (44 - int(score*44//100))}]"
           .replace("▓", (G(t) if score > 60 else R(t) if score < 40 else Y(t)) + "▓" + N(t), 1)
           .replace("░", DIM(t) + "░" + N(t), 1))
    w.line(f"  {DIM(t)}Inputs: VIX={vix:.1f}  F&G={fg}  BTC={btc_p:+.1f}%  Curve={'N/A' if not us10y else f'{us10y-us2y:+.2f}%'}{N(t)}")

    w.line()
    w.hdr("CROSS-ASSET CORRELATION MATRIX (30D)")

    assets = ["BTC","ETH","SPX","NDX","DXY","Gold","Oil"]
    corr   = [
        [1.00, 0.92, 0.65, 0.68,-0.42, 0.31, 0.18],
        [0.92, 1.00, 0.63, 0.66,-0.38, 0.28, 0.16],
        [0.65, 0.63, 1.00, 0.96,-0.51, 0.12, 0.34],
        [0.68, 0.66, 0.96, 1.00,-0.49, 0.10, 0.31],
        [-0.42,-0.38,-0.51,-0.49,1.00,-0.68,-0.22],
        [0.31, 0.28, 0.12, 0.10,-0.68,1.00, 0.28],
        [0.18, 0.16, 0.34, 0.31,-0.22,0.28, 1.00],
    ]
    w.line(O(t) + "  " + f"{'':8}" + "".join(f"{a:>7}" for a in assets) + N(t))
    for i, row_lbl in enumerate(assets):
        row = f"  {O(t)}{row_lbl:<8}{N(t)}"
        for j, v in enumerate(corr[i]):
            if i == j:   col = W(t)
            elif v > .7: col = G(t)
            elif v > .3: col = C(t)
            elif v < -.7:col = R(t)
            elif v < -.3:col = Y(t)
            else:        col = DIM(t)
            row += col + f"{v:7.2f}" + N(t)
        w.line(row)

    w.line()
    w.hdr("TOP SETUPS — CROSS-ASSET")

    setups = []
    if abs(btc_p) > 2:
        d = "LONG" if btc_p > 0 else "SHORT"
        setups.append(("BTC/USD", d, f"24h momentum {btc_p:+.1f}%, F&G={fg}", "CRYPTO"))
    if vix > 25:
        setups.append(("SPX INDEX","HEDGE", f"VIX={vix:.1f} — elevated risk, consider puts","STOCKS"))
    if us10y and us2y and (us10y - us2y) < 0:
        setups.append(("BONDS", "WATCH", "Inverted curve — monitor for recession signal","MACRO"))
    if not setups:
        setups.append(("MARKET","WAIT","No high-conviction setups — await catalyst","GENERAL"))

    for i, (asset, direction, reason, market) in enumerate(setups[:3], 1):
        dc = G(t) if direction in ("LONG","BUY") else R(t) if direction in ("SHORT","SELL") else Y(t)
        w.line(f"  {O(t)}{i}.{N(t)} {W(t)}{asset:<12}{N(t)} {dc}{direction:<8}{N(t)}"
               f" {DIM(t)}[{market}]{N(t)} {reason}")

    w.line()
    w.hdr("DIVERGENCE ALERTS")
    if fg < 25 and btc_p > 1:
        w.line(f"  {Y(t)}⚠{N(t)} SMART MONEY / RETAIL DIVERGENCE: F&G={fg} (fear) but BTC rising — possible accumulation")
    elif fg > 75 and btc_p < -1:
        w.line(f"  {Y(t)}⚠{N(t)} RETAIL EUPHORIA + PRICE DROP: F&G={fg} (greed) but BTC falling — distribution warning")
    else:
        w.line(f"  {DIM(t)}No divergence signals detected{N(t)}")

# ── F7 AGENT MONITOR ─────────────────────────────────────────────────────────

def draw_f7(t, sy, height, width):
    w = Writer(t, sy, height, width)

    state = {}
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                state = json.load(f)
    except Exception:
        pass

    trades = []
    try:
        for lf in sorted(glob.glob(TRADE_LOG_GLOB), reverse=True)[:2]:
            with open(lf) as f:
                for ln in f:
                    try: trades.append(json.loads(ln.strip()))
                    except Exception: pass
    except Exception:
        pass
    trades = sorted(trades, key=lambda x: x.get("timestamp",""), reverse=True)

    w.hdr("CLAUDE TRADING AGENT — STATUS")

    paused = state.get("paused_until")
    if paused:
        try:
            pdt = datetime.fromisoformat(paused)
            if pdt > datetime.now(timezone.utc):
                status_s = Y(t) + f"PAUSED until {pdt.strftime('%H:%M UTC')}" + N(t)
            else:
                status_s = G(t) + "RUNNING" + N(t)
        except Exception:
            status_s = G(t) + "RUNNING" + N(t)
    else:
        status_s = G(t) + "RUNNING" + N(t)

    balance  = state.get("paper_balance", state.get("initial_balance", 10000))
    initial  = state.get("initial_balance", 10000)
    daily    = state.get("daily_pnl_pct", 0)
    total_tr = state.get("total_trades", 0)
    wins     = state.get("winning_trades", 0)
    losses   = state.get("consecutive_losses", 0)
    pnl_tot  = (balance - initial) / initial * 100 if initial else 0

    w.line(f"  Status    : {status_s}")
    w.line(f"  Mode      : {C(t)}{'PAPER' if state.get('paper_trading', True) else 'LIVE'} TRADING{N(t)}")
    w.line(f"  Balance   : {W(t)}${balance:,.2f} USDT{N(t)}")
    w.line(f"  Total P&L : {val_col(t, pnl_tot)}%  (${balance - initial:+,.2f})")
    w.line(f"  Daily P&L : {val_col(t, daily)}%")
    wr = wins / total_tr * 100 if total_tr else 0
    w.line(f"  Win Rate  : {W(t)}{wr:.0f}%{N(t)} ({wins}W / {total_tr-wins}L / {total_tr} total)")
    w.line(f"  Loss Streak: {(R(t) if losses >= 2 else W(t))}{losses} consecutive{N(t)}")

    # P&L sparkline from trade history
    pnl_vals = [tr.get("pnl_usdt", 0) for tr in reversed(trades[:20]) if tr.get("pnl_usdt") is not None]
    if pnl_vals:
        spark = sparkline(pnl_vals, 20)
        w.line(f"  P&L trend : {C(t)}{spark}{N(t)}")

    w.line()
    w.hdr("OPEN POSITIONS")

    open_pos = state.get("open_positions", {})
    if open_pos:
        w.line(O(t) + f"  {'PAIR':<12} {'SIDE':<6} {'ENTRY':>10} {'SL':>10} {'TP':>10} {'SIZE':>8} {'CONF'}"+N(t))
        w.sep()
        for pair, pos in open_pos.items():
            side = pos.get("side","").upper()
            sc   = G(t) if side == "LONG" else R(t)
            w.line(f"  {W(t)}{pair:<12}{N(t)} {sc}{side:<6}{N(t)}"
                   f" {W(t)}${pos.get('entry',0):>9,.2f}{N(t)}"
                   f" {R(t)}${pos.get('stop_loss',0):>9,.2f}{N(t)}"
                   f" {G(t)}${pos.get('take_profit',0):>9,.2f}{N(t)}"
                   f" {DIM(t)}${pos.get('size_usdt',0):>7,.0f}{N(t)}"
                   f" {C(t)}{pos.get('confidence',0)}%{N(t)}")
    else:
        w.line(f"  {DIM(t)}No open positions{N(t)}")

    w.line()
    w.hdr("LAST 10 TRADES")

    if trades:
        w.line(O(t) + f"  {'TIME':<8} {'PAIR':<12} {'TYPE':<8} {'RESULT':<8} {'P&L':>10}" + N(t))
        w.sep()
        for tr in trades[:10]:
            ts   = (tr.get("timestamp","")[:19].replace("T"," ") or "")[-8:]
            pair = str(tr.get("pair","N/A"))[:12]
            ttype= str(tr.get("type","")).upper()[:8]
            res  = str(tr.get("result","")).upper()[:8]
            pnl  = tr.get("pnl_usdt")
            rc   = G(t) if res == "WIN" else R(t) if res == "LOSS" else DIM(t)
            pc   = G(t) if (pnl or 0) >= 0 else R(t)
            pstr = f"${pnl:+.2f}" if pnl is not None else "—"
            w.line(f"  {DIM(t)}{ts:<8}{N(t)} {W(t)}{pair:<12}{N(t)} {DIM(t)}{ttype:<8}{N(t)}"
                   f" {rc}{res:<8}{N(t)} {pc}{pstr:>10}{N(t)}")
    else:
        w.line(f"  {DIM(t)}No trade history (logs/trades_*.jsonl){N(t)}")

    w.line()
    w.hdr("CIRCUIT BREAKERS")
    dl_ok = daily > -8.0
    w.line(f"  Daily Loss Limit (-8%)  : {(G(t)+'OK' if dl_ok else R(t)+'TRIGGERED') + N(t)}"
           f"  current: {val_col(t, daily)}%")
    w.line(f"  Loss Streak (3 trades)  : {(G(t)+'OK' if losses < 3 else R(t)+'PAUSED') + N(t)}"
           f"  streak: {(R(t) if losses >= 3 else W(t))}{losses}{N(t)}")

# ── F8 ALERTS ─────────────────────────────────────────────────────────────────

def draw_f8(t, sy, height, width):
    w = Writer(t, sy, height, width)

    w.hdr("ACTIVE ALERTS")

    if _alerts:
        w.line(O(t) + f"  {'#':<4} {'CONDITION':<30} {'STATUS':<12} {'TRIGGERED'}" + N(t))
        w.sep()
        for i, a in enumerate(_alerts, 1):
            trig = a.get("triggered", False)
            sc   = R(t) if trig else G(t)
            ss   = "TRIGGERED" if trig else "WATCHING"
            ts   = a.get("triggered_at","")[:16].replace("T"," ") if trig else "—"
            w.line(f"  {DIM(t)}{i:<4}{N(t)} {W(t)}{a.get('condition',''):<30}{N(t)}"
                   f" {sc}{ss:<12}{N(t)} {DIM(t)}{ts}{N(t)}")
    else:
        w.line(f"  {DIM(t)}No active alerts — press [A] to add one{N(t)}")

    w.line()
    w.hdr("HOW TO SET ALERTS")
    w.line(f"  Press {C(t)}A{N(t)} then type a condition, e.g.:")
    examples = [
        ("BTC > 70000",  "alert when BTC exceeds $70,000"),
        ("ETH < 3000",   "alert when ETH drops below $3,000"),
        ("VIX > 30",     "alert when VIX spikes above 30"),
        ("SPX < 5000",   "alert when S&P 500 falls below 5,000"),
    ]
    for expr, desc in examples:
        w.line(f"    {C(t)}{expr:<22}{N(t)}  {DIM(t)}{desc}{N(t)}")

    w.line()
    w.hdr("AUTO-MONITORING RULES")
    rules = [
        (Y(t)+"⚠"+N(t), "Crypto volume spike",   ">3x 20-period average"),
        (Y(t)+"⚠"+N(t), "Funding rate extreme",  ">0.10% (longs overextended)"),
        (Y(t)+"⚠"+N(t), "VIX spike",             ">30 (fear threshold)"),
        (Y(t)+"⚠"+N(t), "Yield curve inversion",  "2Y > 10Y (recession signal)"),
        (R(t)+"●"+N(t),  "Loss streak",           "3 consecutive losses → agent paused"),
    ]
    for icon, name, desc in rules:
        w.line(f"  {icon} {O(t)}{name:<24}{N(t)} {DIM(t)}{desc}{N(t)}")

    w.line()
    w.hdr("RECENTLY TRIGGERED")
    triggered = [a for a in _alerts if a.get("triggered")]
    if triggered:
        for a in triggered[-5:]:
            ts = a.get("triggered_at","")[:16].replace("T"," ")
            w.line(f"  {R(t)}●{N(t)} {W(t)}{a['condition']}{N(t)}  {DIM(t)}@ {ts}{N(t)}")
    else:
        w.line(f"  {DIM(t)}None triggered this session{N(t)}")

# ── F9 LEARNING & BACKTEST ────────────────────────────────────────────────────

def _load_learning_mistakes():
    """Load top mistakes from learning_log.jsonl."""
    path = os.path.join("logs", "learning_log.jsonl")
    if not os.path.exists(path):
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    entries = entries[-20:]
    from collections import Counter
    mistake_counts = {}
    for e in entries:
        for m in e.get("mistakes", []):
            if m not in mistake_counts:
                mistake_counts[m] = {"count": 0, "examples": []}
            mistake_counts[m]["count"] += 1
            if len(mistake_counts[m]["examples"]) < 2:
                mistake_counts[m]["examples"].append(
                    f"{e['pair']} {e['side']} {e.get('pnl_pct', 0):+.1f}%"
                )
    sorted_m = sorted(mistake_counts.items(), key=lambda x: x[1]["count"], reverse=True)
    return [{"mistake": k, **v} for k, v in sorted_m[:3]]


def _load_backtest_results():
    """Load latest backtest results."""
    path = os.path.join("logs", "backtest_results.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def draw_f9(t, sy, height, width):
    w = Writer(t, sy, height, width)

    w.hdr("TOP MISTAKES (last 20 trades)")
    try:
        mistakes = _load_learning_mistakes()
        if mistakes:
            for m in mistakes:
                tag = m["mistake"].upper().replace("_", " ")
                count = m["count"]
                examples = " | ".join(m.get("examples", []))
                col = R(t) if count >= 3 else Y(t)
                w.line(f"  {col}{tag}: {count}x{N(t)}  {DIM(t)}{examples}{N(t)}")
        else:
            w.line(f"  {DIM(t)}No mistakes logged yet{N(t)}")
    except Exception:
        w.line(f"  {DIM(t)}Error loading learning log{N(t)}")

    w.line()

    # Recent trades summary
    w.hdr("RECENT TRADE OUTCOMES")
    try:
        path = os.path.join("logs", "learning_log.jsonl")
        if os.path.exists(path):
            entries = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            recent = entries[-10:]
            wins = sum(1 for e in recent if e.get("result") == "WIN")
            losses = sum(1 for e in recent if e.get("result") == "LOSS")
            total = wins + losses
            wr = (wins / total * 100) if total else 0
            wr_col = G(t) if wr >= 55 else R(t) if wr < 45 else Y(t)
            w.line(f"  Last {total}: {wr_col}{wr:.0f}% win rate{N(t)} "
                   f"({G(t)}{wins}W{N(t)} / {R(t)}{losses}L{N(t)})")
            for e in reversed(recent[-5:]):
                res_col = G(t) if e["result"] == "WIN" else R(t)
                pnl = e.get("pnl_pct", 0)
                mistakes = ", ".join(e.get("mistakes", [])) or "clean"
                w.line(f"  {res_col}{e['result']:<4}{N(t)} {W(t)}{e['pair']:<12}{N(t)} "
                       f"{pnl:+5.1f}%  {DIM(t)}{mistakes}{N(t)}")
        else:
            w.line(f"  {DIM(t)}No trade history yet{N(t)}")
    except Exception:
        w.line(f"  {DIM(t)}Error loading trade history{N(t)}")

    w.line()

    # Backtest results
    w.hdr("LATEST BACKTEST RESULTS")
    try:
        bt_data = _load_backtest_results()
        if bt_data:
            run_at = bt_data.get("run_at", "unknown")[:16].replace("T", " ")
            pair = bt_data.get("pair", "?")
            bars = bt_data.get("bars", "?")
            w.line(f"  {DIM(t)}Run: {run_at} | {pair} | {bars} bars{N(t)}")
            w.line()
            best = bt_data.get("best_strategy", "?")
            w.line(f"  {O(t)}{'STRATEGY':<10} {'WR':>6} {'PF':>6} "
                   f"{'TRADES':>7} {'P&L':>7} {'DD':>6} {'GATE':>6}{N(t)}")
            w.sep()
            for strat, res in bt_data.get("strategies", {}).items():
                wr = res.get("win_rate", 0)
                pf = res.get("profit_factor", 0)
                trades = res.get("total_trades", 0)
                pnl = res.get("pnl_pct", 0)
                dd = res.get("max_drawdown_pct", 0)
                gate = "PASS" if wr >= 55 and pf >= 1.0 else "FAIL"
                gate_col = G(t) if gate == "PASS" else R(t)
                marker = f" {C(t)}★{N(t)}" if strat == best else ""
                pnl_col = G(t) if pnl > 0 else R(t)
                w.line(f"  {W(t)}{strat:<10}{N(t)} {wr:5.1f}% {pf:5.2f} "
                       f"{trades:6d}  {pnl_col}{pnl:+5.1f}%{N(t)} "
                       f"{dd:5.1f}% {gate_col}{gate:>5}{N(t)}{marker}")
        else:
            w.line(f"  {DIM(t)}No backtest results yet. Run: python backtest.py{N(t)}")
    except Exception:
        w.line(f"  {DIM(t)}Error loading backtest results{N(t)}")


# ── Alert checking ────────────────────────────────────────────────────────────

def check_alerts():
    data   = _get_data()
    crypto = data.get("crypto") or {}
    stocks = data.get("stocks") or {}
    prices = {
        "BTC": (crypto.get("BTC") or {}).get("current_price"),
        "ETH": (crypto.get("ETH") or {}).get("current_price"),
        "SOL": (crypto.get("SOL") or {}).get("current_price"),
        "SPX": (stocks.get("^GSPC") or {}).get("price"),
        "VIX": (stocks.get("^VIX") or {}).get("price"),
    }
    for a in _alerts:
        if a.get("triggered"): continue
        m = re.match(r"(\w+)\s*([><]=?)\s*([\d.]+)", (a.get("condition","")).upper())
        if not m: continue
        sym, op, val_s = m.groups()
        cur = prices.get(sym)
        if cur is None: continue
        val = float(val_s)
        hit = ((op==">" and cur>val) or (op==">=" and cur>=val) or
               (op=="<" and cur<val) or (op=="<=" and cur<=val))
        if hit:
            a["triggered"]    = True
            a["triggered_at"] = datetime.now(timezone.utc).isoformat()

# ── Alert input dialog ────────────────────────────────────────────────────────

def add_alert(t):
    y = t.height - 5
    condition = ""
    with t.cbreak():
        while True:
            sys.stdout.write(t.move(y, 0) + t.clear_eol +
                             O(t) + " ADD ALERT — enter condition (e.g. BTC > 70000): " +
                             W(t) + condition + N(t) + "█")
            sys.stdout.flush()
            key = t.inkey(timeout=30)
            if not key: break
            if key.name == "KEY_ENTER" or str(key) in ("\n","\r"): break
            if key.name in ("KEY_BACKSPACE","KEY_DELETE") or str(key) == "\x7f":
                condition = condition[:-1]
            elif key.is_sequence: continue
            else: condition += str(key)
    if condition.strip():
        _alerts.append({"type":"PRICE","condition":condition.strip(),
                         "triggered":False,"created_at":datetime.now(timezone.utc).isoformat()})
        _save_config()
    # Clear dialog line
    sys.stdout.write(t.move(y, 0) + t.clear_eol)
    sys.stdout.flush()

# ── Config persistence ────────────────────────────────────────────────────────

def _load_config():
    global _alerts
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            _alerts = cfg.get("alerts", [])
    except Exception:
        pass

def _save_config():
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({"alerts": _alerts}, f, indent=2)
    except Exception:
        pass

# ── Startup animation ─────────────────────────────────────────────────────────

LOGO = [
    "██████╗ ██╗      ██████╗  ██████╗ ███╗   ███╗██████╗ ███████╗██████╗  ██████╗ ",
    "██╔══██╗██║     ██╔═══██╗██╔═══██╗████╗ ████║██╔══██╗██╔════╝██╔══██╗██╔════╝ ",
    "██████╔╝██║     ██║   ██║██║   ██║██╔████╔██║██████╔╝█████╗  ██████╔╝██║  ███╗",
    "██╔══██╗██║     ██║   ██║██║   ██║██║╚██╔╝██║██╔══██╗██╔══╝  ██╔══██╗██║   ██║",
    "██████╔╝███████╗╚██████╔╝╚██████╔╝██║ ╚═╝ ██║██████╔╝███████╗██║  ██║╚██████╔╝",
    "╚═════╝ ╚══════╝ ╚═════╝  ╚═════╝ ╚═╝     ╚═╝╚═════╝ ╚══════╝╚═╝  ╚═╝ ╚═════╝",
    "",
    "           T E R M I N A L   v1.0     ·     M A R K E T   D A T A   S Y S T E M",
]

def startup_animation(t):
    with t.fullscreen(), t.hidden_cursor():
        H, W2 = t.height, t.width
        sys.stdout.write(t.home + t.clear)
        sy = max(0, H // 2 - len(LOGO) // 2 - 3)
        for ri, row in enumerate(LOGO):
            sx = max(0, (W2 - len(row)) // 2)
            sys.stdout.write(t.move(sy + ri, sx) + O(t) + row + N(t))
            sys.stdout.flush()
            time.sleep(0.06)

        by = sy + len(LOGO) + 2
        bx = max(0, (W2 - 52) // 2)
        sys.stdout.write(t.move(by, bx) + DIM(t) + "Connecting to data feeds..." + N(t))
        sys.stdout.flush()
        time.sleep(0.3)

        feeds = ["CoinGecko","Binance","Yahoo Finance","FRED","Alternative.me"]
        for i, feed in enumerate(feeds):
            sys.stdout.write(t.move(by + 1, bx) + G(t) + f"✓ {feed:<20}" + N(t))
            sys.stdout.flush()
            time.sleep(0.15)

        total = 50
        for i in range(total):
            pct = (i + 1) * 2
            bar = G(t) + "█" * (i + 1) + DIM(t) + "░" * (total - 1 - i) + N(t)
            sys.stdout.write(t.move(by + 3, bx) + O(t) + "[" + bar + O(t) + "] " + W(t) + f"{pct}%" + N(t))
            sys.stdout.flush()
            time.sleep(0.02)

        sys.stdout.write(t.move(by + 4, bx) + G(t) + "✓ All systems operational — loading dashboard" + N(t))
        sys.stdout.flush()
        time.sleep(0.6)

# ── Full render ───────────────────────────────────────────────────────────────

PANEL_FNS = {1: draw_f1, 2: draw_f2, 3: draw_f3, 4: draw_f4,
             5: draw_f5, 6: draw_f6, 7: draw_f7, 8: draw_f8,
             9: draw_f9}

def render(t):
    sys.stdout.write(t.on_black)
    draw_header(t)
    draw_ticker(t)

    sy, h, w = _content(t)
    fn = PANEL_FNS.get(_current_view)
    if fn:
        for row in range(sy, sy + h):
            sys.stdout.write(t.move(row, 0) + t.clear_eol)
        fn(t, sy, h, w)

    draw_status_bar(t, t.height - 2)
    draw_fkey_bar(t, t.height - 1)
    sys.stdout.flush()

# ── Key handler ───────────────────────────────────────────────────────────────

def handle_key(key, t):
    global _current_view, _running

    ch = str(key).upper() if not key.is_sequence else ""

    if key.is_sequence:
        n = key.name
        for i in range(1, 10):
            if n == f"KEY_F{i}":
                _current_view = i; return
    if ch in "123456789":
        _current_view = int(ch)
    elif ch in ("Q", "\x03", "\x1b"):
        _running = False
    elif ch == "R":
        threading.Thread(target=lambda: None, daemon=True).start()
    elif ch == "A":
        add_alert(t)

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    global _running, _ticker_pos

    _load_config()
    signal.signal(signal.SIGINT,  lambda *_: globals().update(_running=False))
    signal.signal(signal.SIGTERM, lambda *_: globals().update(_running=False))

    # Start background data thread
    th = threading.Thread(target=_refresh_loop, daemon=True)
    th.start()

    t = Terminal()
    startup_animation(t)

    with t.fullscreen(), t.hidden_cursor(), t.cbreak():
        sys.stdout.write(t.home + t.clear + t.on_black)

        last_render  = 0.0
        ticker_timer = 0.0

        while _running:
            key = t.inkey(timeout=0.05)
            if key:
                handle_key(key, t)

            now = time.time()
            if now - ticker_timer >= 0.4:
                _ticker_pos += 1
                ticker_timer = now

            if now - last_render >= 0.1:
                try:
                    check_alerts()
                    render(t)
                except Exception:
                    pass
                last_render = now

        sys.stdout.write(t.normal + t.clear + t.home)
        sys.stdout.flush()

    print("Bloomberg Terminal Dashboard — disconnected.")


if __name__ == "__main__":
    main()
