"""Rejected is a current funnel view; historical child rows cannot bypass parents."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.api import watchlists as api


class Result:
    def __init__(self, rows=()): self.rows=list(rows)
    def scalars(self): return self
    def first(self): return self.rows[0] if self.rows else None
    def all(self): return self.rows
    def fetchall(self): return self.rows


@pytest.mark.asyncio
@pytest.mark.parametrize('level', ['L1','L2','L3'])
@pytest.mark.parametrize('market', ['spot','futures'])
@pytest.mark.parametrize('removed_at', ['parent','root',None])
async def test_rejected_payload_respects_entire_parent_chain_without_deleting_history(monkeypatch,level,market,removed_at):
    from app.services import pool_service, pipeline_live_candidates, l3_flow_diagnostics
    user=uuid4(); chain=[]
    for name in ('POOL','L1','L2','L3'):
        chain.append(SimpleNamespace(id=uuid4(),user_id=user,level=name,name=name,
            profile_id=uuid4(),source_pool_id=None,market_mode=market,auto_refresh=False,
            source_watchlist_id=chain[-1].id if chain else None))
    wl=next(w for w in chain if w.level==level)
    parent=next(w for w in chain if w.id==wl.source_watchlist_id)
    by_id={w.id:w for w in chain}
    active={w.id:[SimpleNamespace(symbol='KEEP_USDT'),SimpleNamespace(symbol='AVAX_USDT')] for w in chain}
    if removed_at:
        excluded=parent if removed_at=='parent' else chain[0]
        active[excluded.id]=[SimpleNamespace(symbol='KEEP_USDT')]
    def rejection(symbol):
        return SimpleNamespace(symbol=symbol,profile_id=wl.profile_id,recorded_at=datetime.now(timezone.utc),
            analysis_snapshot={},evaluation_trace=[{'type':'block_rule','indicator':'guard','status':'FAIL'}],
            stage=level,failed_type='block_rule',failed_indicator='guard',condition_text='guard',current_value=1,expected_value='0')
    stored=[rejection('AVAX_USDT'),rejection('KEEP_USDT')]
    async def execute(stmt,*args,**kw):
        sql=str(stmt)
        if 'FROM pipeline_watchlist_rejections' in sql: return Result(stored)
        if 'FROM pipeline_watchlists' in sql:
            parent_id=stmt.compile().params['id_1']
            return Result([by_id[parent_id]])
        return Result()
    db=SimpleNamespace(execute=AsyncMock(side_effect=execute))
    monkeypatch.setattr(api,'_load_watchlist_profile_config',AsyncMock(return_value={}))
    monkeypatch.setattr(api,'_load_active_watchlist_assets',AsyncMock(side_effect=lambda ident,db:active[ident]))
    monkeypatch.setattr(api,'_fetch_indicators_map',AsyncMock(return_value={}))
    monkeypatch.setattr(api,'_load_user_score_rules',AsyncMock(return_value=[]))
    monkeypatch.setattr(pool_service,'load_radar_watchlist_eligibility',AsyncMock(return_value={}))
    monkeypatch.setattr(pipeline_live_candidates,'load_live_l3_rejections',AsyncMock(return_value=[
        {'symbol':'LIVE_USDT','profile_id':wl.profile_id}]))
    monkeypatch.setattr(pipeline_live_candidates,'load_live_l3_candidates',AsyncMock(return_value=[]))
    monkeypatch.setattr(pipeline_live_candidates,'classify_live_l3_for_display',AsyncMock(return_value=([],[
        {'symbol':'LIVE_USDT','profile_id':wl.profile_id}])))
    monkeypatch.setattr(api,'_l3_public_visibility_floor_seconds',AsyncMock(return_value=300))
    monkeypatch.setattr(l3_flow_diagnostics,'load_flow_checks',AsyncMock(return_value={}))
    result=await api._get_watchlist_rejections_payload(wl,user,db)
    assert {r['symbol'] for r in result['items']}==({'KEEP_USDT'} if removed_at else {'KEEP_USDT','AVAX_USDT'})
    assert [r.symbol for r in stored]==['AVAX_USDT','KEEP_USDT']
    assert all(str(c.args[0]).lstrip().startswith('SELECT') for c in db.execute.await_args_list)
    # A live complement row outside the parent must be filtered as well.
    assert 'LIVE_USDT' not in {r['symbol'] for r in result['items']}
