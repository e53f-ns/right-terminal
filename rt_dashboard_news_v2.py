"""Public dashboard news, cache-first and safe to call from a worker executor.

Integration (NOT performed here): replace feeds.fetch_news with this fetch_news.
Deploy beside web_dashboard.py, or set DASHBOARD_PROJECT_DIR explicitly. Cache:
<project>/data/dashboard_news_cache.json. requests + PySocks for SOCKS are needed.
A warm call returns immediately; a cold call waits at most wait_timeout (12s).
Refreshes publish each successful source independently, BEFORE optional article
extraction. DNS/TLS can exceed requests timeouts: daemon threads never gate the
caller, and at most one thread per source/cache path can remain outstanding.
No authenticated endpoints, Reddit alternate hosts or rate-limit bypasses.
"""
import copy
import html
import json
import os
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests

GROUPS = ('crypto_news', 'macro_news', 'google_news', 'telegram', 'reddit_wsb', 'reddit_bitcoin')
UA = 'RightTradeDashboard/4.0 (public RSS reader)'
TTL = 300
MAX_BYTES = 2_000_000
MAX_TEXT = 12000
_LOCK = threading.RLock()
_REDDIT_LOCK = threading.Lock()
_STATES = {}
_ACTIVE = {}

FEEDS = (
    ('cointelegraph', 'crypto_news', 'Cointelegraph', 'https://cointelegraph.com/rss', True),
    ('decrypt', 'crypto_news', 'Decrypt', 'https://decrypt.co/feed', True),
    ('coindesk', 'crypto_news', 'CoinDesk', 'https://www.coindesk.com/arc/outboundfeeds/rss/', True),
    ('yahoo', 'macro_news', 'Yahoo Finance', 'https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,AAPL,TSLA,NVDA&region=US&lang=en-US', False),
    ('fed', 'macro_news', 'Federal Reserve', 'https://www.federalreserve.gov/feeds/press_all.xml', False),
    ('google', 'google_news', 'Google News', 'https://news.google.com/rss/search?q=stock+market+Federal+Reserve+when:2d&hl=en-US&gl=US&ceid=US:en', False),
    ('reddit_wsb', 'reddit_wsb', 'Reddit WSB', 'https://www.reddit.com/r/wallstreetbets/.rss', False),
    ('reddit_bitcoin', 'reddit_bitcoin', 'Reddit Bitcoin', 'https://www.reddit.com/r/Bitcoin/.rss', False),
)


def text(value):
    value = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', value or '', flags=re.S | re.I)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', value))).strip()


def publication_time(row):
    value = row.get('published') or row.get('created') or ''
    try:
        return parsedate_to_datetime(value).timestamp()
    except (ValueError, TypeError, OverflowError):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
        except (ValueError, TypeError, OverflowError):
            return 0


def parse_feed(xml, source):
    # Reject DTD/entity expansion, accept real RSS/Atom namespaces and CDATA.
    if re.search(br'<!\s*(DOCTYPE|ENTITY)', xml if isinstance(xml, bytes) else xml.encode(), re.I):
        raise ValueError('DTD not permitted')
    root = ET.fromstring(xml)
    rows = []
    for entry in root.iter():
        if entry.tag.split('}')[-1] not in ('item', 'entry'):
            continue
        values = {}; link = ''
        for child in entry:
            name = child.tag.split('}')[-1]
            values[name] = ''.join(child.itertext())
            if name == 'link' and child.get('rel', 'alternate') == 'alternate':
                link = child.get('href') or values[name]
        title = text(values.get('title'))
        if not title:
            continue
        raw = values.get('encoded') or values.get('content') or values.get('description') or values.get('summary') or ''
        body = text(raw)
        status = 'RSS_EXCERPT' if body and body != title else 'HEADLINE_ONLY'
        if source == 'Google News' and body:
            status = 'RSS_LINK_SUMMARY'  # Google description is often titles/links, not article prose.
        rows.append({'title': title, 'url': link.strip(), 'source': text(values.get('source')) or source,
                     'published': values.get('pubDate') or values.get('published') or values.get('updated') or '',
                     'text': body[:MAX_TEXT], 'text_status': status, 'text_partial': True,
                     'text_chars': min(len(body), MAX_TEXT), 'text_truncated': len(body) > MAX_TEXT,
                     'score': None, 'comments': None, 'currencies': [], 'kind': 'news', 'votes': {}})
        if len(rows) >= 60:
            break
    return rows


