from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Response
from sqlalchemy.dialects import postgresql

from app.services import l3_profile_consolidation as service

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def row(symbol='BTC_USDT', status='approved', profile='M5', score=0, **extra):
    return dict(symbol=symbol, status=status, profile_id=profile, profile_name=profile,
        watchlist_id=profile, asset_id=symbol, alpha_score=score, current_price=10,
        evaluated_at=NOW.isoformat(), decision_id=1,
        data_reasons=['FEATURE_TTL_EXPIRED'] if status == 'awaiting_data' else [], **extra)


def test_shadow_suppression_and_low_score_never_hide_profile_approvals():
    rows = [row(symbol=f'ASSET_{i}', processing_result='SUPPRESSED/PROFILE_SYMBOL_HOURLY_LIMIT',
                shadow_id=None, executable=True, authorization_status='ALLOW') for i in range(24)]
    result = service.consolidate_profile_display_rows(rows, now=NOW)
    assert result['total'] == 24
    assert all(r['executable'] is r['execution_authority'] is False for r in result['items'])
    assert all('authorization_status' not in r and 'shadow_id' not in r for r in result['items'])
    assert result['execution_authority'] is False


def test_union_deduplicates_profiles_and_keeps_data_pending_separate():
    result = service.consolidate_profile_display_rows([
        row(profile='M5'), row(profile='H1', score=30), row(profile='H4', status='awaiting_data'),
        row(symbol='ETH_USDT', status='awaiting_data'), row(symbol='ETH_USDT', status='rejected', profile='H1'),
        row(symbol='SOL_USDT', status='rejected'),
    ], now=NOW)
    assert [r['symbol'] for r in result['items']] == ['BTC_USDT']
    assert result['items'][0]['candidate_count'] == 2
    assert result['items'][0]['profile_id'] == 'H1'
    assert [r['symbol'] for r in result['awaiting_data_items']] == ['ETH_USDT']
    assert result['awaiting_data_items'][0]['data_reasons'] == ['FEATURE_TTL_EXPIRED']
    assert result['awaiting_data_count'] == 1


def test_duplicate_watchlists_count_profile_once_and_empty_universe_is_empty():
    result = service.consolidate_profile_display_rows([row(), row()], now=NOW)
    assert result['total'] == result['items'][0]['candidate_count'] == 1
    assert service.consolidate_profile_display_rows([], now=NOW)['items'] == []


@pytest.mark.asyncio
async def test_loader_scopes_user_and_uses_shared_profile_classifier(monkeypatch):
    user_id = uuid4()
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: ['M5', 'H1']))))
    loader = AsyncMock(side_effect=[[row()], [row(symbol='ETH_USDT', status='awaiting_data')]])
    monkeypatch.setattr(service, 'load_profile_display_rows', loader)
    result = await service.load_l3_profile_consolidation(db, user_id=user_id)
    statement = db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert user_id in statement.params.values()
    assert 'spot' in statement.params.values() and 'L3' in statement.params.values()
    assert loader.call_args_list[0].kwargs['now'] == loader.call_args_list[1].kwargs['now']
    assert all(c.kwargs['user_id'] == user_id for c in loader.call_args_list)
    assert result['total'] == result['awaiting_data_count'] == 1


@pytest.mark.asyncio
async def test_display_route_precedes_dynamic_route_and_never_uses_execution_loader(monkeypatch):
    from app.api import watchlists
    paths = [route.path for route in watchlists.router.routes]
    assert paths.index('/api/watchlists/l3-consolidated/profile-status') < paths.index(
        '/api/watchlists/{watchlist_id}/profile-status')
    payload = service.consolidate_profile_display_rows([row()], now=NOW)
    from app.services import l3_armed_observation
    monkeypatch.setattr(l3_armed_observation, 'load_l3_armed_display', AsyncMock(return_value=payload))
    execution = AsyncMock(side_effect=AssertionError('Display must not consume execution authorization'))
    monkeypatch.setattr(watchlists, 'load_live_l3_candidates', execution)
    response = Response()
    assert await watchlists.get_l3_consolidated_profile_status(response, uuid4(), object()) == payload
    assert response.headers['Cache-Control'] == 'private, no-store'
    execution.assert_not_called()


@pytest.mark.asyncio
async def test_summary_counts_use_profile_approvals_not_shadow_and_exclude_pending(monkeypatch):
    loader = AsyncMock(return_value=[row(processing_result='SUPPRESSED/PROFILE_SYMBOL_HOURLY_LIMIT'),
        row(symbol='ETH_USDT', status='awaiting_data'), row(symbol='SOL_USDT', status='rejected')])
    monkeypatch.setattr(service, 'load_profile_display_rows', loader)
    watchlists = [SimpleNamespace(id='M5', level='L3', market_mode='spot'),
                 SimpleNamespace(id='FUTURES', level='L3', market_mode='futures'),
                 SimpleNamespace(id='H1', level='L2', market_mode='spot')]
    assert await service.load_l3_profile_summary_counts(None, user_id='user', watchlists=watchlists) == {'M5': 1}
    loader.assert_awaited_once()
    assert loader.call_args.kwargs['user_id'] == 'user'
