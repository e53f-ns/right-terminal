"""Upscale prop-account panel for the web dashboard (read-only).

GET /api/upscale -> JSON, GET /upscale -> a small self-contained page.
Reads GET /accounts/with-risk-status through UPSCALE_PROXY (api.upscale.trade is only
reachable through the Happ SOCKS proxy on this machine). The API key is read from the
environment and never appears in a response or a log line.
Money values from Upscale are fixed-point 1e9 strings.
"""
import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("UPSCALE_BASE_URL", "https://api.upscale.trade")
FP9 = 1_000_000_000
_cache: dict = {"t": 0.0, "v": None}
TTL = 15


def _f(x) -> Optional[float]:
    try:
        return float(x) / FP9
    except Exception:
        return None


def _pct(x) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def _session() -> requests.Session:
    s = requests.Session()
    proxy = (os.getenv("UPSCALE_PROXY") or "").strip()
    if proxy:
        s.trust_env = False
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _bool_env(k: str, default: str) -> bool:
    return os.getenv(k, default).lower() == "true"


def snapshot(force: bool = False) -> dict:
    now = time.time()
    if not force and _cache["v"] is not None and now - _cache["t"] < TTL:
        return _cache["v"]
    out = {
        "ok": False, "error": None, "ts": int(now),
        "mode": "DRY_RUN" if _bool_env("UPSCALE_DRY_RUN", "true") else "LIVE",
        "allow_live": _bool_env("UPSCALE_ALLOW_LIVE", "false"),
        "brain": os.getenv("TRADING_BRAIN_PROVIDER", "?"),
        "strategies": os.getenv("ALLOWED_STRATEGIES", "SCALP"),
        "news_blackout_h": [os.getenv("NEWS_BLACKOUT_HOURS_BEFORE", "0"), os.getenv("NEWS_BLACKOUT_HOURS_AFTER", "0")],
    }
    key = os.getenv("UPSCALE_API_KEY", "")
    if not key:
        out["error"] = "UPSCALE_API_KEY не задан"
    else:
        try:
            r = _session().get(f"{BASE_URL}/accounts/with-risk-status",
                               headers={"Authorization": f"Bearer {key}"}, timeout=15)
            if not r.ok:
                out["error"] = f"HTTP {r.status_code}"
            else:
                rows = r.json()
                a = rows[0] if isinstance(rows, list) and rows else None
                if not a:
                    out["error"] = "нет счетов"
                else:
                    rs = a.get("riskStatus") or {}
                    init = _f(a.get("initialAccountBalance"))
                    bal = _f(a.get("accountBalance"))
                    eq = _f(rs.get("currentEquity")) if rs else None
                    eq = eq if eq is not None else bal
                    day0 = _f(a.get("dayStartEquity"))
                    max_d = _pct(a.get("maxDailyDrawdown"))
                    max_t = _pct(a.get("maxTotalDrawdown"))
                    target = _pct(a.get("profitTarget"))
                    daily_dd = ((day0 - eq) / day0 * 100) if (day0 and eq is not None) else None
                    total_dd = ((init - eq) / init * 100) if (init and eq is not None) else None
                    profit = ((eq - init) / init * 100) if (init and eq is not None) else None
                    out.update(
                        ok=True, type=a.get("type"), status=a.get("status"), phase=a.get("currentPhase"),
                        account_tail=str(a.get("accountId", ""))[-4:],
                        initial=init, balance=bal, equity=eq, day_start_equity=day0,
                        daily_dd_pct=daily_dd, daily_dd_limit=max_d,
                        total_dd_pct=total_dd, total_dd_limit=max_t,
                        profit_pct=profit, profit_target=target,
                        profitable_days=a.get("profitableDays"), min_days=a.get("minTradingDays"),
                        api_trading=a.get("apiTrading"), trading_lock=a.get("tradingLockReason"),
                        failure=a.get("failureReason"), created=a.get("createdAt"),
                    )
        except Exception as e:      # never leak the key: only the exception class + short message
            out["error"] = f"нет связи ({type(e).__name__})"
            logger.warning("upscale panel: %s", type(e).__name__)
    _cache.update(t=now, v=out)
    return out


