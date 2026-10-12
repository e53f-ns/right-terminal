"""Independent, persistent read-only feeds. Never submits orders."""
import asyncio
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import time
import rt_dashboard_sources as feeds
import rt_dashboard_news_v2 as newsfeeds
import rt_quotes_v2 as quotes


def derive(w,d):
    d['agent']=w.read_agent_data()
    d['cb_rates']=[{'name':n,'short':s,'rate':r,'stance':st} for n,s,r,st in w.CB_RATES]
    for key,attr in [('corr','CORR_MATRIX'),('company_data','COMPANY_DATA'),('company_connections','COMPANY_CONNECTIONS'),('moex_stocks','MOEX_STOCKS'),('intl_indices','INTL_INDICES'),('commodities_map','COMMODITIES'),('carry_trades','CARRY_TRADES'),('cb_meetings','CB_MEETINGS'),('fed_futures','FED_FUTURES')]:d[key]=getattr(w,attr)
    moex=d.get('moex') or {}
    if moex.get('stocks'):d['stocks']=combine(d.get('stocks') or {},moex['stocks'])
    d['moex_bonds']=moex.get('bonds') or []
    d['currency_strength']=w.calc_currency_strength(d.get('forex') or {})
    macro=d.get('macro') or {}
    d['global_yields']=[dict(y) for y in w.GLOBAL_YIELDS];d['global_yields'][0]['yield_10y']=(macro.get('us10y') or {}).get('value')
    d['yield_curve_inverted']=macro.get('yield_curve_inverted',False)
    d['recession']=w.calc_recession_prob(macro) if macro else {'probability':None,'level':'N/A','reasons':[]}
    d['bond_etfs']={s:(d.get('stocks') or {}).get(s,{}) for s in w.BOND_ETFS}
    d['reference_data']={'cb_rates':'MANUAL_REFERENCE','cb_meetings':'MANUAL_REFERENCE','carry_trades':'MANUAL_REFERENCE','fed_futures':'MANUAL_REFERENCE','corr':'MANUAL_REFERENCE','global_yields':'MIXED: US FRED, other yields manual','nuclear_index':'MANUAL_ESTIMATE'}
    from rt_dashboard_settings import get_int
    d['quote_policy']={'crypto_poll_sec':15,'yahoo_poll_sec':120,'news_poll_sec':get_int('NEWS_REFRESH_SECONDS',300,60,86400),'note':'HTX snapshot polling, not tick stream. Yahoo may be delayed. Closed markets retain LAST_QUOTE with provider timestamp. Missing sources retain last-good with honest status.'}
    return d


_PRICE_FIELDS=('price','prev_close','change_pct','change_24h','quote_ts','fetched_at','_source','status','fetch_status','error','day_high','day_low','market_state','quote_age_sec','exchange_delay_sec','timestamp_kind','market_metadata')
def combine(old,fresh):
    out={s:dict(r) for s,r in old.items()}
    for sym,after in fresh.items():
        before=old.get(sym) or {};row={**before,**after}
        if before.get('price') is not None and (after.get('price') is None or (before.get('quote_ts') or 0)>(after.get('quote_ts') or 0)):
            row.update({k:before[k] for k in _PRICE_FIELDS if k in before})
            if after.get('price') is None and after.get('status') in ('STALE','UNAVAILABLE'):
                row.update(status='LAST_QUOTE' if before.get('market_state') in ('CLOSED','CLOSED_WEEKEND','POST','PRE') else 'STALE',fetch_status='FAILED',error=after.get('error'))
        out[sym]=row
    return out


def quote_health(rows):
    statuses=[r.get('status','UNAVAILABLE') for r in rows.values()]
    if not statuses:return 'EMPTY'
    if all(s in ('LIVE','RECENT') for s in statuses):return 'LIVE'
    if all(s=='LAST_QUOTE' for s in statuses):return 'LAST_QUOTE'
    if not any(r.get('price') is not None for r in rows.values()):return 'UNAVAILABLE'
    return 'PARTIAL'

