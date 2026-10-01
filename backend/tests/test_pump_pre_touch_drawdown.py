"""V4 pre-touch adverse excursion never substitutes whole-horizon MAE."""
from copy import deepcopy
from datetime import timedelta
import pytest
from app.services import pump_opportunity_engine as e
from test_pump_exact_price_paths import observation, paths, NOW


POLICY="reference_to_first_touch_strict_timestamp_v1"


def v4():
    p=observation()
    p["label_spec"].update(version="pump_gross_touch_v4",pre_touch_policy=POLICY)
    p["manifest"]["label_spec_hash"]=e.canonical_hash(p["label_spec"])
    return p


def label(ps,p=None):
    return e.exact_gross_label(p or v4(),ps,5,NOW+timedelta(minutes=7))


def test_targets_have_distinct_prefixes_and_exclude_later_drawdown():
    ps=paths();stamp=ps[1]["bucket_start_ms"]
    ps[1]["points"]=[[stamp+1000,99,"a"],[stamp+2000,100.7,"b"],
                      [stamp+3000,98,"c"],[stamp+4000,100.9,"d"],
                      [stamp+5000,90,"e"]]
    r=label(ps)
    assert r["targets"]["0.6"]["pre_touch"]["drawdown_before_touch_pct"]==pytest.approx(1)
    assert r["targets"]["0.8"]["pre_touch"]["drawdown_before_touch_pct"]==pytest.approx(2)
    assert r["mae_pct"]==pytest.approx(-10)


def test_no_drop_uses_reference_zero_and_exact_touch_is_not_adverse():
    ps=paths();ps[1]["points"][0][1]=100.8
    metric=label(ps)["targets"]["0.8"]["pre_touch"]
    assert metric["status"]=="known" and metric["drawdown_before_touch_pct"]==0
    assert metric["mae_before_touch_pct"]==0


def test_boundary_prices_outside_selected_window_are_excluded():
    ps=paths();ps[0]["points"][0][1]=1
    ps[-1]["points"]=[[ps[-1]["bucket_start_ms"]+4000,1,"after"]]
    ps[1]["points"][0][1]=101
    assert label(ps)["targets"]["0.8"]["pre_touch"]["drawdown_before_touch_pct"]==0


@pytest.mark.parametrize("gap_index",[0,1])
def test_gap_before_or_in_touch_minute_is_unknown_and_censored(gap_index):
    ps=paths();ps[gap_index]["complete"]=False;ps[1]["points"][0][1]=101
    t=label(ps)["targets"]["0.8"]
    assert t["hit"] and t["first_touch_censored"]
    assert t["pre_touch"]["status"]=="unknown"
    assert t["pre_touch"]["drawdown_before_touch_pct"] is None


def test_gap_after_touch_does_not_censor_prefix_or_fabricate_horizon_mae():
    ps=paths();ps[1]["points"][0][1]=101;ps[2]["complete"]=False
    r=label(ps);t=r["targets"]["0.8"]
    assert not t["first_touch_censored"] and t["pre_touch"]["status"]=="known"
    assert r["mae_pct"] is None and not r["coverage_complete"]


@pytest.mark.parametrize("lower_first",[False,True])
def test_same_timestamp_lower_trade_is_ambiguous_regardless_trade_id_order(lower_first):
    ps=paths();stamp=ps[1]["bucket_start_ms"]+1000
    ps[1]["points"]=[[stamp,97,"a" if lower_first else "z"],[stamp,101,"z" if lower_first else "a"]]
    t=label(ps)["targets"]["0.8"]
    assert t["hit"] and not t["first_touch_censored"]
    assert t["pre_touch"]["order_ambiguous"]
    assert t["pre_touch"]["drawdown_before_touch_pct"] is None
    assert t["pre_touch"]["reason"]=="touch_timestamp_order_ambiguous"


def test_same_timestamp_all_above_target_has_known_prefix():
    ps=paths();stamp=ps[1]["bucket_start_ms"]+1000
    ps[1]["points"]=[[stamp,100.8,"a"],[stamp,101,"b"]]
    assert label(ps)["targets"]["0.8"]["pre_touch"]["status"]=="known"


def test_no_hit_distinguishes_not_reached_from_unknown():
    ps=paths()
    assert label(ps)["targets"]["0.8"]["pre_touch"]["status"]=="not_reached"
    ps[1]["complete"]=False
    assert label(ps)["targets"]["0.8"]["pre_touch"]["status"]=="unknown"


def test_pending_window_does_not_claim_pre_touch_result():
    r=e.exact_gross_label(v4(),paths(),5,NOW+timedelta(minutes=6))
    assert r["status"]=="pending" and not r["targets"]


def test_v3_output_unchanged_and_v4_contract_hash_is_distinct():
    p=observation();frozen=deepcopy(p);ps=paths();ps[1]["points"][0][1]=101
    old=label(ps,p);new=label(ps,v4())
    assert all("pre_touch" not in t for t in old["targets"].values())
    assert old["label_spec_hash"]!=new["label_spec_hash"]
    for target in old["targets"]:
        assert old["targets"][target]=={k:v for k,v in new["targets"][target].items() if k!="pre_touch"}
    assert p==frozen


def test_v4_policy_is_required_and_cannot_be_attached_to_old_contract():
    assert e.config({"labels":v4()["label_spec"]})["labels"]["pre_touch_policy"]==POLICY
    wrong=v4()["label_spec"];wrong.pop("pre_touch_policy")
    with pytest.raises(ValueError,match="V4 requires"):e.config({"labels":wrong})
    wrong=observation()["label_spec"];wrong["pre_touch_policy"]=POLICY
    with pytest.raises(ValueError,match="requires pump_gross_touch_v4"):e.config({"labels":wrong})


def test_gap_at_exact_touch_timestamp_never_certifies_pre_touch_prefix():
    ps=paths();ps[1]["complete"]=False
    ps[1]["points"]=[[ps[1]["bucket_start_ms"],101,"boundary"]]
    t=label(ps)["targets"]["0.8"]
    assert t["pre_touch"]["status"]=="unknown"
    assert t["pre_touch"]["reason"]=="gap_before_or_at_observed_touch"


def test_intelligence_exposes_separate_frozen_contract_support():
    import asyncio
    from app.services import pump_opportunity_service as svc
    old=observation();new=v4();ps=paths();ps[1]["points"][0][1]=101
    rows=[{"payload":old,"label":label(ps,old)},{"payload":new,"label":None}]
    class Result:
        def __init__(self,items):self.items=items
        def mappings(self):return self
        def all(self):return self.items
    class DB:
        async def execute(self,query,params):
            return Result(rows if "LEFT JOIN pump_opportunity_labels" in str(query) else [])
    result=asyncio.run(svc.intelligence(DB(),"fixture"))
    cohorts={c["label_version"]:c for c in result["contract_cohorts"]}
    assert cohorts["pump_gross_touch_v3"]["known"]==1
    assert cohorts["pump_gross_touch_v4"]["known"]==0
    assert cohorts["pump_gross_touch_v4"]["unknown_or_pending"]==1
    assert cohorts["pump_gross_touch_v3"]["label_spec_hash"]!=cohorts["pump_gross_touch_v4"]["label_spec_hash"]
    assert result["baseline"]["observations"]==2 and not result["baseline"]["probability_validated"]