class _TelegramParser(HTMLParser):
    def __init__(self, channel):
        super().__init__(convert_charrefs=True)
        self.channel = channel; self.rows = []; self.current = None; self.depth = 0; self.parts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get('data-post') and 'tgme_widget_message' in a.get('class', '').split():
            self.current = {'channel': self.channel, 'source': 'Telegram @' + self.channel,
                            'url': 'https://t.me/' + a['data-post'], 'published': ''}
        if self.current and tag == 'time' and a.get('datetime'):
            self.current['published'] = a['datetime']
            # Telegram puts the timestamp AFTER the text div.
            for row in reversed(self.rows):
                if row['url'] == self.current['url']:
                    row['published'] = a['datetime']
                    break
        if self.depth:
            if tag == 'div': self.depth += 1
            if tag in ('br', 'p'): self.parts.append(' ')
        elif self.current and 'tgme_widget_message_text' in a.get('class', '').split():
            self.depth = 1; self.parts = []

    def handle_data(self, data):
        if self.depth: self.parts.append(data)

    def handle_endtag(self, tag):
        if self.depth and tag == 'div':
            self.depth -= 1
            if not self.depth:
                body = re.sub(r'\s+', ' ', ''.join(self.parts)).strip()
                if body:
                    row = dict(self.current)
                    row.update(text=body[:MAX_TEXT], title=body[:120], text_status='PUBLIC_POST_PARTIAL',
                               text_partial=True, text_chars=min(len(body), MAX_TEXT),
                               text_truncated=len(body) > MAX_TEXT, score=None, comments=None)
                    self.rows.append(row)


def parse_telegram(body, channel):
    parser = _TelegramParser(channel); parser.feed(body)
    return parser.rows[-5:]


def _cache_path(project_dir=None, cache_path=None):
    if cache_path: return Path(cache_path).resolve()
    base = project_dir or os.getenv('DASHBOARD_PROJECT_DIR')
    if not base:
        here = Path(__file__).resolve().parent
        base = here if (here / 'web_dashboard.py').exists() else Path.cwd()
    return Path(base).resolve() / 'data' / 'dashboard_news_cache.json'


def _state(path):
    key = str(path)
    with _LOCK:
        if key not in _STATES:
            state = {'version': 2, 'sources': {}, 'cooldowns': {}, 'cooldown_status': {}, 'cache_error': None}
            if path.exists():
                try:
                    if path.stat().st_size > 8_000_000: raise ValueError('cache too large')
                    disk = json.loads(path.read_text(encoding='utf-8'))
                    if disk.get('version') != 2 or not isinstance(disk.get('sources'), dict):
                        raise ValueError('unsupported cache schema')
                    for source, row in disk['sources'].items():
                        if not isinstance(row, dict) or not isinstance(row.get('rows', []), list):
                            raise ValueError('invalid source cache')
                        if any(not isinstance(item, dict) for item in row.get('rows', [])):
                            raise ValueError('invalid cached item')
                        for field in ('attempt_at', 'fetched_at', 'retry_at'):
                            if row.get(field) is not None and not isinstance(row[field], (int, float)):
                                raise ValueError('invalid cache timestamp')
                    cooldowns = disk.get('cooldowns', {})
                    if not isinstance(cooldowns, dict) or any(not isinstance(v, (int, float)) for v in cooldowns.values()):
                        raise ValueError('invalid cooldown cache')
                    reasons = disk.get('cooldown_status', {})
                    if not isinstance(reasons, dict): raise ValueError('invalid cooldown reasons')
                    state.update(sources=disk['sources'], cooldowns=cooldowns, cooldown_status=reasons)
                except (OSError, ValueError, TypeError, AttributeError):
                    state['cache_error'] = 'CACHE_READ_FAILED'
            _STATES[key] = state
        return _STATES[key]


