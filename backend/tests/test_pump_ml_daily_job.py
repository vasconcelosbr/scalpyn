import asyncio,sys
from pathlib import Path
from uuid import UUID,uuid4
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from pump_ml.job import run_owner,prepare

@pytest.mark.parametrize("busy,prior,expected",[(True,None,"singleton_busy"),(False,uuid4(),"daily_already_recorded"),(False,None,"blocked")])
def test_daily_singleton_and_missing_listing_stop_before_training(monkeypatch,busy,prior,expected):
    async def no_contract(*args):return None,[]
    monkeypatch.setattr('pump_ml.selection.select_temporal_training_rows',no_contract)
    calls=[]
    class DB:
        async def fetchval(self,sql,*args):
            calls.append(sql)
            if 'pg_try_advisory_lock' in sql:return not busy
            if 'SELECT run_id' in sql:return prior
            if 'SELECT config_json' in sql:return {'enabled':True,'training_job_enabled':True}
            if 'pg_total_relation_size' in sql:return 0
            return None
        async def execute(self,sql,*args):calls.append(sql)
    monkeypatch.setitem(sys.modules,'xgboost',None)
    result=asyncio.run(run_owner(DB(),UUID(int=1)))
    assert result['status']==expected
    assert sum('INSERT INTO pump_ml_job_runs' in q for q in calls)==(1 if expected=='blocked' else 0)
    assert not any('INSERT INTO pump_ml_experiments' in q for q in calls)
    if expected=='blocked':assert result['reason']=='no_certified_point_in_time_listing_cohort'

def test_daily_job_cannot_train_micro_sample():
    with pytest.raises(ValueError,match='insufficient_compatible_rows'):prepare([])
