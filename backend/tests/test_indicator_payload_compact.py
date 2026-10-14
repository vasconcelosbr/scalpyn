import json
from datetime import datetime, timedelta, timezone

from app.services.profile_runtime_config import canonical_hash
from app.utils.indicator_merge import envelop_results, merge_indicator_rows
from app.utils.indicator_payload import (
    COMPACT_KEY,
    compact_indicator_payload,
    expand_indicator_envelope,
    expand_indicator_payload,
    is_compact,
    maybe_compact_indicator_payload,
)

NOW = datetime(2026, 10, 14, 9, 23, tzinfo=timezone.utc)


def _governed_payload():
    """Legacy-shaped governed 5m payload as compute_5m writes it."""
    results = {
        "adx": 16.83, "rsi": 54.2, "ema9": 1.25, "ema50": 1.2, "ema200": 1.1,
        "price": 1.26, "obv": -438.51, "volume_24h": 14.0, "atr": 1e-07,
        "flat": 0.0, "big": 1e20, "taker_ratio": 0.61, "missing": None,
    }
    payload = envelop_results(
        results,
        default_source="candle_computed",
        default_confidence=0.80,
        key_source_map={"taker_ratio": ("gate_trades", 1.0)},
        key_metadata={
            "adx": {"period": 14, "parameters": {"period": 14, "enabled": True}},
            "rsi": {"period": 14, "parameters": {}},
            # Live order-flow key with its own source identity: must survive
            # as a per-key override of the shared metadata.
            "taker_ratio": {
                "source_timestamp": "2026-10-14T09:22:58+00:00",
                "snapshot": {"n": 3}, "fallback_used": False,
            },
            # Type-strict override: 1 must not collapse into the shared True.
            "obv": {"candle_closed": 1},
        },
        envelope_metadata={
            "timeframe": "5m", "market_type": "spot",
            "scheduler_group": "microstructure", "source_provider": "gate.io",
            "provider_policy_id": "spot_gate_closed_ohlcv_v1",
            "candle_policy": "CLOSED_ONLY", "candle_closed": True,
            "source_timestamp": "2026-10-14T09:15:00+00:00",
            "computed_at": NOW.isoformat(), "available_at": NOW.isoformat(),
            "producer_version": "compute_5m_v2",
            "capture_contract_version": "gate_ohlcv_canonical_v1",
            "source_ingested_at": "2026-10-14T09:21:33.258651+00:00",
            "config_hash": "6f8e" * 16, "config_profile_id": "p-1",
        },
    )
    return json.loads(json.dumps(payload))


def _stored(payload):
    return json.loads(json.dumps(payload))


def test_compact_round_trip_rebuilds_legacy_payload_and_hashes_exactly():
    legacy = _governed_payload()
    compact = _stored(compact_indicator_payload(legacy))

    assert is_compact(compact)
    assert all("envelope_hash" not in v for k, v in compact.items() if k != COMPACT_KEY)
    assert compact["adx"]["value"] == 16.83
    assert compact["taker_ratio"]["source_timestamp"] == "2026-10-14T09:22:58+00:00"
    assert compact["obv"]["candle_closed"] == 1

    expanded = expand_indicator_payload(compact)
    assert expanded == legacy
    for envelope in expanded.values():
        material = dict(envelope)
        assert material.pop("envelope_hash") == canonical_hash(material)
    assert len(json.dumps(compact)) < len(json.dumps(legacy)) / 2


def test_payloads_without_valid_hashes_are_never_compacted():
    unhashed = _stored(envelop_results({"rsi": 50.0, "adx": 20.0}))
    assert compact_indicator_payload(unhashed) == unhashed

    tampered = _governed_payload()
    tampered["rsi"]["value"] = 99.0
    assert compact_indicator_payload(tampered) == tampered


def test_legacy_rows_pass_through_expansion_unchanged():
    legacy = _governed_payload()
    assert expand_indicator_payload(legacy) is legacy
    assert expand_indicator_payload(None) is None


def test_writer_flag_defaults_to_legacy_format(monkeypatch):
    legacy = _governed_payload()
    monkeypatch.delenv("INDICATORS_COMPACT_ENVELOPE", raising=False)
    assert maybe_compact_indicator_payload(legacy) == legacy
    monkeypatch.setenv("INDICATORS_COMPACT_ENVELOPE", "true")
    assert is_compact(maybe_compact_indicator_payload(legacy))


def test_single_envelope_selected_in_sql_expands_with_header():
    legacy = _governed_payload()
    compact = _stored(compact_indicator_payload(legacy))
    assert expand_indicator_envelope(compact["adx"], compact[COMPACT_KEY]) == legacy["adx"]
    assert expand_indicator_envelope(legacy["adx"], None) == legacy["adx"]


def test_merge_sees_identical_values_meta_and_candidates_for_compact_rows():
    legacy = _governed_payload()
    compact = _stored(compact_indicator_payload(legacy))
    ts = NOW - timedelta(seconds=60)
    from_legacy = merge_indicator_rows([("microstructure", ts, "5m", legacy)], now=NOW)
    from_compact = merge_indicator_rows([("microstructure", ts, "5m", compact)], now=NOW)

    assert COMPACT_KEY not in from_compact.values
    assert from_compact.values == from_legacy.values
    assert from_compact.meta == from_legacy.meta
    assert from_compact.candidates == from_legacy.candidates
