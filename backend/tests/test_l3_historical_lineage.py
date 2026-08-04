from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.ml.historical_l3_lineage import resolve_historical_l3_record
from app.services.ml_challenger_service import MLChallengerService
from scripts.audit_l3_30d_readiness import _decode_jsonb


DECISION_AT = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
SOURCE_AT = DECISION_AT - timedelta(minutes=1)
LABEL_AT = DECISION_AT + timedelta(minutes=8)


def test_audit_jsonb_decoder_normalizes_asyncpg_text_codec():
    assert _decode_jsonb('{"rsi":{"value":55.0}}') == {
        "rsi": {"value": 55.0}
    }
    assert _decode_jsonb("not-json") == "not-json"


def _raw_record():
    return {
        "shadow_id": "shadow-1",
        "entry_timestamp": DECISION_AT - timedelta(minutes=5),
        "created_at": DECISION_AT + timedelta(seconds=2),
        "holding_seconds": 780.0,
        "decision_created_at": DECISION_AT,
        "historical_label_event_at": LABEL_AT,
        "features_snapshot": {
            "rsi": 55.0,
            "taker_ratio": 0.61,
            "volume_delta": 125.0,
            "liquidity_score": 72.0,
        },
        "decision_indicator_snapshot": {
            "rsi": {
                "value": 55.0,
                "timestamp": SOURCE_AT.isoformat(),
                "source_group": "structural",
            },
            "taker_ratio": {
                "value": 0.61,
                "ts": SOURCE_AT.isoformat(),
                "source_group": "live_injection",
            },
            "volume_delta": {
                "value": 125.0,
                "ts": SOURCE_AT.isoformat(),
                "source_group": "live_injection",
            },
        },
    }


def test_resolver_joins_timestamp_aliases_neutralizes_live_and_reanchors_label():
    raw = _raw_record()
    result = resolve_historical_l3_record(
        raw,
        model_feature_columns=[
            "rsi",
            "taker_ratio",
            "volume_delta",
            "flow_strength",
            "delta_normalized",
            "liquidity_score",
        ],
        contract_version="decision_snapshot_ts_v1",
        configured_neutralized_features=[
            "taker_ratio",
            "volume_delta",
            "flow_strength",
            "delta_normalized",
        ],
        untrusted_source_groups=["live_injection"],
    )

    assert result.exclusion_reason is None
    assert result.record is not None
    assert result.record["feature_source_at"] == SOURCE_AT
    assert result.record["feature_source_times"] == {"rsi": SOURCE_AT.isoformat()}
    assert result.record["entry_timestamp"] == DECISION_AT
    assert result.record["created_at"] == DECISION_AT
    assert result.record["holding_seconds"] == 480.0
    assert result.record["features_snapshot"]["taker_ratio"] is None
    assert result.record["features_snapshot"]["volume_delta"] is None
    assert "liquidity_score" in result.neutralized_features
    assert raw["features_snapshot"]["taker_ratio"] == 0.61


def test_resolver_fails_closed_on_future_source_or_noncausal_label():
    future = _raw_record()
    future["decision_indicator_snapshot"]["rsi"]["timestamp"] = (
        DECISION_AT + timedelta(seconds=1)
    ).isoformat()
    result = resolve_historical_l3_record(
        future,
        model_feature_columns=["rsi"],
        contract_version="decision_snapshot_ts_v1",
        configured_neutralized_features=[],
        untrusted_source_groups=["live_injection"],
    )
    assert result.record is None
    assert result.exclusion_reason == "feature_source_after_decision:rsi"

    invalid_label = _raw_record()
    invalid_label["historical_label_event_at"] = DECISION_AT
    result = resolve_historical_l3_record(
        invalid_label,
        model_feature_columns=["rsi"],
        contract_version="decision_snapshot_ts_v1",
        configured_neutralized_features=[],
        untrusted_source_groups=["live_injection"],
    )
    assert result.record is None
    assert result.exclusion_reason == "label_not_after_decision"


def test_resolver_fails_closed_when_parent_value_differs():
    raw = _raw_record()
    raw["decision_indicator_snapshot"]["rsi"]["value"] = 54.0
    result = resolve_historical_l3_record(
        raw,
        model_feature_columns=["rsi"],
        contract_version="decision_snapshot_ts_v1",
        configured_neutralized_features=[],
        untrusted_source_groups=["live_injection"],
    )
    assert result.record is None
    assert result.exclusion_reason == "source_value_mismatch:rsi"


def test_l3_dataframe_neutralizes_marked_direct_and_engineered_features():
    raw = _raw_record()
    result = resolve_historical_l3_record(
        raw,
        model_feature_columns=["rsi", "taker_ratio", "flow_strength"],
        contract_version="decision_snapshot_ts_v1",
        configured_neutralized_features=["taker_ratio", "flow_strength"],
        untrusted_source_groups=["live_injection"],
    )
    record = {
        **result.record,
        "pnl_pct": 1.0,
        "net_return_pct": 0.8,
        "outcome": "TP_HIT",
        "profile_id": "profile-1",
        "source": "L3",
        "shadow_id": "shadow-1",
    }
    built = MLChallengerService()._build_l3_dataset(
        [record],
        ["rsi", "taker_ratio", "flow_strength"],
        win_fast_threshold_s=1800.0,
        lane_contract={"required": ["rsi", "taker_ratio"]},
        feature_ranges={"taker_ratio": {"gte": 0.0}},
    )
    X = built[0]
    assert X[0, 0] == 55.0
    assert str(X[0, 1]) == "nan"
    assert str(X[0, 2]) == "nan"


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None
        self.params = None

    async def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params
        return _Rows(self.rows)


def _historical_config():
    return {
        "ml_l3_historical_lineage_enabled": True,
        "ml_l3_historical_lineage_contract_version": "decision_snapshot_ts_v1",
        "ml_l3_historical_capture_contracts": ["point-in-time-v1"],
        "ml_l3_historical_timestamp_aliases": ["ts", "timestamp"],
        "ml_l3_historical_untrusted_source_groups": ["live_injection"],
        "ml_l3_historical_neutralized_features": [
            "taker_ratio",
            "volume_delta",
            "flow_strength",
            "delta_normalized",
        ],
        "ml_l3_historical_unresolved_feature_policy": "neutralize",
        "ml_l3_historical_label_anchor": "decision_created_at",
    }


@pytest.mark.asyncio
async def test_historical_loader_joins_decision_and_never_updates_shadows():
    row = {
        **_raw_record(),
        "pnl_pct": 1.0,
        "net_return_pct": 0.8,
        "outcome": "TP_HIT",
        "profile_id": "profile-1",
        "source": "L3",
    }
    session = _Session([row])
    records, diagnostics = await MLChallengerService()._load_l3_historical_shadow_data(
        session,
        UUID("00000000-0000-0000-0000-000000000001"),
        30,
        dataset_valid_from=DECISION_AT - timedelta(days=1),
        dataset_query_cutoff=DECISION_AT + timedelta(days=1),
        maturity_embargo_margin_minutes=60,
        ml_config=_historical_config(),
    )

    assert "JOIN decisions_log dl ON dl.id = st.decision_id" in session.statement
    assert "UPDATE shadow_trades" not in session.statement
    assert "barrier_touched_at" in session.statement
    assert len(records) == 1
    assert diagnostics["included_rows"] == 1
    assert diagnostics["shadow_mutations"] == 0
