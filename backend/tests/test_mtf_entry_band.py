"""Candidate entry is presentation-only and never authorizes a simulation."""
from copy import deepcopy

import pytest

from app.schemas.mtf_chart import MTFChartConfig, experimental_preset, presentation_preset
from app.services.mtf_chart_engine import evaluate, iso
from app.services.mtf_entry_band import candidate_entry_band
from app.services.profile_runtime_config import canonical_hash
from test_mtf_chart_engine import setup_frames


def context():
    cfg = MTFChartConfig.model_validate({**experimental_preset(), 'presentation': presentation_preset()})
    frames = setup_frames()
    analysis = evaluate('TEST_USDT', frames, cfg, 28800, .01, 'UP')
    analysis['config_hash'] = canonical_hash(cfg.engine_dump())
    return cfg, frames, analysis


def test_band_preserves_engine_identity_score_vetoes_and_plan():
    cfg, frames, analysis = context()
    before = deepcopy((frames, analysis))
    band = candidate_entry_band(analysis, frames, cfg, 28801)
    assert band['state'] == 'CONFIRMED'
    assert band['low'] == analysis['zone_1h']['low']
    assert band['high'] == analysis['zone_1h']['high'] + cfg.zone_proximity_atr * frames['1h']['atr']
    assert band['entry'] == analysis['plan']['entry']
    assert band['operational_effect'] is False
    assert (frames, analysis) == before
    original_hash = cfg.engine_dump()
    cfg.presentation.entry_band_enabled = False
    assert cfg.engine_dump() == original_hash
    repeated = evaluate('TEST_USDT', frames, cfg, 28800, .01, 'UP')
    assert {**repeated, 'config_hash':canonical_hash(cfg.engine_dump())} == analysis
    assert candidate_entry_band(analysis, frames, cfg, 28801) is None


@pytest.mark.parametrize('status,vetoes,expected', [
    ('WAIT', [], 'WAITING'), ('ARMED', [], 'WAITING'),
    ('NO_TRADE', [], 'BLOCKED'), ('TRIGGERED', ['BTC_DOWNTREND'], 'BLOCKED'),
])
def test_existing_status_and_vetoes_control_color(status, vetoes, expected):
    cfg, frames, analysis = context()
    analysis.update(status=status, vetoes=vetoes)
    result = candidate_entry_band(analysis, frames, cfg, 28801)
    assert result['state'] == expected
    assert result['vetoes'] == vetoes
    assert result['entry'] is None


@pytest.mark.parametrize('case', ['missing', 'future', 'invalid', 'stale', 'data', 'plan_expired', 'plan_outside'])
def test_invalid_or_stale_context_never_draws_a_candidate(case):
    cfg, frames, analysis = context()
    now = 28801
    if case == 'missing': analysis['zone_1h'] = None
    if case == 'future': analysis['zone_1h']['available_at'] = 28801
    if case == 'invalid': frames['1h']['atr'] = float('nan')
    if case == 'stale': now = 28800 + cfg.stale_tf_multiplier * 300
    if case == 'data': analysis['vetoes'] = ['DATA_STALE:1h']
    if case == 'plan_expired': analysis['plan']['expires_at'] = iso(28800)
    if case == 'plan_outside': analysis['plan']['entry'] = analysis['zone_1h']['low'] - 1
    band = candidate_entry_band(analysis, frames, cfg, now)
    assert band['state'] == 'UNAVAILABLE'
    assert band['low'] is None and band['high'] is None and band['entry'] is None


def test_legacy_presentation_remains_valid_and_switch_is_strict_boolean():
    cfg, frames, analysis = context()
    raw = cfg.model_dump()
    del raw['presentation']['entry_band_enabled']
    assert candidate_entry_band(analysis, frames, MTFChartConfig.model_validate(raw), 28801) is None
    for invalid in ('true', 1, {}):
        raw['presentation']['entry_band_enabled'] = invalid
        with pytest.raises(ValueError): MTFChartConfig.model_validate(raw)
