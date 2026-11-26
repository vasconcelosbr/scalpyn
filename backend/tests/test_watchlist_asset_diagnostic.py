"""Owned, read-only diagnostics preserve distinct producers and rejected-only assets."""
import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import HTTPException, Response
from sqlalchemy.orm import Session

from app.services.watchlist_asset_diagnostic import diagnostic
from test_mtf_chart_postgres import database
from test_mtf_history import migrate


def test_unknown_watchlist_stops_before_reading_market_or_private_evidence():
    db = AsyncMock()
    db.scalar.return_value = None
    with pytest.raises(HTTPException) as error:
        asyncio.run(diagnostic(db, user_id=uuid4(), watchlist_id=uuid4(), symbol='TEST_USDT'))
    assert error.value.status_code == 404
    assert db.scalar.await_count == 1
    db.execute.assert_not_awaited()


@pytest.mark.parametrize('level', ['POOL', 'L1', 'L2', 'L3'])
def test_owned_complete_snapshot_and_rejected_only_asset(database, level):
    from app.models.profile import Profile
    from app.models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset, PipelineWatchlistRejection
    connection, uid = database
    migrate(connection)
    connection.execute(sa.text('CREATE TABLE pools(id uuid PRIMARY KEY)'))
    for model in (Profile, PipelineWatchlist, PipelineWatchlistAsset, PipelineWatchlistRejection):
        model.__table__.create(connection)
    connection.execute(sa.text('CREATE TABLE market_metadata(symbol text,price numeric)'))
    connection.execute(sa.text('''CREATE TABLE decisions_log(id uuid,user_id uuid,profile_id uuid,
        symbol text,event_type text,metrics jsonb,created_at timestamptz)'''))
    session = Session(bind=connection)
    profile = Profile(user_id=uid, name='Test', config={'default_timeframe':'4h'})
    session.add(profile); session.flush()
    wl = PipelineWatchlist(user_id=uid, name='Test', level=level, profile_id=profile.id, market_mode='spot')
    session.add(wl); session.flush()
    session.add(PipelineWatchlistAsset(watchlist_id=wl.id, symbol='TEST_USDT', current_price=0,
        analysis_snapshot={'status':'approved','nested':{'value':False}}))
    session.add(PipelineWatchlistRejection(watchlist_id=wl.id, user_id=uid, profile_id=profile.id,
        symbol='REJECT_USDT', stage=level, failed_type='filter', failed_indicator='rsi',
        condition_text='rsi > 50', current_value=0, expected_value='50'))
    session.flush()
    for tf, group, age, payload, market in [
        ('4h','structural',2,'{"ema_20":10,"rsi":0,"missing":null,"flag":false}', 'spot'),
        ('4h','micro',1,'{"volume":7}', 'spot'),
        ('4h','structural',3,'{"ema_20":999}', 'spot'),
        ('1h',None,1,'{"adx":18,"atr_pct":0.5}', 'spot'),
        ('4h','structural',0,'{"ema_20":666}', 'futures'),
        ('4h','structural',-1,'{"ema_20":777}', 'spot'),
    ]:
        connection.execute(sa.text('''INSERT INTO indicators VALUES(now()-make_interval(mins=>:age),
            'TEST_USDT',:tf,:market,:group,CAST(:payload AS jsonb))'''), locals())
    connection.execute(sa.text('''INSERT INTO decisions_log VALUES(:id,:uid,:pid,'TEST_USDT',
        'EVALUATION','{"frozen_input":{"rsi":25}}',now())'''),
        {'id':uuid4(),'uid':uid,'pid':profile.id})
    class ReadOnly:
        async def scalar(self, statement, params=None):
            assert str(statement).lstrip().upper().startswith('SELECT')
            return session.scalar(statement, params)
        async def execute(self, statement, params=None):
            assert str(statement).lstrip().upper().startswith('SELECT')
            return session.execute(statement, params)
    async def run():
        with patch('app.services.profile_display_status.load_profile_display_rows', AsyncMock(return_value=[])):
            result = await diagnostic(ReadOnly(), user_id=uid, watchlist_id=wl.id, symbol='TEST_USDT')
            rows = result['sources']['indicators']
            assert len(rows) == 3
            structural = next(r for r in rows if r['scheduler_group']=='structural')
            assert structural['indicators_json'] == {'ema_20':10,'rsi':0,'missing':None,'flag':False}
            assert result['sources']['profile_decision']['metrics']['frozen_input']['rsi'] == 25
            assert result['read_only'] and not result['execution_authority']
            assert result['sources']['pipeline_asset']['current_price'] == 0
            rejected = await diagnostic(ReadOnly(), user_id=uid, watchlist_id=wl.id, symbol='REJECT_USDT')
            assert rejected['sources']['pipeline_asset'] is None
            assert rejected['sources']['pipeline_rejections'][0]['current_value'] == 0
            assert rejected['availability']['indicators'] == 'unavailable'
            for owner, symbol in [(uuid4(),'TEST_USDT'),(uid,'ABSENT_USDT')]:
                with pytest.raises(HTTPException) as error:
                    await diagnostic(ReadOnly(), user_id=owner, watchlist_id=wl.id, symbol=symbol)
                assert error.value.status_code == 404
    asyncio.run(run())
    session.close()


def test_authenticated_route_disables_caching():
    from app.api.watchlists import get_watchlist_asset_diagnostic
    from app.api.config import get_current_user_id
    import inspect
    assert inspect.signature(get_watchlist_asset_diagnostic).parameters['user_id'].default.dependency is get_current_user_id
    response = Response()
    with patch('app.services.watchlist_asset_diagnostic.diagnostic', AsyncMock(return_value={'read_only':True})):
        result = asyncio.run(get_watchlist_asset_diagnostic(uuid4(), 'TEST_USDT', response, user_id=uuid4(), db=object()))
    assert response.headers['cache-control'] == 'private, no-store'
    assert result['read_only']
