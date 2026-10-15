"""Authenticated, bounded, persisted-only Pool context. No JWT or write fallback."""
import hashlib,json,os
from urllib.parse import urlsplit
from uuid import UUID
import httpx
from . import store


def validate_context(data):
    if data.get('contract_version')!='radar_pool_context_v1' or data.get('read_mode')!='persisted-only' or data.get('served_by')!='service_readonly': raise ValueError('POOL_CONTRACT')
    if data.get('membership_complete') is not True or data.get('membership_basis')!='all_persisted_pool_rows': raise ValueError('POOL_INCOMPLETE')
    coins=data['coins']; w=data['watchlist']
    if str(UUID(data['pool']['id']))!=os.environ['SCALPYN_POOL_ID'] or w['source_pool_id']!=data['pool']['id'] or w['watchlist_id']!=os.environ['SCALPYN_WATCHLIST_ID']: raise ValueError('POOL_IDENTITY')
    if data['row_count']!=len(coins) or data['active_count']!=sum(c['is_active'] is True for c in coins): raise ValueError('POOL_COUNT')
    ids=[c['id'] for c in coins]; pairs=[(c['market_type'],c['symbol']) for c in coins]
    if len(set(ids))!=len(ids) or len(set(pairs))!=len(pairs): raise ValueError('POOL_DUPLICATE')
    if w['asset_count']!=len(w['assets']): raise ValueError('WATCHLIST_COUNT')
    return data


def fetch_context(cutoff):
    url=os.environ['ATLAS_POOL_CONTEXT_URL']; p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in ('scalpyn.vercel.app','scalpyn-production.up.railway.app') or p.query or p.fragment or p.username or p.password or p.port not in (None,443) or p.path!=f"/api/integrations/radar/watchlists/{os.environ['SCALPYN_WATCHLIST_ID']}/pool-context": raise ValueError('POOL_URL')
    with httpx.Client(timeout=120,follow_redirects=False) as client:
        with client.stream('GET',url,headers={'X-Scalpyn-Service-Key':os.environ['ATLAS_SCALPYN_READ_KEY']}) as r:
            r.raise_for_status()
            if r.headers.get('X-Scalpyn-Read-Mode')!='persisted-only' or 'no-store' not in r.headers.get('cache-control',''): raise ValueError('POOL_HEADERS')
            raw=b''
            for chunk in r.iter_bytes():
                raw+=chunk
                if len(raw)>33554432: raise ValueError('POOL_TOO_LARGE')
    data=validate_context(json.loads(raw,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('NONFINITE'))))
    return {'day':data['queried_at'][:10],'captured_at':store.now(),'sha256':hashlib.sha256(raw).hexdigest(),
      'data':data['watchlist'],'pool_context':data,'source':'authenticated_pool_context','source_url':url}
