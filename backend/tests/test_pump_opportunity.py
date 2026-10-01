"""Approved Pump v2 temporal, grouping, isolation and failure contracts."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from uuid import UUID
import json
import pytest
from app.services import pump_opportunity_engine as e
from app.services.pump_ml_research import selection_metrics,train_challenger

NOW=datetime(2026,10,1,12,1,tzinfo=timezone.utc)
OWNER="8080110c-ee9d-4a2b-a53f-6bef86dd8867"


def build(**kwargs):
    c=e.config({"listing_ids":{"BTC_USDT":"gate-btc-spot-listing-1"}})
    values={"delta_norm":0.2,"buy_persistence":0.8,"cvd_slope":0.3,"rvol_strict":2,
            "price_progress_atr":1,"breakout_hold_ratio":0.8,"estimated_slippage_buy_pct":0.01,
            "ask_depth_usdt_1pct":10000,"spread_pct":0.02,"rsi":70,"adx":30,"ema_9":101}
    data={"user_id":OWNER,"row":{"symbol":"BTC_USDT","indicators":{k:{"value":v} for k,v in values.items()}},
          "book":{"asks":[[100,10]],"bids":[[99.99,10]],"observed_at":NOW.isoformat()},
          "source_meta":{},"legacy_config":{"_meta":{"config_hash":"legacy"},"universe_filter":{"min_market_cap_usd":1e9}},
          "c":c,"decision_at":NOW}
    data.update(kwargs)
    return e.build_observation(**data)


def bars(n=120):
    return [{"bucket_start":NOW+timedelta(minutes=i),"high_price":100.5,"low_price":99.9,
             "close_price":100.1,"partial":False} for i in range(n)]


def test_T01_future_feature_is_not_available():
    p=build(source_meta={"delta_norm":{"available_at":(NOW+timedelta(seconds=1)).isoformat()}})
    assert p["values"]["delta_norm"] is None
    assert p["ledger"][0]["result"] is None


def test_T02_pre_decision_boundary_high_does_not_count():
    p=build(decision_at=NOW+timedelta(seconds=3)); b=bars(6);b[0]["high_price"]=102
    label=e.gross_label(p,b,5,NOW+timedelta(minutes=6))
    assert label["targets"]["0.8"]["hit"] is None
    assert label["boundary_ambiguous"]


def test_T03_minute_seven_hit_is_not_five_minute_hit():
    b=bars();b[6]["high_price"]=101
    p=build()
    assert e.gross_label(p,b,5,NOW+timedelta(minutes=130))["targets"]["0.8"]["hit"] is False
    assert e.gross_label(p,b,15,NOW+timedelta(minutes=130))["targets"]["0.8"]["hit"] is True


def test_T04_two_gross_targets_are_distinct():
    b=bars();b[1]["high_price"]=100.7
    result=e.gross_label(build(),b,5,NOW+timedelta(minutes=10))
    assert result["targets"]["0.6"]["hit"] is True
    assert result["targets"]["0.8"]["hit"] is False


@pytest.mark.parametrize("book",[None,{"asks":[[100,1]],"bids":[[99,1]],"observed_at":(NOW-timedelta(minutes=2)).isoformat()}])
def test_T05_missing_stale_reference_unknown(book):
    p=build(book=book);result=e.gross_label(p,bars(),5,NOW+timedelta(minutes=10))
    assert p["reference"]["price"] is None and not p["simulation"]["eligible"]
    assert result["status"]=="unknown" and result["reason"]=="reference_unavailable"


@pytest.mark.parametrize("missing",[1,2])
def test_T06_gaps_never_prove_non_touch(missing):
    b=bars(10)[missing:]
    result=e.gross_label(build(),b,10,NOW+timedelta(minutes=20))
    assert result["targets"]["0.8"]["hit"] is None and result["missing_minutes"]==missing


def test_T07_missing_endpoint_partial_extremes():
    result=e.gross_label(build(),bars(4),5,NOW+timedelta(minutes=10))
    assert result["endpoint_return_pct"] is None and result["mfe_pct"] is None
    assert result["partial_mfe_lower_bound_pct"] is not None


def test_T08_order_within_same_bar_ambiguous():
    b=bars();b[1].update(high_price=101,low_price=97)
    result=e.gross_label(build(),b,5,NOW+timedelta(minutes=10))
    assert result["order_ambiguous"] and result["conservative_stop_first"]


def test_T09_named_version_does_not_hide_changed_cost_contract():
    c=e.config();changed=deepcopy(c);changed["labels"]["cost_policy"]={"roundtrip_fee_pct":0.2}
    assert c["labels"]["version"]==changed["labels"]["version"]
    assert e.canonical_hash(c["labels"])!=e.canonical_hash(changed["labels"])


def test_T10_json_roundtrip_preserves_provenance():
    p=build();roundtrip=json.loads(json.dumps(p))
    assert roundtrip==p
    assert e.canonical_hash(roundtrip)==e.canonical_hash(p)


def test_T11_relisting_is_new_instrument_and_episode():
    p=build();c=e.config({"listing_ids":{"BTC_USDT":"gate-btc-listing-2"}})
    second=build(c=c,previous=p,decision_at=NOW+timedelta(minutes=1))
    assert p["instrument_id"]!=second["instrument_id"] and p["episode_id"]!=second["episode_id"]


def test_T12_legacy_support_contract_regression_is_separate():
    # Existing universe regression suite exercises drain exclusion end-to-end.
    # New ingest consumes only rows built for eligible candidates, never collected keys.
    from pathlib import Path
    source=(Path(__file__).parents[1]/"app/services/pump_opportunity_service.py").read_text()
    assert "for row in sorted(rows" in source
    assert "for row in collected" not in source


def test_T13_correlated_flow_caps_at_one_confirmation():
    p=build();flow=[x for x in p["ledger"] if x["group"]=="flow"]
    assert len(flow)==1 and flow[0]["points"]==10
    assert len(flow[0]["conditions"])==4
    assert p["score_base"]==30


def test_T14_literal_AND_ranges_and_field_relationship():
    rules=[{"field":"rsi","op":"between","value":[65,76]},{"field":"adx","op":"gt","value":25},
           {"field":"ema_9","op":"gt","other_field":"price"}]
    values={"rsi":65,"adx":26,"ema_9":101,"price":100}
    assert all(e.condition(values,r) is True for r in rules)
    assert e.condition({**values,"price":102},rules[-1]) is False
    assert e.condition({**values,"rsi":None},rules[0]) is None
    assert e.condition(values,{**rules[0],"include_lower":False}) is False


def test_T15_veto_beats_score_and_visual_hysteresis():
    p=build();row={"symbol":"BTC_USDT","indicators":{k:{"value":v} for k,v in p["values"].items()},"exhaustion_flag":True}
    blocked=build(row=row,previous=p,decision_at=NOW+timedelta(minutes=1))
    assert blocked["score_base"]==30 and blocked["state"]=="invalidado" and not blocked["simulation"]["eligible"]


def test_T16_visual_state_does_not_keep_admission():
    p=build();row={"symbol":"BTC_USDT","indicators":{k:{"value":v} for k,v in p["values"].items()}}
    row["indicators"]["price_progress_atr"]["value"]=-1
    row["indicators"]["breakout_hold_ratio"]["value"]=0
    weak=build(row=row,previous=p,decision_at=NOW+timedelta(seconds=30))
    assert weak["state"]=="enfraquecendo" and not weak["simulation"]["eligible"]
    assert weak["episode_reference"]==p["episode_reference"]


def test_T17_no_model_is_neutral_without_losing_indicators():
    p=build();assert p["ml"]["delta"]==0 and p["ml"]["probability"] is None and p["score_base"]==30


def test_T18_no_confirmations_no_forced_top_n_admission():
    p=build();row={"symbol":"BTC_USDT","indicators":{k:{"value":v} for k,v in p["values"].items()}}
    for k in ("delta_norm","price_progress_atr","breakout_hold_ratio"):row["indicators"][k]["value"]=-1
    p=build(row=row);assert p["score_base"]==0 and not p["simulation"]["eligible"]


def test_T19_legacy_score_units_rejected():
    with pytest.raises(ValueError,match="SCORE_UNIT_MISMATCH"):e.config({"score_unit":"0-100"})


def test_T20_same_slot_id_stays_identical_but_actual_decision_time_visible():
    p=build();later=build(decision_at=NOW+timedelta(seconds=30))
    assert later["observation_id"]==p["observation_id"] and later["decision_at"]!=p["decision_at"]
    assert build(decision_at=NOW+timedelta(minutes=1))["observation_id"]!=p["observation_id"]


def test_T21_120min_label_crossing_cut_is_purged():
    rows=[{"decision_at":(NOW+timedelta(minutes=i)).isoformat(),"episode_id":str(i)} for i in (0,100,200,400)]
    train,test=e.purged_split(rows,NOW+timedelta(minutes=250),3600)
    assert [r["episode_id"] for r in train]==["0","100"]
    assert [r["episode_id"] for r in test]==["400"]


def test_T22_many_minutes_do_not_replace_independent_support(tmp_path):
    p=build();p.update(target=True,label_coverage_complete=True)
    spec={"features":["rsi"],"boundaries":[NOW.isoformat()]*3,"embargo_seconds":3600,
          "min_episodes":2,"min_days":1,"min_instruments":1,"max_rows":100,"max_threads":1,
          "params":{},"decision_threshold":0.5,"cost_policy_hash":p["manifest"]["cost_policy_hash"],"support_criteria":"explicit test"}
    with pytest.raises(ValueError,match="independent Pump support"):train_challenger([p]*50,spec=spec,output_root=tmp_path)
    assert not list(tmp_path.iterdir())


def test_T23_rejected_winners_and_recall_are_reported():
    m=selection_metrics([True,True,False],[True,False,False])
    assert m["precision"]==1 and m["recall"]==0.5 and m["rejected_winners"]==1


def test_T24_owner_scope_changes_observation_and_episode_identity():
    p=build();other=build(user_id=str(UUID(int=1)))
    assert p["observation_id"]!=other["observation_id"] and p["episode_id"]!=other["episode_id"]


def test_T25_explicit_resource_budget_precedes_heavy_import(tmp_path):
    with pytest.raises(ValueError,match="manifest required"):train_challenger([],spec={},output_root=tmp_path)


def test_T26_flags_reject_unapproved_delta_or_pool_connection():
    for flag in ("ml_delta_enabled","pool_connection_enabled","training_enabled","inference_enabled"):
        with pytest.raises(ValueError):e.config({flag:True})
    assert e.config({"enabled":False})["ml_delta_enabled"] is False


def test_T27_selected_reference_is_immutable_across_new_observations():
    p=build();before=json.dumps(p)
    new=build(book={"asks":[[102,1]],"bids":[[101,1]],"observed_at":(NOW+timedelta(minutes=1)).isoformat()},
              decision_at=NOW+timedelta(minutes=1),previous=p)
    assert json.dumps(p)==before and new["episode_reference"]["price"]==100 and new["reference"]["price"]==102


def test_T28_no_shadow_execution_dependencies():
    from pathlib import Path
    for name in ("pump_opportunity_engine.py","pump_opportunity_service.py","pump_ml_research.py"):
        source=(Path(__file__).parents[1]/"app/services"/name).read_text()
        assert "execute_buy" not in source and "shadow_trade_service" not in source
        assert "train_challenger" not in source or name=="pump_ml_research.py"


def test_T29_hit_after_gap_first_touch_is_censored():
    b=bars();b[3]["high_price"]=101;b=b[1:]
    result=e.gross_label(build(),b,5,NOW+timedelta(minutes=10))
    target=result["targets"]["0.8"]
    assert target["hit"] is True and target["first_touch_censored"] and target["first_touch_interval"] is None


def test_label_not_mature_is_pending_not_unknown():
    assert e.gross_label(build(),bars(),5,NOW+timedelta(minutes=4))["status"]=="pending"


def test_nonfinite_and_future_quote_block_without_fabricated_price():
    for price,at in [(float("nan"),NOW),(100,NOW+timedelta(seconds=1))]:
        p=build(book={"asks":[[price,1]],"bids":[[99,1]],"observed_at":at.isoformat()})
        assert p["reference"]["price"] is None and not p["simulation"]["eligible"]


def test_stale_feature_never_renormalizes_remaining_groups():
    p=build(source_meta={"delta_norm":{"age_seconds":1000,"stale":True}})
    assert p["score_base"]==20 and p["score_final"] is None


def test_unverified_listing_is_explicit_and_excluded_from_ml(tmp_path):
    assert build(c=e.config())["manifest"]["listing_certified"] is False


def test_no_active_breakout_is_not_confirmed_rather_than_missing_feed():
    p=build(); row={"symbol":"BTC_USDT","indicators":{k:{"value":v} for k,v in p["values"].items()}}
    row["indicators"]["breakout_hold_ratio"]={"value":None,"reason":"no_active_breakout"}
    p=build(row=row)
    assert p["score_final"]==20 and p["simulation"]["eligible"] and p["ledger"][2]["result"] is False


def test_ema_values_use_the_governed_source_without_touching_legacy_row():
    row={"symbol":"BTC_USDT","indicators":{},"opportunity_source_values":{"ema9":101}}
    p=build(row=row)
    assert p["values"]["ema9"]==101 and not row["indicators"]


def test_config_cannot_split_correlated_flow_into_multiple_groups():
    with pytest.raises(ValueError,match="single capped"):
        e.config({"groups":{"extra_flow":{"points":10,"conditions":[{"field":"taker_ratio","op":"gt","value":0.5}]}}})
