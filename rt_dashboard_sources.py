"""Read-only dashboard feeds. Explicit routing, timestamps and stale cache."""
import concurrent.futures as cf
import html
import math
import os
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import json
from email.utils import parsedate_to_datetime
from datetime import datetime
import requests

_lock = threading.Lock()
_cache = {}
_cooldowns = {}
UA = 'RightTradeDashboard/3.6 (public RSS reader)'

def get(url, proxy_first=False, timeout=6):
    proxy = (os.getenv('NEWS_PROXY') or os.getenv('UPSCALE_PROXY') or '').strip()
    routes = [proxy, ''] if proxy_first and proxy else ['', proxy] if proxy else ['']
    error = None
    for route in dict.fromkeys(routes):
        with _lock:
            if _cooldowns.get((url, route), 0) > time.time():
                continue
        try:
            with requests.Session() as s:
                s.trust_env = False
                if route:
                    s.proxies = {'http': route, 'https': route}
                r = s.get(url, headers={'User-Agent': UA}, timeout=(4, timeout))
                if r.status_code == 429:
                    try: delay = max(300, min(3600, int(r.headers.get('Retry-After', 300))))
                    except ValueError: delay = 300
                    with _lock: _cooldowns[(url, route)] = time.time() + delay
                    # Respect rate limiting; do not retry via another route.
                    r.raise_for_status()
                    break
                r.raise_for_status()
                return r
        except requests.RequestException as e:
            error = type(e).__name__ + (' HTTP ' + str(e.response.status_code) if e.response is not None else '')
            if getattr(e, 'response', None) is not None and e.response.status_code == 429:
                break
    raise RuntimeError(error or 'source cooldown')

def text(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]*>', ' ', s or ''))).strip()

def parse_feed(xml, source):
    root = ET.fromstring(xml)
    rows = []
    for el in root.iter():
        if el.tag.split('}')[-1] not in ('item', 'entry'): continue
        vals = {}
        for c in el:
            name = c.tag.split('}')[-1]
            vals[name] = ''.join(c.itertext())
            if name == 'link': vals['link'] = c.get('href') or vals[name]
        title = text(vals.get('title'))
        if not title: continue
        rows.append({'title': title, 'url': vals.get('link', ''), 'source': text(vals.get('source')) or source,
                     'published': vals.get('pubDate') or vals.get('updated') or vals.get('published', ''),
                     'text': text(vals.get('encoded') or vals.get('content') or vals.get('description') or vals.get('summary')),
                     'text_status': 'RSS_EXCERPT', 'score': None, 'comments': None})
    return rows

def enrich_article(item):
    """Best-effort public article body; explicitly mark partial/unavailable text."""
    row=dict(item)
    try:
        url=row.get('url','')
        if not url.startswith('https://'): return row
        body=get(url,proxy_first=True).text
        def bodies(v):
            if isinstance(v,dict):
                if v.get('articleBody'):yield str(v['articleBody'])
                for x in v.values():yield from bodies(x)
            elif isinstance(v,list):
                for x in v:yield from bodies(x)
        candidates=[]
        for m in re.finditer(r'<script[^>]*type=[\"\x27]application/ld\+json[\"\x27][^>]*>(.*?)</script>',body,re.S|re.I):
            try:candidates.extend(bodies(json.loads(m.group(1))))
            except ValueError:pass
        if candidates:
            article=max(candidates,key=len);status='ARTICLE_BODY_EXTRACTED'
        else:
            segments=re.findall(r'<article\b[^>]*>(.*?)</article>',body,re.S|re.I)
            paragraphs=re.findall(r'<p\b[^>]*>(.*?)</p>',max(segments,key=len) if segments else '',re.S|re.I)
            article='\n'.join(text(p) for p in paragraphs if len(text(p))>50);status='PARTIAL_HTML_TEXT'
        article=text(article)
        if len(article)>300:
            row.update(text=article[:12000],text_status=status,text_chars=len(article),text_truncated=len(article)>12000)
        else:row['text_status']='RSS_EXCERPT_ARTICLE_UNAVAILABLE'
    except Exception:row['text_status']='RSS_EXCERPT_ARTICLE_UNAVAILABLE'
    return row

