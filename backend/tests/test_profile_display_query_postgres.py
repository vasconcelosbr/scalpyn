"""Preserve latest-decision semantics while keeping historical JSON out of sorting."""
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models.backoffice import DecisionLog
from app.services.profile_display_status import latest_profile_decisions_statement
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
    uniques=[n for n in nodes(plan) if n['Node Type']=='Unique']
    assert uniques, 'Latest-per-pair selection must precede evidence extraction'
    # This checks the actual PG plan: historical JSON must not be projected into Unique/Sort.
    for unique in uniques:
        assert all('metrics' not in expr for n in nodes(unique) for expr in n.get('Output',[]))
