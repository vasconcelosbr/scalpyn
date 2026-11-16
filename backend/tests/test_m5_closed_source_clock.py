"""A settled M5 candle is fresh after closure, but recomputation cannot renew it."""
from datetime import datetime, timedelta, timezone

from app.tasks.compute_indicators import closed_5m_source_clock
from app.services.l3_authorization_contract_v3 import build_authorization_contract
from app.services.l3_public_authorization import authorization_expiry
from app.utils.indicator_merge import MergedIndicators


def test_closed_m5_ttl_reaches_shadow_without_renewing_old_candles():
    opened = datetime(2026, 11, 16, 17, 20, tzinfo=timezone.utc)
    closed = opened + timedelta(minutes=5)
    condition = {'id': 'rsi-veto', 'indicator': 'rsi', 'source': 'ohlcv',
                 'source_provider': 'gate.io', 'provider_policy_id': 'spot_gate_closed_ohlcv_v1',
                 'timeframe': '5m', 'period': 14, 'candle_policy': 'CLOSED_ONLY',
                 'operator': '>=', 'value': 90, 'max_age_seconds': 360}
    profile = {'default_timeframe': '5m', 'filters': {'conditions': []},
               'signals': {'conditions': []}, 'entry_triggers': {'conditions': []},
               'block_rules': {'blocks': [
        {'id': 'block-rsi', 'enabled': True, 'logic': 'AND', 'conditions': [condition]}]}}

    def evaluate(at, rsi=64):
        merged = MergedIndicators()
        merged.candidates = [{'indicator': 'rsi', 'actual': rsi, 'source': 'ohlcv',
            'source_provider': 'gate.io', 'provider_policy_id': 'spot_gate_closed_ohlcv_v1',
            'timeframe': '5m', 'period': 14, 'candle_policy': 'CLOSED_ONLY',
            'candle_closed': True, 'scheduler_group': 'structural',
            **closed_5m_source_clock(opened), 'computed_at': at, 'available_at': at,
            'stale': False}]
        return build_authorization_contract(asset={'symbol': 'SOL_USDT', '_merged_indicators': merged},
            profile_config=profile, legacy_decision='ALLOW', evaluated_at=at,
            profile_id='profile-m5', profile_name='M5', profile_version=opened,
            watchlist_id='watchlist-m5')

    settled = evaluate(closed + timedelta(seconds=90))
    assert settled['valid'] is True
    assert settled['authorization_status'] == 'ALLOW'
    assert authorization_expiry(settled) == closed + timedelta(seconds=360)
    # Real veto boundary is unchanged.
    assert evaluate(closed + timedelta(seconds=90), 89)['contract_technical_decision'] == 'ALLOW'
    assert evaluate(closed + timedelta(seconds=90), 90)['contract_technical_decision'] == 'BLOCK'
    # A fresh computation timestamp cannot rejuvenate stale market input.
    expired = evaluate(closed + timedelta(seconds=361))
    assert expired['valid'] is False
    assert 'FEATURE_TTL_EXPIRED' in expired['reason_codes']
    assert closed_5m_source_clock(opened)['source_open_timestamp'] == opened.isoformat()