def _persist(path, state):
    # Called under _LOCK. Last-good source rows are never replaced by failures.
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as f:
            tmp = f.name
            json.dump({'version': 2, 'saved_at': int(time.time()), 'sources': state['sources'],
                       'cooldowns': state['cooldowns'], 'cooldown_status': state['cooldown_status']}, f, ensure_ascii=False, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        state['cache_error'] = None
    except (OSError, ValueError, TypeError):
        state['cache_error'] = 'CACHE_WRITE_FAILED'
    finally:
        if tmp and os.path.exists(tmp):
            try: os.unlink(tmp)
            except OSError: pass


class FetchError(Exception):
    def __init__(self, code, attempts=None, retry_at=None):
        super().__init__(code)
        self.code = code; self.attempts = attempts or []; self.retry_at = retry_at


def _retry_after(value, now):
    try: delay = float(value)
    except (ValueError, TypeError):
        try: delay = parsedate_to_datetime(value).timestamp() - now
        except (ValueError, TypeError, OverflowError): delay = 3600
    # No upper clamp: never retry earlier than the server asks.
    return now + max(300, delay)


def _download(url, proxy_first, deadline, state, path, reddit=False):
    proxy = (os.getenv('NEWS_PROXY') or os.getenv('UPSCALE_PROXY') or
             ('socks5h://127.0.0.1:10808' if os.name == 'nt' else '')).strip()
    routes = [('DIRECT', '')]
    if proxy: routes = [('SOCKS', proxy), ('DIRECT', '')] if proxy_first else [('DIRECT', ''), ('SOCKS', proxy)]
    # Reddit is never retried via another route after any response indicating blocking.
    host = urlparse(url).hostname
    attempts = []
    for name, route in routes:
        with _LOCK:
            until = state['cooldowns'].get(host, 0)
        if until > time.time():
            reason = 'COOLDOWN_BLOCKED' if state['cooldown_status'].get(host) == 'BLOCKED' else 'COOLDOWN'
            raise FetchError(reason, attempts, until)
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise FetchError('TIME_BUDGET', attempts)
        try:
            with requests.Session() as session:
                session.trust_env = False  # DIRECT truly bypasses inherited HTTP(S)_PROXY.
                if route: session.proxies = {'http': route, 'https': route}
                with session.get(url, headers={'User-Agent': UA, 'Accept': 'application/rss+xml, application/atom+xml, text/html;q=0.8'},
                                 timeout=(min(5, remaining), min(7, remaining)), stream=True) as response:
                    code = response.status_code
                    attempts.append({'route': name, 'http': code})
                    if code == 429 or (reddit and code in (401, 403)):
                        until = _retry_after(response.headers.get('Retry-After'), time.time()) if code == 429 else time.time() + 1800
                        with _LOCK:
                            state['cooldowns'][host] = until
                            state['cooldown_status'][host] = 'RATE_LIMITED' if code == 429 else 'BLOCKED'
                            _persist(path, state)
                        raise FetchError('HTTP_429' if code == 429 else 'HTTP_' + str(code), attempts, until)
                    response.raise_for_status()
                    chunks = []; size = 0
                    for chunk in response.iter_content(16384):
                        if time.monotonic() > deadline: raise FetchError('TIME_BUDGET', attempts)
                        size += len(chunk)
                        if size > MAX_BYTES: raise FetchError('BODY_TOO_LARGE', attempts)
                        chunks.append(chunk)
                    return b''.join(chunks), attempts
        except FetchError:
            raise
        except requests.RequestException as exc:
            # No raw exception message: proxy URLs can contain credentials.
            if attempts and attempts[-1].get('http', 0) >= 400:
                attempts[-1]['error'] = type(exc).__name__
            else: attempts.append({'route': name, 'error': type(exc).__name__})
    raise FetchError('FETCH_FAILED', attempts)


def _article(row, deadline, state, path):
    url = row.get('url', '')
    if not url.startswith('https://'): return row
    result = dict(row)
    try:
        raw, attempts = _download(url, True, deadline, state, path)
        body = raw.decode('utf-8', errors='replace')
        candidates = []
        def visit(value):
            if isinstance(value, dict):
                if isinstance(value.get('articleBody'), str): candidates.append(value['articleBody'])
                for child in value.values(): visit(child)
            elif isinstance(value, list):
                for child in value: visit(child)
        for script in re.findall(r'<script\b[^>]*type=[\"\x27]application/ld\+json[\"\x27][^>]*>(.*?)</script>', body, re.S | re.I):
            try: visit(json.loads(script))
            except (ValueError, RecursionError): pass
        if candidates:
            article = text(max(candidates, key=len)); status = 'ARTICLE_BODY_EXTRACTED_PARTIAL'
        else:
            segments = re.findall(r'<article\b[^>]*>(.*?)</article>', body, re.S | re.I)
            paragraphs = re.findall(r'<p\b[^>]*>(.*?)</p>', max(segments, key=len) if segments else '', re.S | re.I)
            article = ' '.join(text(p) for p in paragraphs if len(text(p)) > 50)
            status = 'PARTIAL_HTML_TEXT'
        if len(article) > max(300, len(row.get('text', ''))):
            result.update(text=article[:MAX_TEXT], text_status=status, text_partial=True,
                          text_chars=min(len(article), MAX_TEXT), text_truncated=len(article) > MAX_TEXT)
        else: result['article_error'] = 'PUBLIC_BODY_NOT_EXTRACTED'
    except FetchError as exc: result['article_error'] = exc.code
    except Exception as exc: result['article_error'] = type(exc).__name__
    return result


def _jobs():
    jobs = list(FEEDS)
    channels = os.getenv('DASHBOARD_NEWS_CHANNELS', 'durov,coindesk').split(',')
    # Public usernames only, bounded work; duplicate channels do not spawn threads.
    channels = list(dict.fromkeys(c.strip().lstrip('@') for c in channels))
    for channel in channels[:8]:
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,31}', channel):
            jobs.append(('tg:' + channel, 'telegram', 'Telegram @' + channel, 'https://t.me/s/' + channel, False))
    return jobs


