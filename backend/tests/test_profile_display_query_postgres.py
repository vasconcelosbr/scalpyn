"""Preserve latest-decision semantics while keeping historical JSON out of sorting."""
import json
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.backoffice import DecisionLog
from app.services.profile_display_status import latest_profile_decisions_statement
from app.services.latest_profile_decision import latest_profile_decision_ids
from test_mtf_chart_postgres import database


def test_latest_decisions_match_legacy_without_sorting_historical_json(database):
    db, uid = database
    p1, p2, other = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    db.execute(sa.text('''CREATE TABLE decisions_log (
        id bigint PRIMARY KEY,user_id uuid,profile_id uuid,symbol text,
        created_at timestamptz,event_type text,score float,metrics jsonb)'''))
    def insert(id, profile, symbol, *, owner=uid, at=now, event='PROFILE_EVALUATION', metrics=None):
        payload = metrics if metrics is not None else {
            'price':id, 'historical_features':['unused feature registry'] * 2000,
            'l3_authorization_contract_v3': {
                'lineage':{'profile_id':str(profile)}, 'evaluated_at':at.isoformat(),
                'sections':{'block_rules':{'blocks':[]}},
                'profile_execution_contract':{'profile_projection_hash':str(id)}}}
        db.execute(sa.text('''INSERT INTO decisions_log VALUES
            (:id,:u,:p,:s,:at,:event,:score,CAST(:m AS jsonb))'''),
            dict(id=id,u=owner,p=profile,s=symbol,at=at,event=event,score=id,m=json.dumps(payload)))
    insert(1,p1,'BTC_USDT',at=now-timedelta(seconds=1))
    insert(2,p1,'BTC_USDT',metrics={})  # Latest missing evidence cannot revive old approval.
    insert(3,p2,'BTC_USDT')
    insert(4,p1,'ETH_USDT')
    insert(5,p1,'ETH_USDT')  # Tied timestamps retain descending ID order.
    insert(6,p1,'ETH_USDT',event='PROFILE_CONSOLIDATION')
    insert(7,p1,'ETH_USDT',event='PROFILE_CONSOLIDATION_REJECTED')
    insert(8,p1,'ETH_USDT',owner=other)
    insert(9,p2,'ETH_USDT')  # Pair filtering, not a cross product.
    insert(10,p1,'ETH_USDT',event=None)  # Retain SQL NOT IN null semantics.
    pairs={(p1,'BTC_USDT'),(p2,'BTC_USDT'),(p1,'ETH_USDT'),(p1,'MISSING_USDT')}
    stmt=latest_profile_decisions_statement(user_id=uid,pairs=pairs)
    contract=DecisionLog.metrics['l3_authorization_contract_v3']
    legacy=(sa.select(DecisionLog.id,DecisionLog.symbol,DecisionLog.profile_id,DecisionLog.score,
        DecisionLog.metrics['price'].label('price'),contract['lineage'].label('lineage'),
        contract['evaluated_at'].label('evaluated_at'),contract['sections'].label('sections'),
        contract['profile_execution_contract']['profile_projection_hash'].label('profile_hash'))
        .where(DecisionLog.user_id==uid,
            sa.tuple_(DecisionLog.profile_id,DecisionLog.symbol).in_(pairs),
            DecisionLog.event_type.notin_(['PROFILE_CONSOLIDATION','PROFILE_CONSOLIDATION_REJECTED']))
        .distinct(DecisionLog.profile_id,DecisionLog.symbol)
        .order_by(DecisionLog.profile_id,DecisionLog.symbol,DecisionLog.created_at.desc(),DecisionLog.id.desc()))
    old={r['id']:dict(r) for r in db.execute(legacy).mappings()}
    new={r['id']:dict(r) for r in db.execute(stmt).mappings()}
    assert new==old and set(new)=={2,3,5}
    assert new[2]['lineage'] is None and new[2]['sections'] is None
    assert not db.execute(latest_profile_decisions_statement(user_id=other,pairs={(p2,'BTC_USDT')})).all()
    assert not db.execute(latest_profile_decisions_statement(user_id=uid,pairs=set())).all()

    sql=str(stmt.compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
    plan=db.scalar(sa.text('EXPLAIN (VERBOSE,FORMAT JSON) '+sql))[0]['Plan']
    def nodes(node):
        yield node
        for child in node.get('Plans',[]): yield from nodes(child)
    limits=[n for n in nodes(plan) if n['Node Type']=='Limit']
    assert limits, 'Latest-per-pair selection must precede evidence extraction'
    for limit in limits:
        assert all('metrics' not in expr for n in nodes(limit) for expr in n.get('Output',[]))
    assert set(db.scalars(latest_profile_decision_ids(user_id=uid,pairs=pairs))) == set(new)


def test_latest_lookup_uses_bounded_index_without_parallel_history_scan(database):
    db, uid = database
    profile = uuid4()
    db.execute(sa.text('''CREATE TABLE decisions_log (
        id bigint PRIMARY KEY,user_id uuid,profile_id uuid,symbol text,
        created_at timestamptz,event_type text,score float,metrics jsonb)'''))
    db.execute(sa.text('''INSERT INTO decisions_log
        SELECT n, :u, :p, 'ASSET_' || (n % 20),
            timestamptz '2026-11-17 00:00:00+00' + n * interval '1 second',
            'PROFILE_EVALUATION', 0, '{}'::jsonb
        FROM generate_series(1, 20000) n'''), {'u':uid,'p':profile})
    path=Path(__file__).parents[1]/'alembic/versions/254_profile_latest_index.py'
    spec=importlib.util.spec_from_file_location('latest_index_migration',path)
    migration=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # Fixture is transactional; use the exact index definition without CONCURRENTLY.
    db.execute(sa.text(migration.INDEX_SQL.replace('CONCURRENTLY ','')))
    db.execute(sa.text('ANALYZE decisions_log'))
    db.execute(sa.text('SET LOCAL max_parallel_workers_per_gather=12'))
    db.execute(sa.text('SET LOCAL min_parallel_table_scan_size=0'))
    db.execute(sa.text('SET LOCAL parallel_setup_cost=0'))
    db.execute(sa.text('SET LOCAL parallel_tuple_cost=0'))
    pairs={(profile,f'ASSET_{n}') for n in range(20)} | {(profile,'MISSING')}
    stmt=latest_profile_decision_ids(user_id=uid,pairs=pairs)
    sql=str(stmt.compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
    plan=db.scalar(sa.text('EXPLAIN (ANALYZE,VERBOSE,FORMAT JSON) '+sql))[0]['Plan']
    def nodes(node):
        yield node
        for child in node.get('Plans',[]): yield from nodes(child)
    actual=list(nodes(plan))
    assert not any(n.get('Parallel Aware') or n['Node Type'].startswith('Gather') for n in actual)
    scans=[n for n in actual if n.get('Index Name')=='ix_decisions_profile_symbol_latest']
    assert scans and all(n['Actual Rows'] <= 1 for n in scans)
    assert set(db.scalars(stmt)) == set(range(19981,20001))
    # Reused prepared plans must still prove the fixed partial-index predicate.
    compiled=stmt.compile(dialect=postgresql.dialect())
    assert 'PROFILE_CONSOLIDATION' not in str(compiled.params)
    prepared=sql.replace("'"+str(uid)+"'",'$1').replace("'"+str(profile)+"'",'$2')
    db.execute(sa.text('SET LOCAL plan_cache_mode=force_generic_plan'))
    db.execute(sa.text('PREPARE latest_profile_probe(uuid,uuid) AS '+prepared))
    try:
        plan=db.scalar(sa.text(f"EXPLAIN (ANALYZE,FORMAT JSON) EXECUTE latest_profile_probe('{uid}','{profile}')"))[0]['Plan']
        actual=list(nodes(plan))
        assert not any(n.get('Parallel Aware') or n['Node Type'].startswith('Gather') for n in actual)
        assert any(n.get('Index Name')=='ix_decisions_profile_symbol_latest' for n in actual)
    finally:
        db.execute(sa.text('DEALLOCATE latest_profile_probe'))