def publication_time(row):
    value=row.get('published','')
    try:return parsedate_to_datetime(value).timestamp()
    except (ValueError,TypeError):
        try:return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
        except (ValueError,TypeError):return 0

def fetch_news():
    feeds = [
      ('crypto_news','Cointelegraph','https://cointelegraph.com/rss',True),
      ('crypto_news','Decrypt','https://decrypt.co/feed',True),
      ('crypto_news','CoinDesk','https://www.coindesk.com/arc/outboundfeeds/rss/',True),
      ('macro_news','Yahoo Finance','https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,AAPL,TSLA,NVDA&region=US&lang=en-US',False),
      ('google_news','Google News','https://news.google.com/rss/search?q=stock+market+Federal+Reserve+when:2d&hl=en-US&gl=US&ceid=US:en',False),
      ('reddit_wsb','Reddit WSB','https://www.reddit.com/r/wallstreetbets/.rss',True),
      ('reddit_bitcoin','Reddit Bitcoin','https://www.reddit.com/r/Bitcoin/.rss',True),
    ]
    groups = ['crypto_news','macro_news','google_news','reddit_wsb','reddit_bitcoin','telegram']
    out = {k: [] for k in groups}; out['statuses'] = {};out['errors'] = {};out['source_meta'] = {}
    def feed(job):
        key, source, url, prox = job
        try:
            rows = parse_feed(get(url, prox).text, source)
            if not rows: raise RuntimeError('empty feed')
            return key, rows, None
        except Exception as e: return key, [], str(e)[:100]
    with cf.ThreadPoolExecutor(max_workers=5) as pool:
        for key, rows, err in pool.map(feed, feeds):
            out[key].extend(rows)
            if err: out['errors'].setdefault(key, []).append(err)
    channels = [c.strip().lstrip('@') for c in os.getenv('DASHBOARD_NEWS_CHANNELS','durov').split(',') if c.strip()]
    def tg(ch):
        try:
            body = get('https://t.me/s/'+ch).text
            msgs = re.findall(r'<div class="tgme_widget_message_text[^\"]*"[^>]*>(.*?)</div>',body,re.S)
            return [{'channel':ch,'text':text(m),'text_status':'PUBLIC_POST'} for m in msgs[-3:] if text(m)]
        except Exception as e:
            return []
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        for rows in pool.map(tg, channels): out['telegram'].extend(rows)
    for key in groups:
        rows = out[key]
        seen = set(); rows = [r for r in rows if not ((r.get('title') or r.get('text')) in seen or seen.add(r.get('title') or r.get('text')))]
        rows = sorted(rows,key=publication_time,reverse=True)[:15 if key=='crypto_news' else 10]
        now = int(time.time())
        with _lock:
            old = _cache.get(key)
            if rows: _cache[key] = (rows,now)
        if rows:
            out['statuses'][key] = 'PARTIAL' if out['errors'].get(key) else 'LIVE'
            out['source_meta'][key] = {'fetched_at':now,'status':out['statuses'][key]}
        elif old:
            rows = old[0]; out['statuses'][key]='STALE';out['source_meta'][key]={'fetched_at':old[1],'status':'STALE'}
        else:
            out['statuses'][key]='UNAVAILABLE';out['source_meta'][key]={'fetched_at':None,'status':'UNAVAILABLE'}
        out[key] = rows
    # Enrich a bounded set of latest public articles, not just titles.
    for key in ['crypto_news','macro_news']:
        with cf.ThreadPoolExecutor(max_workers=3) as pool:
            out[key][:3]=list(pool.map(enrich_article,out[key][:3]))
    out['sentiment'] = {k:'N/A' for k in ['crypto','stocks','reddit']}
    return out

def finite(v):
    try: v=float(v);return v if math.isfinite(v) else None
    except (TypeError,ValueError): return None

