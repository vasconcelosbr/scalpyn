"""Activation path for l3_managed_cert_v2 (local PostgreSQL): configure + recertify."""
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_shadow_l3_persistence import db, URL  # noqa: F401
from test_shadow_l3_continuation import policy, T  # noqa: F401
from test_l3_managed_exit_ml import scenario
from app.ml.l3_managed_exit import CERTIFICATION_KEY, VERSION, certify
from app.models.shadow_trade import ShadowTrade
from app.models.config_profile import ConfigProfile
from app.services.config_service import ConfigService

pytestmark = pytest.mark.skipif(not URL, reason="Local PostgreSQL URL required")


async def _seed(db, policy):
    shadow_ns, rows, cfg = scenario(policy, 'TIMEOUT', max_holding=120)
    uid = uuid4()
    await db.execute(text("INSERT INTO users VALUES(:uid)"), {"uid": uid})
    db.add(ConfigProfile(user_id=uid, pool_id=None, config_type='ml', is_active=True,
                         config_json={**cfg, 'ml_dataset_valid_from': '2025-01-01T00:00:00+00:00'}))
    sid = uuid4()
    from datetime import datetime
    exit_at = datetime.fromisoformat(shadow_ns.exit_timestamp) if isinstance(shadow_ns.exit_timestamp, str) else shadow_ns.exit_timestamp
    shadow = ShadowTrade(id=sid, user_id=uid, symbol='UNI_USDT', source='L3', status='COMPLETED', exchange='gate.io',
                         entry_price=100, tp_price=102, sl_price=95, amount_usdt=100, timeout_candles=2,
                         entry_timestamp=T, exit_timestamp=exit_at, outcome='TIMEOUT', closure_path='l3_continuation',
                         exit_price=shadow_ns.exit_price, exit_price_semantics=shadow_ns.exit_price_semantics,
                         pnl_pct=shadow_ns.pnl_pct, label_contract_version=VERSION, lineage_status='EXACT',
                         config_snapshot=shadow_ns.config_snapshot, eligible_for_training=False)
    db.add(shadow)
    await db.flush()
    v1 = certify(SimpleNamespace(**{**vars(shadow_ns)}), rows, None, checked_at=T + timedelta(days=1))
    assert v1['reason'] == 'CENSORED_OR_INVALID_HORIZON'
    await db.execute(text("""INSERT INTO shadow_l3_exit_states(shadow_id,user_id,policy_hash,policy,state)
        VALUES(:id,:uid,:hash,CAST(:policy AS JSONB),CAST(:state AS JSONB))"""),
        {"id": sid, "uid": uid, "hash": shadow_ns.config_snapshot['shadow_l3_exit_policy']['hash'],
         "policy": json.dumps(shadow_ns.config_snapshot['shadow_l3_exit_policy']),
         "state": json.dumps({"outcome": "TIMEOUT", "ml_label": v1}, default=str)})
    for r in rows:
        await db.execute(text("""INSERT INTO shadow_l3_exit_decisions VALUES(:id,:at,:av,:hash,CAST(:ev AS JSONB),CAST(:st AS JSONB))"""),
                         {"id": sid, "at": r['candle_at'], "av": r['available_at'], "hash": r['policy_hash'],
                          "ev": json.dumps(r['evidence'], default=str), "st": json.dumps(r['state'], default=str)})
    await db.commit()
    return uid, sid


def _args(**kw):
    base = dict(valid_from=(T - timedelta(hours=1)).isoformat(), flow_evidence='REPLAY_FALLBACK',
                timeout_horizon='POLICY_TIMEOUT', missing_candle_wait_seconds=600, user_id=None, apply=False, batch_size=50)
    return SimpleNamespace(**{**base, **kw})


async def _read_write(db):
    await db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE"))
    await db.commit()


@pytest.mark.asyncio
async def test_configure_then_recertify_dry_run_first_then_apply(db, policy):
    from scripts.configure_l3_label_v2 import execute as configure
    from scripts.recertify_l3_managed_labels import execute as recertify
    uid, sid = await _seed(db, policy)
    service = ConfigService(); service.redis = None

    plan = await configure(db, _args(), service=service)
    assert plan['mode'] == 'DRY_RUN' and plan[f'ml.{CERTIFICATION_KEY}']['current'] is None
    await _read_write(db)
    assert (await db.execute(text("SELECT count(*) FROM config_audit_log"))).scalar() == 0

    applied = await configure(db, _args(apply=True), service=service)
    assert applied['read_back_ok'] is True
    assert (await db.execute(text("SELECT count(*) FROM config_audit_log"))).scalar() == 2

    dry = await recertify(db, _args())
    await _read_write(db)
    assert dry['targets'] == 1 and dry['updated'] == 0
    assert dry['transitions'] == {'TIMEOUT|CENSORED_OR_INVALID_HORIZON->valid': 1}
    state = (await db.execute(text("SELECT state FROM shadow_l3_exit_states WHERE shadow_id=:id"), {"id": sid})).scalar_one()
    assert 'certification_version' not in state['ml_label']  # dry run wrote nothing

    done = await recertify(db, _args(apply=True))
    assert done['updated'] == 1 and done['remaining_after'] == 0
    state = (await db.execute(text("SELECT state FROM shadow_l3_exit_states WHERE shadow_id=:id"), {"id": sid})).scalar_one()
    assert state['ml_label']['valid'] is True and state['ml_label']['certification_version'] == 'l3_managed_cert_v2'
    assert state['ml_label_superseded'][0]['reason'] == 'CENSORED_OR_INVALID_HORIZON'
    row = (await db.execute(text("SELECT eligible_for_training, net_return_pct FROM shadow_trades WHERE id=:id"), {"id": sid})).one()
    assert row[0] is True and row[1] == pytest.approx(state['ml_label']['net_return_pct'])
    assert (await recertify(db, _args()))['targets'] == 0  # idempotent
