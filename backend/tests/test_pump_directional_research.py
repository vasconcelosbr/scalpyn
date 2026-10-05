"""Directional semantics, temporal integrity and native artifact round-trip."""
import copy
from datetime import datetime,timedelta,timezone
import pytest
from app.services.pump_opportunity_engine import config,canonical_hash
from app.services.pump_contracts import FEATURE_SPEC
from app.services.pump_directional_research import (OBJECTIVE,directional_target,episode_weights,
    train_directional,directional_preview,load_directional_preview)

@pytest.mark.parametrize('label',[
    {'status':'unknown','coverage_complete':True,'endpoint_return_pct':-.1},
    {'status':'known','coverage_complete':False,'endpoint_return_pct':-.1},
    {'status':'known','coverage_complete':True,'endpoint_return_pct':None},
    {'status':'known','coverage_complete':True,'endpoint_return_pct':False},
    {'status':'known','coverage_complete':True,'endpoint_return_pct':0},
    {'status':'known','coverage_complete':True,'endpoint_return_pct':-100},
])
def test_unknown_flat_or_invalid_does_not_become_down(label):assert directional_target(label) is None

def test_direction_uses_endpoint_not_positive_touch():
    label={'status':'known','coverage_complete':True,'endpoint_return_pct':-.1,'targets':{'0.8':{'hit':True}}}
    assert directional_target(label) is False
    assert directional_target({**label,'endpoint_return_pct':.1}) is True

def fixture():
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    labels=config()['labels'];fh=canonical_hash(FEATURE_SPEC);lh=canonical_hash(labels);ch=canonical_hash(None)
    spec={'features':['rsi','adx'],'feature_spec_hash':fh,'producer_config_hash':'fixture-source',
          'label_spec':labels,'label_spec_hash':lh,'reference_policy':'gate_best_ask_v1','cost_policy':None,'cost_policy_hash':ch,
          'boundaries':[(start+timedelta(days=i)).isoformat() for i in (3,6,9)],'embargo_seconds':7200,
          'min_episodes':20,'min_days':4,'min_instruments':3,'max_rows':400,'max_threads':1,
          'params':{'n_estimators':5,'max_depth':2,'random_state':20261001},'decision_threshold':.5,
          'support_criteria':{'test_only':True},
          'directional_target':{'version':OBJECTIVE,'horizon_minutes':10,'reference_policy':'gate_best_ask_v1',
                                'flat_policy':'exclude_exact_zero','unknown_policy':'exclude','return_policy':'endpoint_gross'},
          'directional_evaluation':{'bootstrap_repetitions':20,'reliability_bins':5,'calibration_C':1.0,'calibration_max_iter':1000}}
    rows=[]
    for i in range(320):
        up=i%4 in (0,3)
        rows.append({'observation_id':str(i),'decision_at':(start+timedelta(minutes=45*i)).isoformat(),
                     'episode_id':str(i),'instrument_id':str(i%10),'horizon_minutes':10,
                     'manifest':{'listing_certified':True,'feature_spec_hash':fh,'label_spec_hash':lh,
                                 'cost_policy_hash':ch,'legacy_config_hash':'fixture-source'},
                     'values':{'rsi':40 if i%2 else 60,'adx':20 if i%4<2 else 40},
                     'reference':{'price':100,'policy':'gate_best_ask_v1'},
                     'label_status':'known','label_coverage_complete':True,'endpoint_return_pct':.1 if up else -.1,
                     'target':not up}) # deliberately incompatible old touch target
    return rows,spec

def test_repeated_episode_receives_same_total_weight():
    assert episode_weights([{'episode_id':'a'},{'episode_id':'a'},{'episode_id':'b'}])==[.5,.5,1]

def test_directional_native_fit_and_preview_are_not_activated(tmp_path):
    rows,spec=fixture();result=train_directional(rows,spec=spec,output_root=tmp_path)
    m=result['metrics'];manifest=result['manifest']
    assert m['support']['observations']==320 and m['purged_or_embargoed_rows']>0
    assert sum(b['observations'] for b in m['reliability'])==m['cohort_rows'][-1]
    assert all('observed_up_frequency' in b for b in m['reliability'])
    assert len(m['paired_episode_brier_ci95'])==2
    assert manifest['artifact_namespace'].startswith('pump_directional/')
    p=load_directional_preview(rows[0]['values'],tmp_path/manifest['artifact_namespace'])
    assert p['score'] is None and p['probability'] is None and p['direction'] is None
    assert p['applied_delta']==0 and p['status']=='abstained'
    assert 0<=p['research_only']['estimated_up_probability']<=1
    assert set(p['research_only']['joint_model_contributions_log_odds'])=={'rsi','adx'}
    assert manifest['auto_promotion'] is False

def test_missing_and_out_of_range_abstain_before_prediction():
    _,spec=fixture();manifest={'spec':spec,'experiment_id':'fixture','objective':OBJECTIVE,'status':'challenger',
                             'feature_bounds':{'rsi':{'min':40,'max':60},'adx':{'min':20,'max':40}}}
    def forbidden(_):raise AssertionError('must not predict')
    assert directional_preview({'rsi':None,'adx':30},manifest,forbidden)['reason']=='missing_or_invalid_features'
    assert directional_preview({'rsi':100,'adx':30},manifest,forbidden)['reason']=='outside_training_feature_range'

def test_cross_cut_episode_is_removed_from_all_cohorts(tmp_path):
    rows,spec=fixture()
    for r in rows:r['episode_id']='one-crossing-episode'
    spec['min_episodes']=1
    with pytest.raises(ValueError,match='Both directions required'):train_directional(rows,spec=spec,output_root=tmp_path)

def test_mixed_contracts_and_unknown_labels_are_excluded(tmp_path):
    rows,spec=fixture()
    for r in rows:r['label_status']='unknown'
    with pytest.raises(ValueError,match='Insufficient independent'):train_directional(rows,spec=spec,output_root=tmp_path)


@pytest.mark.parametrize('price',[0,-1,None,False])
def test_invalid_reference_price_never_becomes_directional_example(tmp_path,price):
    rows,spec=fixture()
    for r in rows:r['reference']['price']=price
    with pytest.raises(ValueError,match='Insufficient independent'):train_directional(rows,spec=spec,output_root=tmp_path)

def test_directional_query_uses_known_endpoint_and_snapshot():
    from pump_ml.selection import DIRECTIONAL_ROWS_SQL,ROWS_SQL
    assert "l.payload->>'status'='known'" in ROWS_SQL
    assert 'horizon_minutes=$8 AND labeled_at<=$9' in DIRECTIONAL_ROWS_SQL
    assert 'o.published_at<=$9' in DIRECTIONAL_ROWS_SQL
    assert "l.payload->'targets'->'0.8'->>'hit'" not in DIRECTIONAL_ROWS_SQL
    assert "'endpoint_return_pct'" in DIRECTIONAL_ROWS_SQL