def _work(job, path, state):
    sid, group, label, url, proxy_first = job
    identity = (str(path), sid)
    try:
        def run():
            deadline = time.monotonic() + 20
            raw, attempts = _download(url, proxy_first, deadline, state, path, reddit=group.startswith('reddit_'))
            rows = parse_telegram(raw.decode('utf-8', errors='replace'), sid[3:]) if group == 'telegram' else parse_feed(raw, label)
            if not rows: raise FetchError('EMPTY_PARSED_FEED', attempts)
            rows.sort(key=publication_time, reverse=True); rows = rows[:20]
            now = int(time.time())
            with _LOCK:
                previous = state['sources'].get(sid, {})
                record = {'group': group, 'source': label, 'url': url, 'rows': rows,
                          'attempt_at': previous.get('attempt_at', now), 'fetched_at': now,
                          'error': None, 'upstream_status': 'LIVE', 'attempts': attempts, 'retry_at': None}
                state['sources'][sid] = record
                _persist(path, state)  # Immediate durable feed result, BEFORE article fetch.
            if group in ('crypto_news', 'macro_news'):
                enriched = _article(rows[0], time.monotonic() + 8, state, path)
                with _LOCK:
                    record['rows'][0] = enriched
                    _persist(path, state)
        if group.startswith('reddit_'):
            with _REDDIT_LOCK: run()
        else: run()
    except Exception as exc:
        with _LOCK:
            record = state['sources'].setdefault(sid, {'group': group, 'source': label, 'url': url, 'rows': []})
            code = exc.code if isinstance(exc, FetchError) else type(exc).__name__
            record.update(error=code, upstream_status=('RATE_LIMITED' if code in ('HTTP_429', 'COOLDOWN') else
                          'BLOCKED' if code in ('HTTP_401', 'HTTP_403', 'COOLDOWN_BLOCKED') else 'UNAVAILABLE'),
                          attempts=exc.attempts if isinstance(exc, FetchError) else [],
                          retry_at=exc.retry_at if isinstance(exc, FetchError) else None)
            _persist(path, state)
    finally:
        with _LOCK: _ACTIVE.pop(identity, None)


