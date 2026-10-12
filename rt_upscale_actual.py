"""Authoritative Upscale GET-only account, position and execution history view."""
import os,time,json,hashlib,threading
from pathlib import Path
from decimal import Decimal
from data.upscale_mirror import make_session,BASE_URL
from rt_upscale_readonly import ReadOnlyVenue,fp9,identity,SyncFault
_LOCK=threading.Lock();_CACHE=None;_AT=0.;_EVENTS={};_HISTORY=None;_HAT=0.

def n(v):return float(fp9(v)) if v is not None else None

def asset_class(tags,category=None):
 t={str(x).lower() for x in (tags or [])}
 if t&{'forex','fx','currency','currencies'}:return 'forex'
 if t&{'commodities','commodity','metals','metal','gold','energy','oil'}:return 'forex'
 if t&{'stocks','stock','equities','equity','etf','indices','index'}:return 'stock'
 if 'crypto' in t or category=='crypto':return 'crypto'
 return 'stock' if category=='rwa' else 'crypto'

def _spark_points(rows,limit=96):
 pts=[]
 for r in rows or []:
  try:pts.append({'t':int(r['timestamp']),'equity':n(r['volume'])})
  except Exception:continue
 pts.sort(key=lambda x:x['t'])
 if len(pts)>limit:
  step=len(pts)/limit;pts=[pts[int(i*step)] for i in range(limit-1)]+[pts[-1]]
 return pts

def execution_price(event):
 base=event.get('exchangedBase');quote=event.get('exchangedQuote')
 if base is None or quote is None:return None
 b=Decimal(fp9(base));q=Decimal(fp9(quote))
 return float(abs(q/b)) if b else None

def event_row(p,events,symbol):
 opens=[e for e in events if e.get('eventName')=='increasePosition' and e.get('version')==1 and (e.get('order') or {}).get('status')=='executed']
 start=opens[0] if len(opens)==1 else {};o=start.get('order') or {}
 closes=[e for e in events if e.get('eventName') in ('closePosition','forceClose','liquidate') and e.get('closedAt')==p.get('closedAt')]
 final=max(closes,key=lambda e:e.get('version',-1)) if closes else {};end=final.get('order') or {}
 if end.get('status')!='executed':end={}
 leverage=n(o.get('leverage'))
 return {'key':hashlib.sha256(identity(p).encode()).hexdigest()[:24],'symbol':symbol,'direction':p.get('direction'),'status':p.get('status'),
 'opened_at':o.get('createdAt') or start.get('lastUpdatedAt'),'closed_at':p.get('closedAt'),
 'entry':execution_price(start),'entry_source':'executed initial event exchangedQuote / exchangedBase',
 'exit':execution_price(final) if end else None,'exit_source':'executed closing event exchangedQuote / exchangedBase' if end else None,
 'leverage':leverage,'leverage_source':'executed initial order.leverage' if leverage is not None else None,
 'size':n(p.get('size')) if p.get('status')=='opened' else n(start.get('size')),
 'margin':n(p.get('margin')) if p.get('status')=='opened' else n(start.get('margin')),
 'notional':n(p.get('notional')) if p.get('status')=='opened' else n(start.get('notional')),
 'reported_pnl':n(p.get('pnl')) if p.get('status')!='opened' else None,
 'fee':n(p.get('fee')),'funding':n(p.get('funding')),
 'close_order_type':end.get('type') or p.get('closeReason'),
 'pnl_note':'Exchange reported pnl, fees and signed funding separately. Not a synthesized net PnL. Open-event pnl is not current unrealized PnL.'}

