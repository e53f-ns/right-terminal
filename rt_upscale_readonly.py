"""GET-only, identity-bound Upscale reconciliation. No trading callbacks.

Audit and native balances have their own ledger; never book a synthetic paper
close, release paper margin, or guess exchange net PNL. Caller persists returned
state atomically BEFORE changing runtime positions. API identifiers are private.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import tempfile
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import quote

# Shared by Telegram mutations, State.save and reconciliation commits.
STATE_LOCK = threading.RLock()
GET_LOCK = threading.Lock()
_NEXT_GET = 0.0

def save_locked(state):
    with STATE_LOCK:
        atomic_write_state(state._path, state._data)

def paced_get(session, url, **kwargs):
    """Serialize GETs; bounded 429 retry honoring Retry-After (seconds/date).

    Excessive server delay fails closed instead of retrying before it expires.
    No non-GET request can be issued by this helper.
    """
    global _NEXT_GET
    with GET_LOCK:
        for attempt in range(4):
            time.sleep(max(0.0, _NEXT_GET-time.monotonic()))
            response = session.get(url, **kwargs)
            _NEXT_GET = time.monotonic()+1.1
            if response.status_code != 429:
                return response
            raw = getattr(response, 'headers', {}).get('Retry-After')
            delay = 2.0 ** (attempt+1)
            if raw:
                try:
                    delay = max(delay, float(raw))
                except (ValueError, TypeError):
                    try:
                        delay = max(delay, parsedate_to_datetime(raw).timestamp()-time.time())
                    except (ValueError, TypeError, OverflowError):
                        pass
            _NEXT_GET = time.monotonic()+max(1.1, delay)
            if attempt == 3 or delay > 60:
                raise SyncFault('Upscale GET rate limited; retry deferred')
    raise SyncFault('Upscale GET retry exhausted')

BUCKETS = ('open_positions', 'stock_positions', 'forex_positions')
MONEY = ('size', 'notional', 'margin', 'pnl', 'fee', 'funding',
         'exchangedQuote', 'exchangedBase', 'pnlInEvent', 'feeInEvent', 'fundingInEvent')

class SyncFault(RuntimeError):
    pass

def fp9(v):
    if not isinstance(v, (str, int)) or isinstance(v, bool):
        raise SyncFault('Invalid fp9 value')
    d = Decimal(str(v))
    if not d.is_finite() or d != d.to_integral_value():
        raise SyncFault('Invalid fp9 value')
    return str(d / Decimal(10**9))

def identity(p):
    v = p.get('idx')
    if isinstance(v, list):
        if len(v) != 1: raise SyncFault('Invalid position identity')
        v = v[0]
    if not isinstance(v, str) or not v: raise SyncFault('Missing position identity')
    return v

def timestamp(s):
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if d.tzinfo is None: raise ValueError()
        return d.timestamp()
    except (AttributeError, ValueError, TypeError):
        raise SyncFault('Invalid exchange/local timestamp') from None

def array(d):
    if not isinstance(d, list) or not all(isinstance(p, dict) for p in d):
        raise SyncFault('Invalid array response')
    return d

class ReadOnlyVenue:
    """Uses a session with configured SOCKS; only its .get method is reachable."""
    supports_id_snapshot = True
    def __init__(self, session, base_url, key, account_id):
        self.session, self.base_url, self.key, self.account_id = session, base_url.rstrip('/'), key, account_id
    def get(self, path):
        try:
            r = paced_get(self.session, self.base_url + path,
                                 headers={'Authorization': 'Bearer '+self.key}, timeout=30)
            if not r.ok: raise SyncFault('Upscale GET failed HTTP '+str(r.status_code))
            return r.json()
        except SyncFault: raise
        except Exception: raise SyncFault('Upscale GET transport/JSON failure') from None
    def snapshot(self, position_ids=None):
        account_path = quote(self.account_id, safe='')
        accounts = array(self.get('/accounts/with-risk-status'))
        selected = [a for a in accounts if a.get('accountId') == self.account_id]
        if len(selected) != 1 or not selected[0].get('apiTrading') or selected[0].get('status') != 'active':
            raise SyncFault('Configured account unavailable')
        account = selected[0]
        markets = array(self.get('/v2/markets?accountId='+account_path))
        active = array(self.get('/positions/'+account_path+'/active'))
        orders = array(self.get('/orders/'+account_path+'/active'))
        risk = self.get('/accounts/'+account_path+'/risk-status')
        if not isinstance(risk, dict): raise SyncFault('Invalid risk response')
        risk = risk.get('riskStatus', risk)
        hist, total, offset = [], None, 0
        # Once IDs are durable, fetch only tracked detail, never full history.
        if position_ids is not None:
            hist = [self.detail(pid) for pid in sorted(set(position_ids))]
            return dict(account=account, markets=markets, active=active, orders=orders, risk=risk, history=hist)
        while True:
            d = self.get('/positions/'+account_path+'/portfolio/history?limit=100&offset='+str(offset))
            if not isinstance(d, dict) or type(d.get('totalCount')) is not int:
                raise SyncFault('Invalid history paging response')
            if total is None: total = d['totalCount']
            if total < 0 or total != d['totalCount']: raise SyncFault('History changed while paging')
            page = array(d.get('data'))
            hist.extend(page); offset += len(page)
            if offset >= total: break
            if not page or offset > 10000: raise SyncFault('Incomplete history')
        if len(hist) != total or len({identity(p) for p in hist}) != total:
            raise SyncFault('History count/identity mismatch')
        return dict(account=account, markets=markets, active=active, orders=orders, risk=risk, history=hist)
    def detail(self, pid):
        d = self.get('/positions/'+quote(pid, safe=''))
        if isinstance(d, list):
            if len(d) != 1: raise SyncFault('Invalid detail response')
            d = d[0]
        if not isinstance(d, dict) or identity(d) != pid: raise SyncFault('Detail identity mismatch')
        return d
    def events(self, pid):
        return array(self.get('/positions/'+quote(pid, safe='')+'/history'))
    def active(self):
        return array(self.get('/positions/'+quote(self.account_id, safe='')+'/active'))
    def orders(self):
        return array(self.get('/orders/'+quote(self.account_id, safe='')+'/active'))

def restore_position(pos):
    """Preserve all persisted metadata, including demo IDs/open_fee/asset_class."""
    p = deepcopy(pos)
    p['side'] = 'buy' if p['side'] in ('buy', 'long') else 'sell'
    p.setdefault('margin', p['size_usdt'])
    p.setdefault('leverage', 1)
    return p

def persist_metadata(target, result):
    for k in ('demo', 'open_fee', 'asset_class', 'opened_at'):
        if k in result: target[k] = deepcopy(result[k])

def normalized(p):
    out = {k:deepcopy(p.get(k)) for k in ('status', 'direction', 'openedAt', 'closedAt', 'lastUpdatedAt',
                'eventName', 'closeReason', 'version', 'profitAdjustmentApplied')}
    out['position_id'] = identity(p)
    for k in MONEY:
        if p.get(k) is not None: out[k] = fp9(p[k])
    if p.get('leverage') is not None:
        out['leverage'] = fp9(p['leverage'])
        out['leverage_source'] = 'exchange_position_field'
    elif p.get('margin') is not None and Decimal(fp9(p['margin'])) > 0 and p.get('notional') is not None:
        out['implied_leverage'] = str(Decimal(fp9(p['notional'])) / Decimal(fp9(p['margin'])))
        out['leverage_source'] = 'derived_notional_over_margin_not_exchange_leverage_field'
    return out

def legacy_match(local, events):
    # Observed API openedAt changes on funding. Use actual initial order/event.
    opens = [e for e in events if e.get('eventName') == 'increasePosition' and e.get('version') == 1]
    if len(opens) != 1: return False
    e = opens[0]; o = e.get('order') or {}
    t = o.get('createdAt') or e.get('lastUpdatedAt')
    if abs(timestamp(t)-timestamp(local.get('opened_at'))) > 120: return False
    if o.get('status') != 'executed' or o.get('indexPrice') is None: return False
    fill, entry = Decimal(fp9(o['indexPrice'])), Decimal(str(local['entry']))
    return entry > 0 and abs(fill/entry-1) <= Decimal('0.01')

def plan_reconciliation(state, venue, checked_at=None):
    """All-or-nothing pure state plan. Any ambiguity raises, preserving input.

    Restore missing IDs by unique initial execution event, never pair alone.
    Confirm exact detail, trader, market, direction and active identity. Include
    source evidence in durable closed audit before removing any local ghost.
    """
    locals_live = [p for b in BUCKETS for p in state.get(b, {}).values()
                   if (p.get('demo') or {}).get('dry_run') is not True]
    ids = [(p.get('demo') or {}).get('position_id') for p in locals_live]
    if getattr(venue, 'supports_id_snapshot', False) is True and all(ids):
        snap = venue.snapshot(position_ids=ids)
    else:
        snap = venue.snapshot()
    account = snap['account']; aid = account['accountId']
    market_by_symbol = {}
    for m in snap['markets']:
        symbol = m['config']['baseAsset']
        if symbol in market_by_symbol: raise SyncFault('Ambiguous market symbol')
        market_by_symbol[symbol] = m['id']
    active = {identity(p):p for p in snap['active']}
    if len(active) != len(snap['active']): raise SyncFault('Duplicate active identity')
    result = deepcopy(state); seen = set(); events_cache = {}
    audit = result.setdefault('upscale_reconciliation', {})
    closed = audit.setdefault('closed', {})
    def events(pid):
        if pid not in events_cache: events_cache[pid] = venue.events(pid)
        return events_cache[pid]
    for bucket in BUCKETS:
        for pair, local in list(result.get(bucket, {}).items()):
            original_local = deepcopy(local)
            meta = local.get('demo') or {}
            if meta.get('dry_run') is True: continue
            symbol = pair.split('/')[0]
            mid = market_by_symbol.get(symbol)
            if mid is None: raise SyncFault('Tracked symbol unavailable')
            direction = 'long' if local['side'] in ('buy', 'long') else 'short'
            candidates = [p for p in snap['history'] if p.get('trader') == aid and p.get('market') == mid and p.get('direction') == direction]
            if meta.get('position_id'):
                matches = [p for p in candidates if identity(p) == meta['position_id']]
            else:
                matches = [p for p in candidates if legacy_match(local, events(identity(p)))]
            if len(matches) != 1: raise SyncFault('Unresolved or ambiguous tracked position')
            pid = identity(matches[0])
            if pid in seen: raise SyncFault('Identity tracked twice')
            seen.add(pid)
            detail = venue.detail(pid)
            if (detail.get('trader'), detail.get('market'), detail.get('direction')) != (aid, mid, direction):
                raise SyncFault('Detail ownership/market/direction mismatch')
            local['demo'] = dict(meta, venue='upscale', account_id=aid,
                                 account_type=account['type'], symbol=symbol, market_id=mid,
                                 direction=direction, position_id=pid, dry_run=False)
            local['venue_position'] = normalized(detail)
            initial_orders = [e.get('order') or {} for e in events(pid) if e.get('eventName') == 'increasePosition' and e.get('version') == 1]
            if len(initial_orders) == 1 and initial_orders[0].get('leverage') is not None:
                local['venue_position']['initial_order_leverage'] = fp9(initial_orders[0]['leverage'])
                local['venue_position']['initial_order_leverage_source'] = 'exchange_executed_initial_order'
            if detail.get('status') == 'opened':
                if pid not in active or detail.get('closedAt') is not None: raise SyncFault('Active snapshot mismatch')
                fields = ('trader', 'market', 'direction', 'status', 'version', 'size')
                if type(detail.get('version')) is not int or any(detail.get(k) != active[pid].get(k) for k in fields):
                    raise SyncFault('Active detail changed during reconciliation')
                # Ensure protection belongs to this ID, not a new position on same pair.
                protected = [o for o in snap['orders'] if o.get('positionId') in (pid, [pid]) and o.get('market') == mid and o.get('direction') == direction and o.get('status') == 'active']
                for typ, fld in (('stop', 'stop_loss'), ('take', 'take_profit')):
                    hit = [o for o in protected if o.get('type') == typ]
                    if len(hit) != 1 or Decimal(fp9(hit[0]['triggerPrice'])) != Decimal(str(local[fld])) or hit[0].get('amount') != detail.get('size'):
                        raise SyncFault('Protection mismatch; no order repair attempted')
            elif detail.get('status') in ('closed', 'liquidated'):
                if pid in active or detail.get('pnl') is None or detail.get('fee') is None or detail.get('funding') is None:
                    raise SyncFault('Closed accounting missing or contradictory')
                timestamp(detail.get('closedAt'))
                ev = [e for e in events(pid) if e.get('eventName') in ('closePosition', 'liquidate', 'forceClose') and e.get('closedAt') == detail['closedAt']]
                if not ev: raise SyncFault('Missing authoritative closure event')
                last = max(ev, key=lambda e:e.get('version', -1)); order = last.get('order') or {}
                if identity(last) != pid or last.get('version') != detail.get('version'):
                    raise SyncFault('Closure event identity/version mismatch')
                row = dict(pair=pair, bucket=bucket, local_position=original_local,
                           exchange=normalized(detail), closure_event=deepcopy(last),
                           source='upscale_get_only', pnl_semantics='exchange reported pnl; fees/funding separate',
                           reason=detail.get('closeReason') or (order.get('type') if order.get('status') == 'executed' else None),
                           recorded_at=checked_at or datetime.now(timezone.utc).isoformat())
                closed.setdefault(pid, row)
                del result[bucket][pair]
            else: raise SyncFault('Unknown exchange position status')
    if set(active)-seen: raise SyncFault('Untracked live position; entries blocked')
    # A second GET closes the race window; do not retire/rebind on changed identity.
    active_rows = venue.active()
    active_again = {identity(p):p for p in active_rows}
    if len(active_again) != len(active_rows): raise SyncFault('Duplicate second active identity')
    stable = ('trader', 'market', 'direction', 'status', 'version', 'size')
    if {k:tuple(v.get(f) for f in stable) for k,v in active.items()} != {k:tuple(v.get(f) for f in stable) for k,v in active_again.items()}:
        raise SyncFault('Active positions changed during reconciliation')
    # Order changes do not necessarily increment position version.
    def order_fingerprint(orders):
        return sorted(json.dumps(o, sort_keys=True) for o in orders)
    if order_fingerprint(snap['orders']) != order_fingerprint(venue.orders()):
        raise SyncFault('Protection orders changed during reconciliation')
    audit['checked_at'] = checked_at or datetime.now(timezone.utc).isoformat()
    audit['source_of_truth'] = 'upscale'
    audit['account_type'] = account['type']
    audit['current_phase'] = account.get('currentPhase')
    audit['account_id'] = aid
    audit['native_account_balance'] = fp9(account['accountBalance'])
    audit['native_equity'] = fp9(snap['risk']['currentEquity'])
    audit['paper_balance_unchanged'] = result.get('paper_balance')
    audit['fault'] = None
    return result

def atomic_write_state(path, data):
    """Commit archive + active map together, retaining old file on write failure."""
    path = os.path.abspath(path)
    fd, tmp = tempfile.mkstemp(prefix='.upscale-sync-', suffix='.tmp', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def sync_runtime(state, engine):
    """For integration only. GETs and atomic local persistence; no callbacks.

    Existing host State.save is non-atomic. Reconciliation uses its own writer.
    Snapshot ledger stays separate; all live orders/exits remain venue-managed.
    """
    mirror = engine.mirror
    if not mirror or getattr(mirror, 'dry_run', True): return False
    engine._upscale_sync_fault = 'reconciliation incomplete'
    with STATE_LOCK:
        old = deepcopy(state._data)
    try:
        venue = ReadOnlyVenue(mirror._session, os.getenv('UPSCALE_BASE_URL', 'https://api.upscale.trade'), mirror._key, mirror.account_id)
        planned = plan_reconciliation(old, venue)
        restored = {pair:restore_position(pos) for b in BUCKETS for pair,pos in planned.get(b, {}).items()}
        with STATE_LOCK:
            # A command during GETs invalidates the plan. Never overwrite it or
            # restore a stale snapshot on failure; next scan obtains a new plan.
            if state._data != old:
                raise SyncFault('Local state changed during reconciliation')
            atomic_write_state(state._path, planned)
            state._data = planned
            engine.positions = restored
            engine._marks = {k:v for k,v in engine._marks.items() if k in restored}
            engine._upscale_sync_fault = None
    except Exception:
        engine._upscale_sync_fault = 'Upscale reconciliation unavailable/ambiguous; entries blocked'
        return False
    return True
