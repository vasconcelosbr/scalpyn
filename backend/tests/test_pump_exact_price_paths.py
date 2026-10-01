from copy import deepcopy
from datetime import timedelta
import pytest
from test_pump_opportunity import NOW,build
from app.services import pump_opportunity_engine as e


@pytest.mark.parametrize("read_exhausted",[False,True])
def test_exact_batch_shared_reads_queue_and_atomic_completion(monkeypatch,read_exhausted):
    import asyncio,json
    from app import database
    from app.services import pump_opportunity_service as svc
    p=observation();queries=[]
    class Result:
        def __init__(self,rows=()):self.rows=rows
        def mappings(self):return self
        def scalars(self):return self
        def scalar(self):return True
        def all(self):return self.rows
    class DB:
        async def execute(self,query,params=None):
            sql=str(query);queries.append(sql)
            assert 'flow_buckets_1m' not in sql
            if 'SELECT o.payload,q.horizon_minutes' in sql:
                assert params['batch']==512
                return Result([{'payload':p,'horizon':5}])
            if 'selected AS MATERIALIZED' in sql:
                if read_exhausted:return Result([{'exhausted':True,'points':0,'bytes':0,'requested_points':100001,'requested_bytes':20000001,'head_points':100001,'head_bytes':20000001,'accepted_count':0}])
                return Result([{'instrument_id':p['instrument_id'],'payload':path,'points':18,'bytes':2000,'exhausted':False,'accepted_count':1} for path in paths()])
            if 'INSERT INTO pump_opportunity_labels' in sql:
                records=json.loads(params['records']);assert len(records)==1
                assert records[0]['p']['resolution']=='trades_exact_window_v1'
            return Result()
    async def get_config(db,user):return e.config({'labels_enabled':True,'labels':p['label_spec']})
    async def storage(db):return 0
    async def run_db_task(fn,**kwargs):return await fn(DB())
    monkeypatch.setattr(svc,'get_config',get_config)
    monkeypatch.setattr(svc,'storage_bytes',storage)
    monkeypatch.setattr(database,'run_db_task',run_db_task)
    result=asyncio.run(svc.label_batch('fixture'))
    assert result['written']==(0 if read_exhausted else 1)
    assert sum('selected AS MATERIALIZED' in q for q in queries)==1
    assert any('ORDER BY q.ready_at' in q for q in queries)
    assert any('SET completed_at' in q for q in queries) is not read_exhausted
    if read_exhausted:
        assert result['reason']=='individual_window_exceeds_read_budget'
        assert result['resource_block']['retry_policy']=='explicit_review_required'
        assert any('SET resource_block' in q for q in queries)


def observation():
    p=build(decision_at=NOW+timedelta(seconds=3))
    p["label_spec"].update(version="pump_gross_touch_v3",resolution="trades_exact_window_v1",
        future_price_policy="last_trade_asof_endpoint_v1",endpoint_max_age_seconds=60,settle_seconds=63)
    p["manifest"]["label_spec_hash"]=e.canonical_hash(p["label_spec"])
    return p


def test_budget_prefix_only_completes_admitted_horizons(monkeypatch):
    import asyncio,json
    from uuid import UUID
    from app import database
    from app.services import pump_opportunity_service as svc
    original=observation();rows=[];written=[];completed=[]
    for index in range(3):
        p=deepcopy(original);p['observation_id']=str(UUID(int=index+1))
        rows.append({'payload':p,'horizon':5})
    class Result:
        def __init__(self,items=()):self.items=items
        def mappings(self):return self
        def all(self):return self.items
        def scalar(self):return True
    class DB:
        async def execute(self,query,params=None):
            sql=str(query)
            if 'SELECT o.payload,q.horizon_minutes' in sql:return Result(rows)
            if 'selected AS MATERIALIZED' in sql:
                assert [r['n'] for r in json.loads(params['requests'])]==[1,2,3]
                # Only two full windows are admitted; a third stays pending.
                return Result([{'instrument_id':None,'payload':None,'points':18,'bytes':2000,
                    'exhausted':False,'accepted_count':2},*[{'instrument_id':original['instrument_id'],
                    'payload':p,'points':18,'bytes':2000,'exhausted':False,'accepted_count':2} for p in paths()]])
            if 'INSERT INTO pump_opportunity_labels' in sql:written.extend(json.loads(params['records']))
            if 'UPDATE pump_opportunity_label_queue' in sql:completed.extend(json.loads(params['records']))
            return Result()
    async def get_config(db,user):return e.config({'labels_enabled':True,'labels':original['label_spec']})
    async def storage(db):return 0
    async def run(fn,**kwargs):return await fn(DB())
    monkeypatch.setattr(svc,'get_config',get_config);monkeypatch.setattr(svc,'storage_bytes',storage)
    monkeypatch.setattr(database,'run_db_task',run)
    result=asyncio.run(svc.label_batch('fixture'))
    assert result['requested']==3 and result['accepted']==result['written']==2
    assert [r['id'] for r in written]==[r['payload']['observation_id'] for r in rows[:2]]
    assert completed==written and rows[2]['payload']['observation_id'] not in {r['id'] for r in completed}


