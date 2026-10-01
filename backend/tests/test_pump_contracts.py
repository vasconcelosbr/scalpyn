from copy import deepcopy
from datetime import timedelta
import pytest
from test_pump_opportunity import NOW,build
from app.services import pump_opportunity_engine as e
from app.services.pump_contracts import FEATURE_SPEC,gate_listing_record,validate_listing,validate_manifest

def pair():return {"id":"BTC_USDT","base":"BTC","quote":"USDT","buy_start":1600000000,"sell_start":1600000000,"trade_status":"tradable"}

def test_listing_zero_unknown_no_epoch_invention():
    assert not gate_listing_record({**pair(),"buy_start":0,"sell_start":0},NOW)["certified"]
    assert not build()["manifest"]["listing_certified"]  # ID without evidence cannot certify

def test_listing_evidence_epoch_cutover_and_hash():
    record=gate_listing_record(pair(),NOW)
    assert validate_listing(record,"BTC_USDT")
    c=e.config({"listing_ids":{"BTC_USDT":record["listing_id"]},"listing_records":{"BTC_USDT":record}})
    p=build(c=c);assert p["manifest"]["listing_certified"]
    changed=deepcopy(record);changed["evidence"]["buy_start"]+=1
    assert not validate_listing(changed,"BTC_USDT")
    assert gate_listing_record({**pair(),"buy_start":1600000001},NOW)["listing_id"]!=record["listing_id"]

def spec():
    return {"features":["rsi","adx"],"feature_spec_hash":e.canonical_hash(FEATURE_SPEC),
        "producer_config_hash":"fixture-source","label_spec":build()["label_spec"],"label_spec_hash":build()["manifest"]["label_spec_hash"],"reference_policy":"gate_best_ask_v1",
        "boundaries":[(NOW+timedelta(days=i)).isoformat() for i in (1,2,3)],"embargo_seconds":7200,
        "min_episodes":20,"min_days":4,"min_instruments":3,"max_rows":1000,"max_threads":1,
        "decision_threshold":.5,"cost_policy":None,"cost_policy_hash":e.canonical_hash(None),
        "support_criteria":{"test_only":True}}

@pytest.mark.parametrize("change",[{"features":["rsi","rsi"]},{"features":["invented"]},{"embargo_seconds":7199},
    {"feature_spec_hash":"wrong"},{"cost_policy":{"roundtrip_pct":.1}},{"min_days":0},{"support_criteria":{}},
    {"boundaries":[NOW.isoformat()]*3}])
def test_frozen_manifest_rejects_incompatible_or_ambiguous_contracts(change):
    with pytest.raises(ValueError):validate_manifest({**spec(),**change})

def test_manifest_freezes_gross_cost_unknown_without_fake_net():
    frozen=validate_manifest(spec());assert frozen["spec"]["cost_policy"] is None
    assert frozen["auto_promotion"] is False and frozen["applied_delta"]==0