def quote(sym):
    url='https://query1.finance.yahoo.com/v8/finance/chart/'+urllib.parse.quote(sym,safe='')+'?interval=1m&range=1d'
    try:
        data=get(url).json()['chart']['result'][0]
    except Exception:
        # Standard yfinance transport is a fallback, not a fabricated price.
        import yfinance as yf
        ticker=yf.Ticker(sym)
        ticker.history(period='5d',interval='1d',timeout=8)
        m=ticker.history_metadata
        if not m.get('regularMarketPrice'):raise ValueError('Yahoo quote unavailable')
        data={'meta':m}
    m=data['meta']
    price=finite(m.get('regularMarketPrice'));prev=finite(m.get('chartPreviousClose') or m.get('previousClose'))
    if not price: raise ValueError('missing price')
    ts=m.get('regularMarketTime');now=int(time.time())
    # A fast poll does not remove exchange delay. Label Yahoo explicitly.
    return {'price':price,'prev_close':prev,'change_pct':(price-prev)/prev*100 if prev else None,
      'day_high':finite(m.get('regularMarketDayHigh')), 'day_low':finite(m.get('regularMarketDayLow')),
      'fifty_two_week_high':finite(m.get('fiftyTwoWeekHigh')),'fifty_two_week_low':finite(m.get('fiftyTwoWeekLow')),
      'quote_ts':ts,'fetched_at':now,'_source':'Yahoo Finance (may be delayed)',
      'quote_age_sec':max(0,now-int(ts)) if ts else None,
      'status':'LAST_QUOTE' if ts and now-ts>300 else 'RECENT',
      'exchange_delay_sec':m.get('exchangeDataDelayedBy'), 'market_state':m.get('marketState','UNKNOWN')}

def fetch_quotes(symbols, previous=None):
    previous=previous or {};out={}
    def one(sym):
        old=previous.get(sym) or {}
        try: return sym,{**old,**quote(sym)}
        except Exception:
            if old.get('price') is not None: return sym,{**old,'status':'STALE'}
            return sym,{'price':None,'change_pct':None,'status':'UNAVAILABLE','_source':'Yahoo Finance'}
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        for sym,row in pool.map(one,symbols):out[sym]=row
    return out

def htx_quotes(symbols=None):
    symbols=symbols or ['BTC','ETH','SOL','BNB','XRP']
    try:
        d=get('https://api.huobi.pro/market/tickers',timeout=10).json()
        if d.get('status')!='ok':raise ValueError('HTX unavailable')
        ts=int(d.get('ts',0)//1000)
        out={}
        for t in d['data']:
            if not t['symbol'].endswith('usdt'):continue
            price=finite(t.get('close'));op=finite(t.get('open'))
            if not price:continue
            out[t['symbol'][:-4].upper()]={'price':price,'change_24h':(price-op)/op*100 if op else None,
                'quote_ts':ts,'fetched_at':int(time.time()),'_source':'HTX spot USDT','status':'RECENT'}
        return out
    except Exception:
        def one(sym):
            try:
                d=get('https://api.huobi.pro/market/detail/merged?symbol='+sym.lower()+'usdt').json()
                if d.get('status')!='ok':return sym,None
                t=d['tick'];p=finite(t.get('close'));op=finite(t.get('open'))
                return sym,{'price':p,'change_24h':(p-op)/op*100 if op and p else None,
                  'quote_ts':int(d['ts']//1000),'fetched_at':int(time.time()),'_source':'HTX spot USDT','status':'RECENT'}
            except Exception:return sym,None
        with cf.ThreadPoolExecutor(max_workers=5) as pool:
            out={k:v for k,v in pool.map(one,symbols) if v and v['price']}
        if not out:raise RuntimeError('HTX tickers unavailable')
        return out

def funding():
    out={}
    for sym in ['BTC','ETH','SOL','BNB','XRP']:
        try:
            d=get('https://api.hbdm.com/linear-swap-api/v1/swap_funding_rate?contract_code='+sym+'-USDT').json()
            rate=finite((d.get('data') or {}).get('funding_rate'))
            if rate is not None: out[sym]=rate
        except Exception:pass
    if not out: raise RuntimeError('HTX funding unavailable')
    return out
