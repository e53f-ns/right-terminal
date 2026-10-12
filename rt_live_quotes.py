"""Shared live quotes for the dashboard: Upscale /v2/markets indexPrice (the price the venue trades on).
ONE GET per refresh, cached; read budget on Upscale is 60 GET/min per key, so TTL >= 5s and 429 back-off."""
import os, time, threading
from decimal import Decimal
from data.upscale_mirror import make_session, BASE_URL

_L = threading.Lock()
_C = {'at': 0.0, 'val': None}
_ACC = {'id': None, 'at': 0.0}
_BACK = {'until': 0.0}


def _fp9(v):
    return float(Decimal(str(v)) / Decimal(1000000000))


def _get(session, key, path):
    r = session.get(BASE_URL + path, headers={'Authorization': 'Bearer ' + key}, timeout=12)
    if r.status_code == 429:
        _BACK['until'] = time.time() + float(r.headers.get('Retry-After', '30') or 30)
        raise RuntimeError('rate limited')
    r.raise_for_status()
    return r.json()


def _account(session, key):
    if _ACC['id'] and time.time() - _ACC['at'] < 600:
        return _ACC['id']
    accs = _get(session, key, '/accounts/with-risk-status')
    accs = accs if isinstance(accs, list) else accs.get('data', accs)
    conf = os.getenv('UPSCALE_ACCOUNT_ID')
    ch = [a for a in accs if a.get('status') == 'active' and a.get('apiTrading') and (not conf or a.get('accountId') == conf)]
    if os.getenv('UPSCALE_ALLOW_LIVE', 'false').lower() != 'true':
        ch = [a for a in ch if a.get('type') == 'demo']
    if len(ch) != 1:
        raise RuntimeError('account ambiguous')
    _ACC.update(id=ch[0]['accountId'], at=time.time())
    return _ACC['id']


def snapshot(ttl=5.0):
    with _L:
        now = time.time()
        if _C['val'] is not None and now - _C['at'] < ttl:
            return _C['val']
        if now < _BACK['until']:
            return {**(_C['val'] or {'quotes': {}}), 'status': 'RATE_LIMITED'}
        key = os.getenv('UPSCALE_API_KEY', '')
        if not key:
            return {'status': 'UNAVAILABLE', 'quotes': {}}
        session = make_session()
        try:
            acc = _account(session, key)
            mk = _get(session, key, '/v2/markets?accountId=' + acc)
            mk = mk if isinstance(mk, list) else mk.get('data', mk)
            q, cls, asof = {}, {}, 0
            for m in mk:
                c = m.get('config') or {}; s = m.get('state') or {}
                if s.get('indexPrice') is None:
                    continue
                p = _fp9(s['indexPrice'])
                if p <= 0:
                    continue
                asof = max(asof, int(s.get('blockTimestamp') or 0))
                tags = [str(t).lower() for t in (c.get('tags') or [])]
                for k in {c.get('baseAsset'), c.get('name'), c.get('ticker')}:
                    if k:
                        q[k] = p
                if c.get('baseAsset'):
                    cls[c['baseAsset']] = tags
            health = None
            try:
                import json
                health = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'feed_health.json')))
            except Exception:
                pass
            val = {'status': 'LIVE', 'fetched_at': int(now), 'asof_ms': asof, 'source': 'Upscale /v2/markets indexPrice',
                   'quotes': q, 'tags': cls, 'feed_health': health}
            _C.update(at=now, val=val)
            return val
        except Exception as e:  # noqa: BLE001
            return {**(_C['val'] or {'quotes': {}}), 'status': 'STALE', 'error': type(e).__name__}
        finally:
            session.close()


# ---- Crypto tick stream: server keeps ONE public OKX WebSocket, browsers read it over SSE ----
import asyncio, json, aiohttp

_WS_URLS = ['wss://ws.okx.com:8443/ws/v5/public', 'wss://ws.okx.com/ws/v5/public']


class CryptoStream:
    def __init__(self):
        self.ticks = {}; self.ver = 0; self.want = set(); self.sent = set()
        self.task = None; self.status = 'INIT'; self.last = 0.0

    def ensure(self):
        if self.task is None or self.task.done():
            self.task = asyncio.get_event_loop().create_task(self.run())

    async def _subs(self, ws):
        while True:
            for s in sorted(self.want - self.sent):
                await ws.send_str(json.dumps({'op': 'subscribe', 'args': [{'channel': 'tickers', 'instId': s + '-USDT'}]}))
                self.sent.add(s); await asyncio.sleep(0.05)
            await asyncio.sleep(0.5)

    async def run(self):
        backoff, i = 1, 0
        while True:
            url = _WS_URLS[i % len(_WS_URLS)]; i += 1
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url, heartbeat=20, timeout=10) as ws:
                        self.sent = set(); self.status = 'LIVE'; backoff = 1
                        t = asyncio.get_event_loop().create_task(self._subs(ws))
                        try:
                            async for msg in ws:
                                if msg.type != aiohttp.WSMsgType.TEXT:
                                    continue
                                try:
                                    m = json.loads(msg.data)
                                    for d in m.get('data') or []:
                                        p = float(d['last']); sym = str(d['instId']).replace('-USDT', '')
                                        if p > 0:
                                            self.ticks[sym] = p; self.ver += 1; self.last = time.time()
                                except Exception:
                                    pass
                        finally:
                            t.cancel()
            except Exception:
                pass
            self.status = 'RECONNECTING'
            await asyncio.sleep(min(backoff, 30)); backoff *= 2


STREAM = CryptoStream()
