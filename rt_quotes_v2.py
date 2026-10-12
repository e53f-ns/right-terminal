"""Read-only quote adapter. No trading imports or process mutations.

Optional deps: curl_cffi (preferred Yahoo chart transport), requests, yfinance.
RT_QUOTES_CACHE selects a durable JSON cache (default ~/.cache/rt-dashboard/quotes-v2.json).
Single writer per cache path; calls within a process are serialized. No route rotation.
Prices are provider observations, never synthesized. Polling is NOT streaming.
"""
import json
import math
import os
from pathlib import Path
import threading
import time
from urllib.parse import quote as urlquote
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

_LOCK = threading.RLock()
_STATE = None
_CACHE_PATH = Path(os.getenv('RT_QUOTES_CACHE', str(Path.home()/'.cache/rt-dashboard/quotes-v2.json')))
YAHOO_POLL_SEC = 120
HTX_POLL_SEC = 15
YAHOO_REQUEST_GAP = 0.2
_LAST_REQUEST = 0.0

class RateLimited(RuntimeError):
    pass

def finite(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None

def stamp(value):
    if isinstance(value, datetime): value = value.timestamp()
    n = finite(value)
    return int(n) if n is not None and n > 0 else None

def _state():
    global _STATE
    if _STATE is None:
        try:
            d = json.loads(_CACHE_PATH.read_text(encoding='utf-8'))
            _STATE = d if isinstance(d, dict) and d.get('version') == 2 else {}
        except (OSError, ValueError):
            _STATE = {}
        for k in ('rows', 'attempts', 'cooldowns', 'failures'):
            if not isinstance(_STATE.get(k), dict): _STATE[k] = {}
        _STATE['version'] = 2
    return _STATE

def _jsonable(value):
    if value is None or isinstance(value, (str, bool, int)): return value
    if isinstance(value, datetime): return stamp(value)
    if isinstance(value, float): return finite(value)
    if isinstance(value, dict): return {str(k):_jsonable(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)): return [_jsonable(v) for v in value]
    # Optional fundamentals can contain numpy scalars; never serialize NaN/Inf.
    return finite(value)

def _save():
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_name(_CACHE_PATH.name + '.' + str(os.getpid()) + '.tmp')
        tmp.write_text(json.dumps(_jsonable(_state()), allow_nan=False), encoding='utf-8')
        os.replace(tmp, _CACHE_PATH)
        _state().pop('cache_error', None)
    except (OSError, ValueError, TypeError):
        _state()['cache_error'] = 'PERSISTENCE_FAILED'

def _delay(value):
    try: return max(300, float(value))
    except (TypeError, ValueError):
        try: return max(300, parsedate_to_datetime(value).timestamp()-time.time())
        except (TypeError, ValueError, OverflowError): return 300

def _limit(provider, retry_after=None):
    s = _state()
    n = min(8, s['failures'].get(provider, 0) + 1)
    s['failures'][provider] = n
    s['cooldowns'][provider] = time.time() + max(_delay(retry_after), min(3600, 300*2**(n-1)))
    _save()

def _http(url, provider):
    global _LAST_REQUEST
    if _state()['cooldowns'].get(provider, 0) > time.time(): raise RateLimited('provider cooldown')
    if provider == 'yahoo':
        time.sleep(max(0, YAHOO_REQUEST_GAP-(time.monotonic()-_LAST_REQUEST)))
        _LAST_REQUEST = time.monotonic()
    # Choose ONE transport; a 429 never triggers another host, proxy or yfinance.
    try:
        from curl_cffi import requests as transport
        session = transport.Session(impersonate='chrome')
    except ImportError:
        import requests as transport
        session = transport.Session()
    session.trust_env = False
    proxy = os.getenv('RT_QUOTES_PROXY', '').strip()
    if proxy: session.proxies = {'http': proxy, 'https': proxy}
    try:
        r = session.get(url, timeout=12, headers={'User-Agent':'Mozilla/5.0'})
        if r.status_code == 429:
            _limit(provider, r.headers.get('Retry-After'))
            raise RateLimited('HTTP 429')
        r.raise_for_status()
        return r.json()
    finally: session.close()

def _market(meta, now):
    try: local = datetime.fromtimestamp(now, ZoneInfo(meta.get('exchangeTimezoneName') or 'UTC'))
    except (ValueError, KeyError): return 'UNKNOWN'
    ts = stamp(meta.get('regularMarketTime'))
    # Yahoo may advertise a synthetic current session on a weekend. A Friday
    # regularMarketTime on Sunday is a last quote, not a stalled live feed.
    # Fresh Sunday-evening futures/FX observations are allowed through below.
    if local.weekday() >= 5 and meta.get('instrumentType') != 'CRYPTOCURRENCY' and (not ts or now-ts > 3600):
        return 'CLOSED_WEEKEND'
    explicit = str(meta.get('marketState') or '').upper()
    if explicit in ('CLOSED', 'PRE', 'POST', 'REGULAR', 'PREPRE', 'POSTPOST'): return explicit
    periods = meta.get('currentTradingPeriod') or {}
    for key in ('regular', 'pre', 'post'):
        p = periods.get(key) or {}
        start = stamp(p.get('start')); end = stamp(p.get('end'))
        # Yahoo futures schedules can span the entire weekend; that is not
        # a continuous open session. Only trust bounded intraday windows.
        if start and end and 0 < end-start <= 86400 and start <= now < end:
            return {'regular':'REGULAR', 'pre':'PRE', 'post':'POST'}[key]
    try: local = datetime.fromtimestamp(now, ZoneInfo(meta.get('exchangeTimezoneName') or 'UTC'))
    except (ValueError, KeyError): return 'UNKNOWN'
    if local.weekday() >= 5 and meta.get('instrumentType') != 'CRYPTOCURRENCY': return 'CLOSED_WEEKEND'
    regular = periods.get('regular') or {}
    end = stamp(regular.get('end')); start = stamp(regular.get('start'))
    if start and end:
        # Old schedule from Friday is not evidence Monday's market is closed.
        if datetime.fromtimestamp(start, local.tzinfo).date() == local.date(): return 'CLOSED'
    return 'UNKNOWN'

def _decorate(row, now, failed=False, error=None, provider='yahoo'):
    row = dict(row)
    if provider == 'yahoo' and row.get('market_metadata'):
        row['market_state'] = _market(row['market_metadata'], now)
    ts = stamp(row.get('quote_ts'))
    row['quote_ts'] = ts
    row['quote_age_sec'] = max(0, int(now-ts)) if ts else None
    row['attempt_at'] = int(now)
    row['retry_at'] = max(_state()['cooldowns'].get(provider, 0), _state()['attempts'].get(row.get('_cache_key',''), 0)) or None
    price = finite(row.get('price'))
    row['price'] = price if price is not None and price > 0 else None
    if row['price'] is None: status = 'UNAVAILABLE'
    elif failed: status = 'STALE'
    elif provider == 'htx': status = 'RECENT' if ts and now-ts <= 60 else 'STALE'
    elif row.get('market_state','').startswith('CLOSED') or row.get('market_state') in ('POST','POSTPOST','PRE','PREPRE'):
        # A closed exchange does not excuse arbitrarily old cached observations.
        status = ('LAST_QUOTE' if now-ts <= 4*86400 else 'STALE') if ts else 'UNKNOWN_TIME'
    else:
        delay = finite(row.get('exchange_delay_sec')) or 0
        status = 'RECENT' if ts and now-ts <= max(300, delay+120) else 'LAST_QUOTE' if ts and now-ts <= 4*86400 and row.get('market_state') == 'UNKNOWN' else 'STALE'
    row['status'] = status
    row['fetch_status'] = 'FAILED' if failed else 'OK' if row['price'] is not None else 'UNAVAILABLE'
    if error: row['error'] = error
    else: row.pop('error', None)
    if _state().get('cache_error'): row['cache_error'] = _state()['cache_error']
    row.pop('_cache_key', None)
    return row

def _parse_yahoo(data, now):
    m = dict(data.get('meta') or {})
    periods = m.get('currentTradingPeriod')
    if isinstance(periods, dict):
        m['currentTradingPeriod'] = {k:{'start':stamp(v.get('start')), 'end':stamp(v.get('end'))}
                                    for k,v in periods.items() if isinstance(v,dict)}
    else: m.pop('currentTradingPeriod', None)
    m['regularMarketTime'] = stamp(m.get('regularMarketTime'))
    price = finite(m.get('regularMarketPrice'))
    if price is None or price <= 0: raise ValueError('missing provider price')
    prev = finite(m.get('previousClose'))
    if prev is None: prev = finite(m.get('chartPreviousClose'))
    delay = finite(m.get('exchangeDataDelayedBy'))
    return {'price':price, 'prev_close':prev, 'change_pct':(price-prev)/prev*100 if prev else None,
            'day_high':finite(m.get('regularMarketDayHigh')), 'day_low':finite(m.get('regularMarketDayLow')),
            'fifty_two_week_high':finite(m.get('fiftyTwoWeekHigh')), 'fifty_two_week_low':finite(m.get('fiftyTwoWeekLow')),
            'quote_ts':stamp(m.get('regularMarketTime')), 'fetched_at':int(now),
            '_source':'Yahoo Finance (may be delayed)', 'timestamp_kind':'PROVIDER_REGULAR_MARKET_TIME',
            'exchange_delay_sec':delay*60 if delay is not None else None,
            'market_state':_market(m, now), 'market_state_basis':'PROVIDER_OR_SCHEDULE_WEEKEND_HEURISTIC',
            'market_metadata':{k:m[k] for k in ('regularMarketTime','exchangeTimezoneName','instrumentType','currentTradingPeriod','marketState') if k in m},
            'currency':m.get('currency'), 'exchange_timezone':m.get('exchangeTimezoneName')}

def _yahoo(sym):
    try:
        payload = _http('https://query1.finance.yahoo.com/v8/finance/chart/'+urlquote(sym,safe='')+'?interval=1m&range=1d', 'yahoo')
        result = (payload.get('chart') or {}).get('result') or []
        if not result: raise ValueError('Yahoo chart has no result')
        return _parse_yahoo(result[0], time.time())
    except RateLimited: raise
    except Exception:
        if _state()['cooldowns'].get('yahoo', 0) > time.time(): raise RateLimited('provider cooldown')
        # Optional fallback uses public yfinance history metadata, no fake OHLC close.
        import yfinance as yf
        from curl_cffi import requests as transport
        class GuardedSession(transport.Session):
            def request(self, *args, **kwargs):
                if _state()['cooldowns'].get('yahoo', 0) > time.time():
                    raise RateLimited('provider cooldown')
                response = super().request(*args, **kwargs)
                if response.status_code == 429:
                    _limit('yahoo', response.headers.get('Retry-After'))
                    raise RateLimited('HTTP 429')
                return response
        session = GuardedSession(impersonate='chrome')
        session.trust_env = False
        proxy = os.getenv('RT_QUOTES_PROXY', '').strip()
        if proxy: session.proxies = {'http':proxy, 'https':proxy}
        try:
            t = yf.Ticker(sym, session=session)
            t.history(period='1d', interval='1m', timeout=12, raise_errors=True)
            return _parse_yahoo({'meta':t.history_metadata}, time.time())
        except Exception as exc:
            if isinstance(exc, RateLimited): raise
            if 'RateLimit' in type(exc).__name__ or '429' in str(exc):
                _limit('yahoo'); raise RateLimited('yfinance rate limited') from None
            raise RuntimeError('Yahoo chart and yfinance unavailable') from None
        finally:
            session.close()

def _best(*rows):
    good = [dict(r) for r in rows if isinstance(r, dict) and (finite(r.get('price')) or 0) > 0]
    return max(good, key=lambda r:(stamp(r.get('quote_ts')) or 0, stamp(r.get('fetched_at')) or 0)) if good else {}

def fetch_quotes(symbols, previous=None):
    """Return every requested Yahoo symbol, retaining durable last-good observations."""
    with _LOCK:
        s = _state(); out = {}; previous = previous or {}
        for sym in dict.fromkeys(symbols):
            key = 'yahoo:'+sym; now = time.time()
            old = _best(s['rows'].get(key), previous.get(sym))
            if old: s['rows'][key] = old
            failed = old.get('fetch_status') == 'FAILED'; error = old.get('error')
            if now >= max(s['attempts'].get(key,0), s['cooldowns'].get('yahoo',0)):
                s['attempts'][key] = now+YAHOO_POLL_SEC
                try:
                    fresh = _yahoo(sym)
                    old = _best(old, fresh)
                    # Older observations must never replace newer quotes.
                    failed = old != fresh
                    error = 'OLDER_PROVIDER_OBSERVATION' if failed else None
                    s['rows'][key] = old
                except Exception as exc:
                    failed = True; error = 'RATE_LIMITED' if isinstance(exc,RateLimited) else 'SOURCE_UNAVAILABLE'
            elif s['cooldowns'].get('yahoo',0) > now:
                failed = True; error = 'RATE_LIMITED'
            row = _decorate({**old, '_cache_key':key}, time.time(), failed, error)
            row.setdefault('_source','Yahoo Finance (may be delayed)')
            row.setdefault('change_pct', None)
            out[sym] = row
            if row['price'] is not None: s['rows'][key] = row
        _save()
        if s.get('cache_error'):
            for row in out.values(): row['cache_error'] = s['cache_error']
        return out

def _htx_row(t, envelope_ts, now):
    p = finite(t.get('close')); op = finite(t.get('open'))
    if p is None or p <= 0: raise ValueError('missing HTX price')
    ts = stamp(t.get('ts')) or stamp(envelope_ts)
    return {'price':p, 'change_24h':(p-op)/op*100 if op else None,
            'quote_ts':ts//1000 if ts and ts > 100000000000 else ts,
            'fetched_at':int(now), '_source':'HTX spot USDT', 'timestamp_kind':'TICKER_SNAPSHOT',
            'market_state':'CONTINUOUS', 'currency':'USDT'}

def _kucoin_bnb():
    d = _http('https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=BNB-USDT', 'kucoin')
    t = d.get('data') or {}
    p = finite(t.get('price')); ts = stamp(t.get('time'))
    if d.get('code') != '200000' or p is None or p <= 0 or not ts:
        raise ValueError('KuCoin BNB quote unavailable')
    return {'price':p, 'change_24h':None, 'quote_ts':ts//1000,
            'fetched_at':int(time.time()), '_source':'KuCoin spot BNB-USDT (HTX fallback)',
            'timestamp_kind':'EXCHANGE_LEVEL1_TIME', 'market_state':'CONTINUOUS', 'currency':'USDT'}

def htx_quotes(symbols=None):
    """Default: ALL HTX USDT spot tickers. Explicit list: exact complete coverage.
    Snapshot ts is not claimed as last-trade time. Unsupported symbols return null.
    No fabricated conversion for unavailable USDT pairs or stablecoins.
    """
    with _LOCK:
        s = _state(); now = time.time(); error = None
        wanted = list(dict.fromkeys(symbols)) if symbols is not None else None
        if now >= max(s['attempts'].get('htx:batch',0), s['cooldowns'].get('htx',0)):
            s['attempts']['htx:batch'] = now+HTX_POLL_SEC
            try:
                d = _http('https://api.huobi.pro/market/tickers', 'htx')
                if d.get('status') != 'ok' or not d.get('data'): raise ValueError('empty HTX')
                for t in d['data']:
                    pair = t.get('symbol','')
                    if not pair.endswith('usdt'): continue
                    sym = pair[:-4].upper(); key = 'htx:'+sym
                    try: row = _htx_row(t,d.get('ts'),now)
                    except ValueError: continue
                    s['rows'][key] = _best(s['rows'].get(key), row)
                s['htx_error'] = None
            except Exception as exc:
                error = 'RATE_LIMITED' if isinstance(exc, RateLimited) else 'SOURCE_UNAVAILABLE'
                s['htx_error'] = error
        error = error or s.get('htx_error')
        if s['cooldowns'].get('htx',0) > now: error = 'RATE_LIMITED'
        if wanted is None: wanted = sorted(k[4:] for k in s['rows'] if k.startswith('htx:'))
        if 'BNB' in wanted and (error or not s['rows'].get('htx:BNB')):
            if now >= max(s['attempts'].get('kucoin:BNB',0), s['cooldowns'].get('kucoin',0)):
                s['attempts']['kucoin:BNB'] = now+30
                try: s['rows']['htx:BNB'] = _best(s['rows'].get('htx:BNB'), _kucoin_bnb())
                except Exception: pass  # Last-good remains; no fabricated timestamp.
        out = {}
        for sym in wanted:
            key = 'htx:'+sym
            observed = s['rows'].get(key) or {}
            fallback_ok = sym == 'BNB' and observed.get('_source','').startswith('KuCoin') and not s['cooldowns'].get('kucoin',0) > now
            row = _decorate({**observed, '_cache_key':'htx:batch'}, time.time(), bool(error) and not fallback_ok, error if not fallback_ok else None, 'htx')
            row.setdefault('_source','HTX spot USDT'); row.setdefault('change_24h',None)
            if row['price'] is None: row['error'] = error or 'NO_HTX_USDT_SPOT_QUOTE'
            out[sym] = row
        _save()
        if s.get('cache_error'):
            for row in out.values(): row['cache_error'] = s['cache_error']
        return out