async def run(w):
    loop=asyncio.get_running_loop();root=Path(w.__file__).resolve().parent
    snapshot=root/'data/dashboard_view_cache.json'
    try:
        initial=json.loads(snapshot.read_text(encoding='utf-8'));initial['source_meta']={};initial['api_status']={};w.cache.update(derive(w,initial))
    except (OSError,ValueError):pass
    quotes._CACHE_PATH=root/'data/dashboard_yahoo_quotes.json'
    # Separate module state/lock/cache: a slow Yahoo request never gates HTX.
    spec=importlib.util.spec_from_file_location('rt_crypto_quotes_isolated',root/'rt_quotes_v2.py')
    crypto=importlib.util.module_from_spec(spec);spec.loader.exec_module(crypto)
    crypto._CACHE_PATH=root/'data/dashboard_crypto_quotes.json'
    stock_syms=list(dict.fromkeys(['^GSPC','^NDX','^DJI','^VIX','^RUT','^N225','^GDAXI','^FTSE','^HSI','000001.SS','XLK','XLF','XLE','XLV','XLI','XLB','XLRE','XLC','XLY','XLP','XLU','GC=F','CL=F','SI=F','BZ=F','NG=F','HG=F','ZW=F','ZC=F',*w.COMPANY_DATA.keys(),*w.BOND_ETFS]))
    forex_syms=list(dict.fromkeys([*w.FX_PAIRS,'DX-Y.NYB']))
    agent_pairs=list((w.cache.get().get('agent') or {}).get('watchlist') or [])
    crypto_syms=list(dict.fromkeys(['BTC','ETH','SOL','BNB','XRP','ADA','AVAX','DOT','LINK','UNI','ATOM','LTC','BCH','NEAR','APT','ARB','OP','FIL','DOGE','SUI','MATIC',*(w.cache.get().get('crypto') or {}).keys(),*[p.split('/')[0] for p in agent_pairs]]))
    quote_pool=concurrent.futures.ThreadPoolExecutor(max_workers=2,thread_name_prefix='dashboard-yahoo')
    crypto_pool=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='dashboard-crypto')
    def publish(key,value,status,error=None):
        d=w.cache.get();d['api_status']=dict(d.get('api_status') or {});d['source_meta']=dict(d.get('source_meta') or {})
        meta=dict(d['source_meta'].get(key) or {});meta.update(attempt_at=int(time.time()),status=status)
        if error:meta['error']=error
        else:meta.pop('error',None)
        if value is not None:
            if key in ('stocks','forex','crypto'):value=combine(d.get(key) or {},value)
            if key=='stocks_fundamentals':
                existing=d.get('stocks') or {};d['stocks']={s:{**r,**value.get(s,{}),**{k:r[k] for k in _PRICE_FIELDS if k in r}} for s,r in existing.items()}
                for s,r in value.items():d['stocks'].setdefault(s,r)
            elif key=='crypto_metadata':
                existing=d.get('crypto') or {};d['crypto']={s:{**value.get(s,{}),**r} for s,r in existing.items()}
                for s,r in value.items():d['crypto'].setdefault(s,r)
            else:d[key]=value
            meta['returned_at']=int(time.time())
            if isinstance(value,dict) and key in ('stocks','forex','crypto'):
                expected=stock_syms if key=='stocks' else forex_syms if key=='forex' else crypto_syms
                meta.update(expected=len(expected),received=sum(s in value for s in expected),priced=sum(value.get(s,{}).get('price') is not None for s in expected))
                success=max((r.get('fetched_at') or 0 for r in value.values()),default=0)
                if success:meta['fetched_at']=success
            else:meta['fetched_at']=(value.get('fetched_at') or 0) if key=='upscale_venue' and isinstance(value,dict) else int(time.time())
        d['source_meta'][key]=meta;d['api_status'][key]=status;w.cache.update(derive(w,d))
    async def source(key,fn,sec):
        while True:
            start=time.monotonic();status='ERR'
            try:
                value=await loop.run_in_executor(None,fn)
                status='LIVE' if value else 'EMPTY'
                if isinstance(value,dict) and value.get('statuses'):status='LIVE' if all(s=='LIVE' for s in value['statuses'].values()) else 'PARTIAL'
                if key=='news' and not any(value.get(k) for k in newsfeeds.GROUPS):status='EMPTY'
                if key=='rsi':status='LIVE' if all(v is not None for v in value.values()) else 'PARTIAL' if any(v is not None for v in value.values()) else 'EMPTY'
                if key=='crucix' and isinstance(value,dict) and not value.get('connected'):status='UNAVAILABLE'
                if key=='upscale_venue' and isinstance(value,dict):status=value.get('status','UNAVAILABLE')
                if key in ('claude_news','claude_macro') and value is None and w.cache.get().get(key):
                    publish(key,None,'STALE');await asyncio.sleep(60);continue
                publish(key,value,status);w.logger.info('independent %s %s %.1fs',key,status,time.monotonic()-start)
            except Exception as exc:publish(key,None,'STALE' if w.cache.get().get(key) else 'ERR',type(exc).__name__)
            await asyncio.sleep(max(5,sec-(time.monotonic()-start)))
    def cached_yahoo(symbols):
        state=quotes._state();return {s:quotes._decorate({**(state['rows'].get('yahoo:'+s) or {}),'_cache_key':'yahoo:'+s},time.time()) for s in symbols}
    async def yahoo_loop(key,symbols):
        cached=cached_yahoo(symbols);publish(key,cached,quote_health(cached))
        while True:
            for i in range(0,len(symbols),6):
                batch=symbols[i:i+6]
                rows=await loop.run_in_executor(quote_pool,lambda:quotes.fetch_quotes(batch,w.cache.get().get(key)))
                full=combine(w.cache.get().get(key) or {},rows);publish(key,full,quote_health({s:full.get(s,{}) for s in symbols}))
                await asyncio.sleep(.2)
            await asyncio.sleep(120)
    async def crypto_loop():
        while True:
            rows=await loop.run_in_executor(crypto_pool,lambda:crypto.htx_quotes(crypto_syms))
            publish('crypto',rows,quote_health(rows));await asyncio.sleep(15)
    def ai_news():
        d=w.cache.get();news=d.get('news') or {}
        if not any(news.get(k) for k in ['crypto_news','macro_news','google_news']):return None
        result=w.news_analysis(news,d)
        if result:result.update(timestamp=int(time.time()),coverage='Public partial article text / RSS excerpts / headlines; each item marked; no claim that all full articles were read')
        return result
    async def persist():
        keys=('stocks','forex','crypto','news','macro','funding','rsi','global','fear_greed','defi_tvl','polymarket','earnings_calendar','claude_news','claude_macro')
        def save():
            d=w.cache.get();payload={k:d[k] for k in keys if k in d};snapshot.parent.mkdir(parents=True,exist_ok=True)
            tmp=snapshot.with_suffix('.tmp');tmp.write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False,default=str),encoding='utf-8');tmp.replace(snapshot)
        while True:
            try:await loop.run_in_executor(None,save)
            except Exception as exc:w.logger.warning('view persistence: %s',type(exc).__name__)
            await asyncio.sleep(30)
    from rt_dashboard_venue import venue_snapshot
    publish('agent',w.read_agent_data(),'LIVE')
    specs=[('upscale_venue',venue_snapshot,15),('crypto_metadata',w.fetch_crypto,600),('global',w.fetch_global,300),('fear_greed',w.fetch_fear_greed,600),('funding',feeds.funding,300),('rsi',w.fetch_rsi,300),('macro',w.fetch_macro,600),('news',lambda:newsfeeds.fetch_news(project_dir=root,wait_timeout=0),20),('geo',w.fetch_geo,600),('crucix',w.fetch_crucix,600),('defi_tvl',w.fetch_defi_tvl,600),('moex',w.fetch_moex,120),('polymarket',w.fetch_polymarket,60),('earnings_calendar',w.fetch_earnings_calendar,3600),('agent',w.read_agent_data,15),('claude_news',ai_news,600),('claude_macro',lambda:w.market_macro_analysis(w.cache.get()),600),('stocks_fundamentals',lambda:w.fetch_yf(list(w.COMPANY_DATA),list(w.COMPANY_DATA)),3600)]
    await asyncio.gather(*(source(k,fn,sec) for k,fn,sec in specs),yahoo_loop('stocks',stock_syms),yahoo_loop('forex',forex_syms),crypto_loop(),persist())
