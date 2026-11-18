"""Behavioral contracts for editable MTF rules and display-only targets."""
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi import Response

from app.schemas.mtf_chart import MTFChartConfig, MTFPresentation, experimental_preset, presentation_preset
from app.services.mtf_chart_engine import evaluate, indicator_history
from app.services.mtf_indicator_config import effective_indicators
from app.services.profile_runtime_config import canonical_hash
from test_mtf_chart_engine import setup_frames, candle
from test_mtf_chart_postgres import database


def test_legacy_roundtrip_and_visual_changes_preserve_engine_hash():
    legacy = experimental_preset()
    for key in ('atr_period', 'force_patterns', 'rejection_patterns'):
        legacy.pop(key)
    original = MTFChartConfig.model_validate(legacy)
    normalized = original.model_dump()
    normalized['presentation'] = {**presentation_preset(), 'target_pct': 2.5}
    assert MTFChartConfig.model_validate(normalized).engine_dump() == original.engine_dump()
    normalized['force_patterns'] = ['PIN_BULL']
    assert MTFChartConfig.model_validate(normalized).engine_dump() != original.engine_dump()


@pytest.mark.parametrize('key,value', [('atr_period', 0), ('atr_period', 300), ('atr_period', 1.5),
    ('force_patterns', []), ('rejection_patterns', ['PIN_BEAR']), ('trigger_patterns', [])])
def test_invalid_rules_rejected(key, value):
    with pytest.raises(ValueError):
        MTFChartConfig.model_validate({**experimental_preset(), key:value})


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf')])
def test_invalid_target_rejected(value):
    with pytest.raises(ValueError):
        MTFPresentation.model_validate({**presentation_preset(), 'target_pct':value})


def test_existing_rules_change_trigger_and_arming_without_new_conditions():
    raw = experimental_preset()
    frames = setup_frames()
    def status(data): return evaluate('BTC_USDT', frames, MTFChartConfig.model_validate(data), 28800, .01, 'UP')['status']
    assert status(raw) == 'TRIGGERED'
    assert status({**raw, 'trigger_patterns':['PIN_BULL']}) == 'ARMED'
    assert status({**raw, 'zone_proximity_atr':0}) == 'WAIT'
    assert status({**raw, 'max_spread_pct':.001}) == 'NO_TRADE'
    assert status({**raw, 'min_score':sum(raw['score_weights'].values())}) == 'ARMED'


def test_force_and_rejection_patterns_are_effective_score_inputs():
    frames = setup_frames()
    frames['4h']['patterns'] = [{'time':14400, 'pattern':'PIN_BULL'}]
    frames['1h']['patterns'] = [{'time':25200, 'pattern':'MARUBOZU_BULL'}]
    original = evaluate('BTC_USDT', frames, MTFChartConfig.model_validate(experimental_preset()), 28800, .01, 'UP')
    cfg = MTFChartConfig.model_validate({**experimental_preset(), 'force_patterns':['PIN_BULL'], 'rejection_patterns':['MARUBOZU_BULL']})
    result = evaluate('BTC_USDT', frames, cfg, 28800, .01, 'UP')
    assert original['score']['components']['force'] == original['score']['components']['rejection'] == 0
    assert result['score']['components']['force'] == cfg.score_weights['force']
    assert result['score']['components']['rejection'] == cfg.score_weights['rejection']


def test_atr_override_calculates_locally_and_has_a_shared_identity():
    global_config = {'atr':{'enabled':True,'period':14}, 'ema':{'enabled':True,'periods':[9]}}
    untouched = deepcopy(global_config)
    base_identity = {'config_profile_id':'global', 'config_user_id':'owner', 'config_hash':canonical_hash(global_config)}
    cfg = MTFChartConfig.model_validate(experimental_preset())
    assert effective_indicators(global_config,base_identity,cfg) == (global_config,base_identity)
    cfg.atr_period = 7
    effective, identity = effective_indicators(global_config,base_identity,cfg)
    assert global_config == untouched
    assert effective['atr']['period'] == 7 and effective['ema'] == global_config['ema']
    assert identity == {**base_identity, 'config_hash':canonical_hash(effective)}
    candles = [candle(t=i*300,h=105+i%7,l=95-i%3) for i in range(30)]
    assert indicator_history(candles,effective,{'atr'})['atr'][-1] != indicator_history(candles,global_config,{'atr'})['atr'][-1]
    from test_l3_armed_observation import context
    from app.services.l3_armed_observation import reading
    _,_,_,item,row,kwargs = context()
    kwargs.update(cfg=cfg,indicator_identity=identity)
    assert reading(item,row,**kwargs)['status'] is None
    row['analysis'].update(config_hash=canonical_hash(cfg.engine_dump()),indicator_identity=identity)
    assert reading(item,row,**kwargs)['status'] == 'ARMED'


@pytest.mark.asyncio
async def test_bootstrap_exposes_bounds_and_inherited_atr_without_writes(monkeypatch):
    from app.api import mtf_chart
    config = experimental_preset()
    config['presentation'] = presentation_preset()
    del config['presentation']['target_pct']
    before = deepcopy(config)
    get = AsyncMock(side_effect=[config, {'atr':{'enabled':True,'period':21}}, {}])
    write = AsyncMock()
    monkeypatch.setattr(mtf_chart.config_service,'get_config',get)
    monkeypatch.setattr(mtf_chart.config_service,'update_config',write)
    monkeypatch.setattr(mtf_chart.service,'watchlist_options',AsyncMock(return_value=[]))
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:[]))))
    uid = uuid4()
    result = await mtf_chart.bootstrap(Response(),db,uid)
    assert result['inherited_atr'] == {'enabled':True,'period':21}
    assert result['config']['presentation']['target_pct'] == 1
    assert result['config_schema']['properties']['pivot_left']['minimum'] == 1
    assert all(call.args[2] == uid for call in get.await_args_list)
    assert config == before
    write.assert_not_awaited()


def test_target_migration_is_idempotent_and_preserves_config(database):
    import importlib.util
    import json
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    db, uid = database
    original = {**experimental_preset(), 'presentation':presentation_preset()}
    original['presentation'].pop('target_pct')
    db.execute(sa.text("UPDATE config_profiles SET config_json=CAST(:j AS jsonb) WHERE user_id=:u"),{'u':uid,'j':json.dumps(original)})
    custom = {**original, 'presentation':{**original['presentation'],'target_pct':2.5}}
    db.execute(sa.text("INSERT INTO config_profiles(id,user_id,config_type,config_json) VALUES(:id,:u,'mtf_analysis',CAST(:j AS jsonb))"),{'id':uuid4(),'u':uid,'j':json.dumps(custom)})
    path = Path(__file__).parents[1]/'alembic/versions/250_mtf_target_presentation.py'
    spec = importlib.util.spec_from_file_location('target_migration',path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(db)):
        migration.upgrade()
        migration.upgrade()
        rows = db.execute(sa.text("SELECT config_json FROM config_profiles WHERE user_id=:u"),{'u':uid}).scalars().all()
        expected = {**original,'presentation':{**original['presentation'],'target_pct':1}}
        assert expected in rows and custom in rows
        assert MTFChartConfig.model_validate(expected).engine_dump() == MTFChartConfig.model_validate(original).engine_dump()
        migration.downgrade()
        rows = db.execute(sa.text("SELECT config_json FROM config_profiles WHERE user_id=:u"),{'u':uid}).scalars().all()
        assert all(row == original for row in rows)
