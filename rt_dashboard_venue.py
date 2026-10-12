"""GET-only public Upscale snapshot for authenticated RT dashboard chat."""
import os,time,threading
from decimal import Decimal

_cache={'at':0.0,'value':None}
_lock=threading.Lock()

def _money(value):
    return float(Decimal(str(value))/Decimal(1000000000)) if value is not None else None

def _rows(value):
    if isinstance(value,list):return value
    if isinstance(value,dict) and isinstance(value.get('data'),list):return value['data']
    raise ValueError('Unexpected venue response')

def _get(path):
    from data.upscale_mirror import make_session,BASE_URL
    key=os.getenv('UPSCALE_API_KEY','')
    if not key:raise RuntimeError('Venue credentials unavailable')
    with make_session() as session:
        response=session.get(BASE_URL+path,headers={'Authorization':'Bearer '+key},timeout=12)
        if not response.ok:raise RuntimeError('Venue HTTP error')
        return response.json()

def _fresh(getter=_get):
    accounts=_rows(getter('/accounts/with-risk-status'))
    configured=os.getenv('UPSCALE_ACCOUNT_ID','')
    if configured:
        choices=[a for a in accounts if a.get('accountId')==configured]
    else:
        choices=[a for a in accounts if a.get('status')=='active' and a.get('apiTrading')]
        if os.getenv('UPSCALE_ALLOW_LIVE','false').lower()!='true':choices=[a for a in choices if a.get('type')=='demo']
    if len(choices)!=1:raise ValueError('Account selection ambiguous')
    a=choices[0];account=a['accountId']
    markets=_rows(getter('/v2/markets?accountId='+account))
    names={m['id']:m.get('config',{}).get('baseAsset') for m in markets}
    positions=_rows(getter('/positions/'+account+'/active'))
    orders=_rows(getter('/orders/'+account+'/active'))
    protective=[]
    for o in orders:
        if o.get('type') not in ('stop','take'):continue
        protective.append({'symbol':names.get(o.get('market',o.get('marketId')),'UNKNOWN'),'type':o.get('type'),'direction':o.get('direction'),'trigger_price':_money(o.get('triggerPrice'))})
    safe_positions=[]
    for p in positions:
        symbol=names.get(p.get('market'))
        if not symbol:raise ValueError('Unknown active market')
        size=_money(p.get('size'));notional=_money(p.get('notional'))
        margin=_money(p.get('margin'))
        effective_leverage=notional/margin if margin and notional is not None else None
        safe_positions.append({'symbol':symbol,'direction':p.get('direction'),'size':size,'entry':notional/size if size and notional is not None else None,'notional':notional,'margin':margin,'effective_leverage':effective_leverage,'leverage_source':'derived: venue notional / current margin; not a reported leverage setting','last_event_pnl':_money(p.get('pnl')),'pnl_note':'Venue position event field; NOT verified current unrealized PnL. Use account riskStatus currentEquity for equity.','opened_at':p.get('openedAt'),'protective_orders':[o for o in protective if o['symbol']==symbol and o['direction']==p.get('direction')]})
    risk=a.get('riskStatus') or {}
    return {'status':'LIVE','fetched_at':int(time.time()),'source':'Upscale GET active positions/orders + selected account','account':{'type':a.get('type'),'status':a.get('status'),'phase':a.get('currentPhase'),'api_trading':a.get('apiTrading'),'free_balance':_money(a.get('accountBalance')),'equity':_money(risk.get('currentEquity'))},'positions':safe_positions,'active_count':len(safe_positions),'protective_orders':protective,'orders_sent_by_snapshot':0,'note':'Configuration enables execution; this GET verifies existing venue positions, not trading-process health. Protective prices grouped by symbol/direction, not a claim about every position if duplicate markets.'}

def venue_snapshot(force=False):
    from rt_upscale_actual import fetch
    return fetch(force=force)


def public_snapshot(agent):
    safe_agent={k:agent.get(k) for k in ['status','mode','balance','equity','daily_pnl_pct']}
    safe_agent['local_positions_unverified']=list((agent.get('open_positions') or {}).keys())
    return {'agent_accounting':safe_agent,'venue':venue_snapshot(),'configured_flags':{k:os.getenv(k,'UNKNOWN') for k in ['PAPER_TRADING','UPSCALE_DRY_RUN','UPSCALE_ALLOW_LIVE','DEMO_MIRROR']}}
