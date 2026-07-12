from app.services.ml_challenger_service import (
    _barrier_contract_key,
    _hybrid_approved_contract_policy,
)


def _record(mode, tp):
    return {"barrier_mode": mode, "tp_pct_applied": tp}


def test_hybrid_keeps_all_contracts_before_configured_gate():
    records = [_record("ATR_DYNAMIC", 1.5), _record("FIXED", 1.0)]
    selected, metadata = _hybrid_approved_contract_policy(
        records,
        expected_mode="ATR_DYNAMIC",
        expected_tp_pct=1.5,
        atr_dynamic_only_min_rows=2,
    )
    assert selected == records
    assert metadata["contract_strategy"] == "CONTRACT_AWARE_HYBRID"
    assert metadata["homogeneous_contract_rows"] == 1


def test_hybrid_migrates_only_after_configured_gate():
    atr = [_record("ATR_DYNAMIC", 1.5), _record("ATR_DYNAMIC", 1.5)]
    selected, metadata = _hybrid_approved_contract_policy(
        [*atr, _record("FIXED", 1.0)],
        expected_mode="ATR_DYNAMIC",
        expected_tp_pct=1.5,
        atr_dynamic_only_min_rows=2,
    )
    assert selected == atr
    assert metadata["contract_strategy"] == "ATR_DYNAMIC_ONLY"


def test_contract_key_is_stable_and_explicit():
    assert _barrier_contract_key(_record("atr_dynamic", 1.5)) == "ATR_DYNAMIC|1.5"
    assert _barrier_contract_key({}) == "<NULL>|<NULL>"