def paths():
    out=[]
    for i in range(6):
        start=(NOW+timedelta(minutes=i)).timestamp()*1000
        out.append({"bucket_start_ms":int(start),"complete":True,"points":[[start+1000,100.1,f"{i}a"],[start+2000,100.2,f"{i}b"],[start+59000,100.3,f"{i}c"]]})
    return out


def test_exact_window_excludes_predecision_and_postendpoint_highs():
    ps=paths();ps[0]["points"][0][1]=102;ps[-1]["points"][-1][1]=102
    result=e.exact_gross_label(observation(),ps,5,NOW+timedelta(minutes=7))
    assert result["targets"]["0.8"]["hit"] is False
    assert result["coverage_complete"] and result["boundary_ambiguous"] is False
    assert result["endpoint_age_seconds"]==1


def test_exact_target_after_decision_has_observed_second_timestamp():
    ps=paths();ps[1]["points"][0][1]=101
    result=e.exact_gross_label(observation(),ps,5,NOW+timedelta(minutes=7))
    target=result["targets"]["0.8"]
    assert target["hit"] and target["time_to_touch_seconds"]==58 and not target["first_touch_censored"]


def test_exact_gap_preserves_unknown_non_touch_and_partial_extremes():
    ps=paths();ps[1]["complete"]=False
    result=e.exact_gross_label(observation(),ps,5,NOW+timedelta(minutes=7))
    assert result["targets"]["0.8"]["hit"] is None and result["mfe_pct"] is None
    assert result["endpoint_return_pct"] is not None


def test_exact_hit_after_gap_is_true_but_first_time_censored():
    ps=paths();ps[0]["complete"]=False;ps[2]["points"][1][1]=101
    target=e.exact_gross_label(observation(),ps,5,NOW+timedelta(minutes=7))["targets"]["0.8"]
    assert target["hit"] and target["first_touch_censored"] and target["time_to_touch_seconds"] is None


def test_exact_endpoint_never_uses_later_trade():
    ps=paths();ps[-1]["points"]=[[ps[-1]["bucket_start_ms"]+4000,101,"late"]]
    result=e.exact_gross_label(observation(),ps,5,NOW+timedelta(minutes=7))
    assert result["endpoint_age_seconds"]==4
    assert result["endpoint_return_pct"]==pytest.approx(.3)


def test_exact_stale_endpoint_is_unknown_not_zero():
    ps=paths();ps[-1]["points"]=[];ps[-2]["points"]=[]
    result=e.exact_gross_label(observation(),ps,5,NOW+timedelta(minutes=7))
    assert result["endpoint_return_pct"] is None


def test_exact_same_timestamp_target_and_downside_order_is_ambiguous():
    ps=paths();stamp=ps[2]["points"][0][0];ps[2]["points"]=[[stamp,101,"a"],[stamp,97,"b"]]
    result=e.exact_gross_label(observation(),ps,5,NOW+timedelta(minutes=7))
    assert result["order_ambiguous"] and result["conservative_stop_first"]


def test_exact_maturity_waits_for_endpoint_minute_availability():
    assert e.exact_gross_label(observation(),paths(),5,NOW+timedelta(minutes=6))["status"]=="pending"


def test_raw_archival_deduplicates_ids_and_marks_budget_truncation():
    stamp=NOW.timestamp()*1000
    raw={"source":"rest_fallback","trades":[{"trade_id":1,"ts_ms":stamp+1000,"price":"100"},{"trade_id":1,"ts_ms":stamp+1000,"price":"100"},{"trade_id":2,"ts_ms":stamp+2000,"price":"101"}]}
    bucket={"bucket_start_ms":stamp,"partial":False}
    full=e.price_path(raw,bucket,10);truncated=e.price_path(raw,bucket,1)
    assert full["complete"] and len(full["points"])==2
    assert not truncated["complete"] and truncated["reported_points"]==2 and truncated["reason"]=="price_point_budget_exceeded"
    assert e.canonical_hash(full)!=e.canonical_hash(truncated)


def test_raw_ws_without_liveness_never_certifies_absence():
    path=e.price_path({"source":"gate_trades_ws_spot","trades":[],"alive_slots":None},{"bucket_start_ms":NOW.timestamp()*1000,"partial":False},100)
    assert not path["complete"] and path["reason"]=="ws_liveness_unavailable"


def test_new_exact_contract_is_distinct_from_minute_labels():
    p=observation();assert e.canonical_hash(p["label_spec"])!=e.canonical_hash(build()["label_spec"])
    c=e.config({"labels":p["label_spec"]});assert c["labels"]["resolution"]=="trades_exact_window_v1"


def test_exact_valid_cost_policy_is_contextual_never_an_exit_command():
    p=observation();p["label_spec"]["cost_policy"]={"roundtrip_pct":.2}
    result=e.exact_gross_label(p,paths(),5,NOW+timedelta(minutes=7))
    assert result["net_return_pct"]==pytest.approx(result["endpoint_return_pct"]-.2)