PAGE = """<!doctype html><meta charset="utf-8"><title>Upscale</title>
<style>body{background:#0b0f14;color:#d6dde6;font:14px/1.5 ui-monospace,Menlo,Consolas,monospace;margin:24px}
h1{font-size:18px;margin:0 0 12px}.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.c{background:#121923;border:1px solid #1f2a38;border-radius:8px;padding:12px}.k{color:#7d8da1;font-size:12px}
.v{font-size:20px}.bar{height:8px;background:#1f2a38;border-radius:4px;margin-top:6px;overflow:hidden}
.bar i{display:block;height:100%;background:#2fbf71}.warn i{background:#e0a030}.bad i{background:#e0503a}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px}.dry{background:#1d3b2b;color:#6fe3a0}
.live{background:#4a1f1a;color:#ff8f7e}a{color:#7cc0ff}</style>
<h1>UPSCALE <span id=mode class=pill></span> <small id=ts class=k></small> <a href="/">← дашборд</a></h1>
<div id=err class=k></div><div class=g id=g></div>
<script>
const $=id=>document.getElementById(id);
const n=(x,d=2)=>x==null?'–':Number(x).toFixed(d);
function card(t,v,sub,pct,lim){let cls='';if(pct!=null&&lim){const r=pct/lim;cls=r>=.8?'bad':r>=.5?'warn':'';}
 const w=pct!=null&&lim?Math.max(0,Math.min(100,pct/lim*100)):0;
 return `<div class=c><div class=k>${t}</div><div class=v>${v}</div><div class=k>${sub||''}</div>${lim?`<div class="bar ${cls}"><i style="width:${w}%"></i></div>`:''}</div>`}
async function load(){try{const d=await (await fetch('/api/upscale')).json();
 $('mode').textContent=d.mode;$('mode').className='pill '+(d.mode==='LIVE'?'live':'dry');
 $('ts').textContent=new Date(d.ts*1000).toLocaleTimeString();$('err').textContent=d.ok?'':(d.error||'нет связи');
 let h='';
 if(d.ok){h+=card('Equity','$'+n(d.equity),'старт $'+n(d.initial)+' · счёт …'+d.account_tail+' · '+d.type+'/'+d.phase);
 h+=card('Дневная просадка',n(d.daily_dd_pct)+'% / '+n(d.daily_dd_limit,0)+'%','от старта дня $'+n(d.day_start_equity),Math.max(0,d.daily_dd_pct),d.daily_dd_limit);
 h+=card('Общая просадка',n(d.total_dd_pct)+'% / '+n(d.total_dd_limit,0)+'%','от начального баланса',Math.max(0,d.total_dd_pct),d.total_dd_limit);
 h+=card('Цель',n(d.profit_pct)+'% / '+n(d.profit_target,0)+'%','',Math.max(0,d.profit_pct),d.profit_target);
 h+=card('Профитные дни',(d.profitable_days??'–')+' / '+(d.min_days??'–'),'каждый ≥ +0.5% от начального');
 h+=card('API-торговля',d.api_trading?'вкл':'выкл',d.trading_lock?('блок: '+d.trading_lock):(d.failure?('провал: '+d.failure):d.status));}
 h+=card('Агент','мозг: '+d.brain,'стратегии: '+d.strategies+' · live-флаг: '+(d.allow_live?'ДА':'нет'));
 h+=card('Блок новостей','−'+d.news_blackout_h[0]+'ч / +'+d.news_blackout_h[1]+'ч','0 = выключено');
 $('g').innerHTML=h}catch(e){$('err').textContent='нет связи с дашбордом'}}
load();setInterval(load,15000);
</script>"""
