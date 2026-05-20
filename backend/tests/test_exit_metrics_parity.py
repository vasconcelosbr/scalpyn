"""Task #316 — validate_parity diagnostic semantics.

Garante que:
* ok → status='ok', missing=[], extra=[], coverage=100
* missing → status='partial_divergence' + missing populado
* extra → status='partial_divergence' + extra populado
* _capture_error → status='capture_error' (não compara catálogo)
* chaves internas (EXIT_METRICS_INTERNAL_KEYS) NÃO entram na diff
* nested entry é flatten antes da comparação
"""

from app.services.exit_metrics import (
    EXIT_METRICS_INTERNAL_KEYS,
    validate_parity,
)


def test_parity_ok():
    entry = {"rsi": 55.0, "macd": -0.01, "adx": 22.0}
    exit_ = {"rsi": 50.0, "macd": 0.001, "adx": 23.5}
    res = validate_parity(entry, exit_, trade_id="t1", outcome="tp")
    assert res["status"] == "ok"
    assert res["missing"] == []
    assert res["extra"] == []
    assert res["coverage_pct"] == 100.0


def test_parity_missing_key_in_exit():
    entry = {"rsi": 55.0, "macd": -0.01, "adx": 22.0}
    exit_ = {"rsi": 50.0, "macd": 0.001}
    res = validate_parity(entry, exit_, trade_id="t2", outcome="sl")
    assert res["status"] == "partial_divergence"
    assert res["missing"] == ["adx"]
    assert res["extra"] == []
    assert 0 < res["coverage_pct"] < 100


def test_parity_extra_key_in_exit():
    entry = {"rsi": 55.0}
    exit_ = {"rsi": 50.0, "new_indicator": 0.5}
    res = validate_parity(entry, exit_, trade_id="t3", outcome="timeout")
    assert res["status"] == "partial_divergence"
    assert res["missing"] == []
    assert res["extra"] == ["new_indicator"]


def test_parity_capture_error_short_circuits():
    entry = {"rsi": 55.0, "macd": -0.01}
    exit_ = {"_capture_error": "RuntimeError: provider down"}
    res = validate_parity(entry, exit_, trade_id="t4", outcome="flow_tb")
    assert res["status"] == "capture_error"
    # missing/extra MUST NOT be populated when capture failed
    assert res["missing"] == []
    assert res["extra"] == []


def test_parity_internal_keys_excluded_from_diff():
    entry = {"rsi": 55.0, "system_metadata": {"k": "v"}}
    exit_ = {"rsi": 50.0, "timestamps": "now"}
    res = validate_parity(entry, exit_, trade_id="t5", outcome="tp")
    # Both sides have internal-only extra keys — should NOT count as
    # missing/extra. The catalog comparison sees only {"rsi"} on each side.
    assert res["status"] == "ok"
    for ik in EXIT_METRICS_INTERNAL_KEYS:
        assert ik not in res["missing"]
        assert ik not in res["extra"]


def test_parity_flattens_nested_entry():
    nested_entry = {
        "rsi": {"value": 55.0, "source_group": "structural"},
        "macd": {"value": -0.01, "source_group": "structural"},
    }
    exit_ = {"rsi": 50.0, "macd": 0.001}
    res = validate_parity(nested_entry, exit_, trade_id="t6", outcome="tp")
    assert res["status"] == "ok"
    assert res["coverage_pct"] == 100.0


def test_parity_empty_both_sides():
    res = validate_parity({}, {}, trade_id="t7", outcome="tp")
    assert res["status"] == "empty"
    assert res["coverage_pct"] == 100.0