def _snapshot(path, state, jobs):
    now = time.time()
    out = {group: [] for group in GROUPS}
    out.update(statuses={}, errors={}, source_meta={}, sources={}, sentiment={'crypto': 'N/A', 'stocks': 'N/A', 'reddit': 'N/A'})
    for sid, group, label, url, _ in jobs:
        record = copy.deepcopy(state['sources'].get(sid, {}))
        rows = record.pop('rows', [])
        active = (str(path), sid) in _ACTIVE
        fetched = record.get('fetched_at')
        stale = bool(rows) and (bool(record.get('error')) or not fetched or now - fetched > TTL)
        status = 'STALE' if stale else 'LIVE' if rows else record.get('upstream_status', 'REFRESHING' if active else 'UNAVAILABLE')
        meta = {'source': label, 'url': url, 'attempt_at': record.get('attempt_at'), 'fetched_at': fetched,
                'age_sec': max(0, int(now - fetched)) if fetched else None, 'status': status,
                'upstream_status': record.get('upstream_status', status), 'error': record.get('error'),
                'retry_at': record.get('retry_at'), 'attempts': record.get('attempts', []), 'refreshing': active,
                'count': len(rows)}
        out['sources'][sid] = meta
        for row in rows:
            row.update(feed_id=sid, fetched_at=fetched, stale=stale)
        out[group].extend(rows)
        if meta['error']: out['errors'].setdefault(group, []).append(sid + ': ' + meta['error'])
    for group in GROUPS:
        metas = {sid: out['sources'][sid] for sid, g, *_ in jobs if g == group}
        seen = set(); rows = []
        for row in sorted(out[group], key=publication_time, reverse=True):
            identity = row.get('url') or row.get('title') or row.get('text')
            if identity and identity not in seen:
                seen.add(identity); rows.append(row)
        out[group] = rows[:15 if group == 'crypto_news' else 10]
        if out[group]:
            if all(r.get('stale') for r in out[group]): status = 'STALE'
            elif any(m['status'] != 'LIVE' for m in metas.values()): status = 'PARTIAL'
            else: status = 'LIVE'
        else:
            statuses = {m['status'] for m in metas.values()}
            status = next((s for s in ('RATE_LIMITED', 'BLOCKED', 'REFRESHING') if s in statuses), 'UNAVAILABLE')
        out['statuses'][group] = status
        timestamps = [m['fetched_at'] for m in metas.values() if m['fetched_at']]
        # Group newest timestamp is NOT a claim that every row is fresh.
        out['source_meta'][group] = {'status': status, 'fetched_at': max(timestamps) if timestamps else None,
                                     'sources': metas, 'count': len(out[group])}
    out['cache_meta'] = {'path': str(path), 'error': state.get('cache_error'), 'persistent': True,
                         'refreshing': any(m['refreshing'] for m in out['sources'].values()),
                         'policy': 'last-good per source; cached rows retain original fetched_at; excerpts explicitly partial'}
    return out


def fetch_news(project_dir=None, cache_path=None, wait_timeout=12, force_refresh=False):
    """Return existing dashboard groups + per-source metadata, never fabricated rows.

    Cache-first startup: persisted rows return immediately while sources refresh.
    Cold startup: wait <= wait_timeout for independently published feed results.
    force_refresh never overrides a host cooldown or starts duplicate workers.
    An empty transient response is REFRESHING/UNAVAILABLE, not LIVE. Poll again.
    """
    path = _cache_path(project_dir, cache_path); state = _state(path); jobs = _jobs()
    started = time.monotonic()
    with _LOCK:
        warm = any(state['sources'].get(sid, {}).get('rows') for sid, *_ in jobs)
        for job in jobs:
            sid, group, label, url, _ = job
            identity = (str(path), sid)
            record = state['sources'].setdefault(sid, {'group': group, 'source': label, 'url': url, 'rows': []})
            host_until = state['cooldowns'].get(urlparse(url).hostname, 0)
            if host_until > time.time():
                blocked = state['cooldown_status'].get(urlparse(url).hostname) == 'BLOCKED'
                record.update(error='COOLDOWN_BLOCKED' if blocked else 'COOLDOWN', retry_at=host_until,
                              upstream_status='BLOCKED' if blocked else 'RATE_LIMITED')
                continue
            if identity in _ACTIVE: continue
            from rt_dashboard_settings import get_int
            interval = get_int('NEWS_REFRESH_SECONDS', TTL, 60, 86400) if not record.get('error') else 60
            if not force_refresh and time.time() - (record.get('attempt_at') or 0) < interval: continue
            record['attempt_at'] = int(time.time())
            worker = threading.Thread(target=_work, args=(job, path, state), daemon=True, name='news-' + sid)
            _ACTIVE[identity] = worker; worker.start()
    if not warm:
        deadline = started + max(0, min(float(wait_timeout), 30))
        while time.monotonic() < deadline:
            with _LOCK:
                if not any((str(path), sid) in _ACTIVE for sid, *_ in jobs): break
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
    with _LOCK:
        return _snapshot(path, state, jobs)