def fetch(force=False):
 global _CACHE,_AT,_EVENTS,_HISTORY,_HAT
 with _LOCK:
  if not force and _CACHE is not None and time.monotonic()-_AT<30:return dict(_CACHE)
  session=make_session()
  try:
   # Unique account selection, never first account silently.
   key=os.environ['UPSCALE_API_KEY'];probe=ReadOnlyVenue(session,BASE_URL,key,'unused')
   accounts=probe.get('/accounts/with-risk-status');configured=os.getenv('UPSCALE_ACCOUNT_ID')
   choices=[a for a in accounts if a.get('status')=='active' and a.get('apiTrading') and (not configured or a.get('accountId')==configured)]
   if os.getenv('UPSCALE_ALLOW_LIVE','false').lower()!='true':choices=[a for a in choices if a.get('type')=='demo']
   if len(choices)!=1:raise SyncFault('Account selection ambiguous')
   a=choices[0];venue=ReadOnlyVenue(session,BASE_URL,key,a['accountId'])
   account_path=a['accountId'];markets=venue.get('/v2/markets?accountId='+account_path);names={m['id']:m['config']['baseAsset'] for m in markets}
   meta={m['config']['baseAsset']:{'asset_class':asset_class(m['config'].get('tags'),m['config'].get('category')),'mark':n((m.get('state') or {}).get('indexPrice'))} for m in markets}
   active=venue.active();orders=venue.orders();risk=venue.get('/accounts/'+account_path+'/risk-status');risk=risk.get('riskStatus',risk)
   now=time.monotonic()
   cached_open_ids={identity(p) for p in (_HISTORY or {}).get('rows',[]) if p.get('status')=='opened'}
   active_ids={identity(p) for p in active}
   if _HISTORY is None or now-_HAT>300 or cached_open_ids!=active_ids:
    hist=[];offset=0;total=None
    while True:
     d=venue.get('/positions/'+account_path+'/portfolio/history?limit=100&offset='+str(offset))
     if type(d.get('totalCount')) is not int:raise SyncFault('Missing history count')
     if total is None:total=d['totalCount']
     if total!=d['totalCount']:raise SyncFault('History changed during paging')
     page=d.get('data');
     if not isinstance(page,list):raise SyncFault('Missing history page')
     hist.extend(page);offset+=len(page)
     if offset>=total:break
     if not page or offset>10000:raise SyncFault('Incomplete history')
    if len(hist)!=total or len({identity(p) for p in hist})!=total:raise SyncFault('History count mismatch')
    _HISTORY={'rows':hist,'total':total,'at':int(time.time()),'account':a['accountId']};_HAT=now
   if _HISTORY['account']!=a['accountId']:raise SyncFault('History account changed')
   allrows={identity(p):p for p in _HISTORY['rows']};allrows.update({identity(p):p for p in active})
   normalized=[]
   for pid,p in allrows.items():
    if p.get('trader')!=a['accountId']:raise SyncFault('Position account mismatch')
    if p.get('market') not in names:raise SyncFault('Unknown historical symbol')
    cached=_EVENTS.get(pid)
    if not cached or cached['version']!=p.get('version'):
     events=venue.events(pid)
     if any(identity(e)!=pid for e in events):raise SyncFault('Event identity mismatch')
     cached={'version':p.get('version'),'events':events};_EVENTS[pid]=cached
    normalized.append(event_row(p,cached['events'],names[p['market']]))
   positions=[]
   active_keys={hashlib.sha256(pid.encode()).hexdigest()[:24] for pid in active_ids}
   for p in normalized:
    if p['status']!='opened' or p['key'] not in active_keys:continue
    related=[o for o in orders if o.get('positionId') in (next(pid for pid,raw in allrows.items() if hashlib.sha256(pid.encode()).hexdigest()[:24]==p['key']),[next(pid for pid,raw in allrows.items() if hashlib.sha256(pid.encode()).hexdigest()[:24]==p['key'])]) and o.get('status')=='active']
    safe=[{'type':o.get('type'),'trigger_price':n(o.get('triggerPrice')),'size':n(o.get('amount'))} for o in related if o.get('type') in ('stop','take')]
    mt=meta.get(p['symbol'],{});mark=mt.get('mark');upnl=None
    if isinstance(mark,float) and isinstance(p.get('entry'),float) and isinstance(p.get('size'),float):upnl=(mark-p['entry'])*p['size']
    positions.append({**p,'protective_orders':safe,'asset_class':mt.get('asset_class','crypto'),'mark':mark,'unrealized_pnl':upnl,'unrealized_note':'(mark index price - entry) x signed size; before fees/spread/funding'})
   try:eq_hist=_spark_points(venue.get('/accounts/'+account_path+'/equity-history'))
   except Exception:eq_hist=None
   try:
    st=venue.get('/accounts/'+account_path+'/stats');stats={k:n(st.get(k)) for k in ('sumPnl','sumFee','sumFunding','profit','avgLoss','avgProfit','maxLoss','maxProfit') if k in st};stats.update({k:st.get(k) for k in ('positionsCount','profitablePositionsCount','unprofitablePositionsCount','winratePercent') if k in st})
   except Exception:stats=None
   for p in normalized:p['asset_class']=meta.get(p['symbol'],{}).get('asset_class','crypto')
   closed=sorted([p for p in normalized if p['status'] in ('closed','liquidated')],key=lambda p:p.get('closed_at') or '',reverse=True)
   value={'status':'LIVE','fetched_at':int(time.time()),'source':'Upscale GET execution events + paginated portfolio history',
    'account':{'type':a.get('type'),'phase':a.get('currentPhase'),'free_balance':n(a.get('accountBalance')),'equity':n(risk.get('currentEquity')),'day_start_equity':n(a.get('dayStartEquity'))},
    'equity_history':eq_hist,'stats':stats,'unrealized_total':(sum(x['unrealized_pnl'] for x in positions if isinstance(x.get('unrealized_pnl'),float)) if positions else 0.0),'positions':positions,'active_count':len(positions),'history':closed,
    'history_meta':{'total_expected':_HISTORY['total'],'collected':len(_HISTORY['rows']),'closed_count':len(closed),'complete':True,'scope':'selected account; default portfolio/history API scope (may be current phase only)','fetched_at':_HISTORY['at']},'orders_sent_by_snapshot':0}
   _CACHE=value;_AT=time.monotonic();return dict(value)
  except Exception as e:
   if _CACHE:return {**_CACHE,'status':'STALE','error':type(e).__name__}
   return {'status':'UNAVAILABLE','error':type(e).__name__,'positions_verified':False,'orders_sent_by_snapshot':0}
  finally:session.close()
