from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.api.shadow_trades import (
    _active_read_projection,
    _consolidation_payload,
    _rejected_projected_ids_query,
    _sanitize_status,
)
from app.models.backoffice import DecisionLog
from app.models.config_profile import ConfigProfile
from app.models.shadow_trade import ShadowTrade
from app.schemas.spot_engine_config import SpotEngineConfig
from app.services import shadow_trade_service
from app.services.l3_rejected_trade_consolidation import (
    REASON_ACTIVE_REJECTED,
    REASON_LOWER_PRIORITY,
    REASON_RATE_LIMIT,
    RejectedL3Candidate,
    consolidate_l3_rejected_candidates,
    rejected_candidate_from_decision,
)
from app.services.l3_trade_consolidation import build_consolidation_event_id


NOW = datetime(2026, 8, 31, 17, 42, 10, tzinfo=timezone.utc)


def candidate(
    *,
    user_id=None,
    symbol="ETH_USDT",
    direction="SPOT",
    profile_name="PROFILE_A",
    profile_id=None,
    score=40.0,
    source="l3_filter_rejected",
) -> RejectedL3Candidate:
    return rejected_candidate_from_decision(
        user_id=user_id or uuid4(),
        decision={
            "symbol": symbol,
            "direction": direction,
            "strategy": "L3",
            "decision": "BLOCK",
            "score": score,
            "reasons": [{"reason": source}],
            "metrics": {
                "source": source,
                "score_components": {
                    "market_structure_score": score,
                    "momentum_score": score - 1,
                    "liquidity_score": score - 2,
                    "signal_score": score - 3,
                },
            },
            "_asset": {"price": 100.0},
        },
        observed_at=NOW,
        buy_threshold=30.0,
        strong_buy_threshold=50.0,
        profile_id=profile_id or uuid4(),
        profile_name=profile_name,
        profile_version=NOW,
        profile_version_id=uuid4(),
        rules_snapshot={"default_timeframe": "5m"},
        watchlist_id=str(uuid4()),
        watchlist_name=f"WL_{profile_name}",
        watchlist_level="L3",
        source_watchlist_id=None,
    )


def test_rejected_flag_is_independent_and_defaults_off():
    scanner = SpotEngineConfig().scanner
    assert scanner.l3_single_profile_per_symbol_enabled is False
    assert scanner.l3_rejected_single_profile_per_symbol_enabled is False


def test_rejected_flag_round_trips_through_persisted_spot_config():
    payload = SpotEngineConfig().model_dump(mode="json")
    payload["scanner"]["l3_rejected_single_profile_per_symbol_enabled"] = True
    restored = SpotEngineConfig.from_config_json(payload)
    assert restored.scanner.l3_rejected_single_profile_per_symbol_enabled is True
    assert (
        restored.scanner.l3_profile_consolidation_rule_version
        == "single_profile_per_symbol_v1"
    )


def test_rejected_rate_limit_is_gui_owned_and_not_runtime_fallback():
    from app.schemas.strategy_settings import MLShadowConfig

    payload = MLShadowConfig().model_dump(mode="json")
    assert payload["shadow_capture_l3_rejected_max_per_hour"] == 500
    source = (
        Path(__file__).parents[1]
        / "app"
        / "services"
        / "l3_rejected_trade_consolidation.py"
    ).read_text(encoding="utf-8")
    assert "shadow_capture_l3_rejected_max_per_hour_missing" in source
    assert '.get("shadow_capture_l3_rejected_max_per_hour", 500)' not in source


def test_rejected_event_identity_cannot_collide_with_approved_lane():
    row = candidate()
    approved = build_consolidation_event_id(
        symbol=row.symbol,
        direction=row.direction,
        timeframe=row.timeframe,
        candle_open_timestamp=row.candle_open_timestamp,
    )
    assert row.event_id != approved


def test_filter_and_trigger_rejections_share_the_same_event_group_identity():
    user_id = uuid4()
    filtered = candidate(
        user_id=user_id,
        profile_name="FILTER",
        source="l3_filter_rejected",
    )
    triggered = candidate(
        user_id=user_id,
        profile_name="TRIGGER",
        source="l3_entry_trigger_rejected",
    )
    assert filtered.event_id == triggered.event_id
    assert {filtered.rejection_stage, triggered.rejection_stage} == {
        "PROFILE_FILTER",
        "ENTRY_TRIGGER",
    }


@pytest.mark.asyncio
async def test_mixed_rule_versions_are_rejected_before_any_write():
    rows = [
        candidate(profile_name="V1"),
        replace(candidate(profile_name="OTHER"), rule_version="future_rule_v2"),
    ]
    with pytest.raises(ValueError, match="mixed_l3_rejected"):
        await consolidate_l3_rejected_candidates(rows, scan_run_id="mixed")


class FakeResult:
    def __init__(self, *, scalar=None, first=None):
        self._scalar = scalar
        self._first = first

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def first(self):
        return self._first


class SharedState:
    def __init__(self, *, max_per_hour=100, created_last_hour=0):
        self.lock = asyncio.Lock()
        self.active = None
        self.suppressions = []
        self.created = 0
        self.max_per_hour = max_per_hour
        self.created_last_hour = created_last_hour
        self.last_extra = None
        self.last_lineage = None


class FakeSession:
    def __init__(self, shared: SharedState):
        self.shared = shared
        self.pending_trade = None
        self.pending_suppressions = []
        self.lock_acquired = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    @asynccontextmanager
    async def begin(self):
        try:
            yield self
            if self.pending_trade is not None:
                self.shared.active = self.pending_trade
                self.shared.created += 1
                self.shared.created_last_hour += 1
            self.shared.suppressions.extend(self.pending_suppressions)
        finally:
            if self.lock_acquired:
                self.shared.lock.release()
                self.lock_acquired = False

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "pg_advisory_xact_lock" in sql:
            if not self.lock_acquired:
                await self.shared.lock.acquire()
                self.lock_acquired = True
            return FakeResult(first=(1,))
        if "candidate_identity" in sql:
            return FakeResult(first=None)
        if "COUNT(*)" in sql and "l3_consolidation_enforced IS TRUE" in sql:
            return FakeResult(scalar=self.shared.created_last_hour)

        descriptions = getattr(statement, "column_descriptions", None) or []
        entity = descriptions[0].get("entity") if descriptions else None
        if entity is ShadowTrade:
            return FakeResult(scalar=self.shared.active)
        if entity is ConfigProfile:
            return FakeResult(
                scalar=SimpleNamespace(
                    config_json={
                        "shadow_capture_l3_rejected_max_per_hour": self.shared.max_per_hour
                    }
                )
            )
        raise AssertionError(f"Unexpected SQL: {sql}")

    def add(self, row):
        assert isinstance(row, DecisionLog)
        self.pending_suppressions.append(row)


def install_runtime(monkeypatch, shared: SharedState):
    import app.database as database
    from app.services import l3_rejected_trade_consolidation as service

    monkeypatch.setattr(database, "CeleryAsyncSessionLocal", lambda: FakeSession(shared))

    async def fake_config(_user_id):
        return {
            "tp_pct": 1.0,
            "sl_pct": 1.0,
            "amount_usdt": 1000.0,
            "timeout_candles": 12,
            "ttt_enabled": True,
            "ttt_tp_pct": 1.0,
            "ttt_timeout_minutes": 60,
            "trailing": {},
            "l3_rejected_single_profile_per_symbol_enabled": True,
            "l3_profile_consolidation_rule_version": "single_profile_per_symbol_v1",
        }

    async def fake_features(_symbols):
        return {}

    async def fake_create(db, decision, *_args, **kwargs):
        trade = SimpleNamespace(
            id=uuid4(),
            user_id=decision.user_id,
            symbol=decision.symbol,
            direction=decision.direction,
            source="L3_REJECTED",
            status="RUNNING",
            created_at=NOW,
        )
        db.pending_trade = trade
        shared.last_extra = kwargs.get("extra_config")
        shared.last_lineage = kwargs.get("lineage")
        return trade.id

    monkeypatch.setattr(shadow_trade_service, "load_shadow_creation_config", fake_config)
    monkeypatch.setattr(
        shadow_trade_service, "_load_strategy_lab_features_by_symbol", fake_features
    )
    monkeypatch.setattr(shadow_trade_service, "_create_from_decision", fake_create)
    # Imports inside the service resolve the monkeypatched module functions.
    assert service.SOURCE == "L3_REJECTED"


@pytest.mark.asyncio
async def test_multiple_rejected_profiles_create_one_owner_and_associations(monkeypatch):
    shared = SharedState()
    user_id = uuid4()
    rows = [
        candidate(user_id=user_id, profile_name="LOW", score=35),
        candidate(user_id=user_id, profile_name="WINNER", score=49),
        candidate(user_id=user_id, profile_name="MID", score=42),
    ]
    install_runtime(monkeypatch, shared)

    result = await consolidate_l3_rejected_candidates(rows, scan_run_id="scan-one")

    assert result[0].decision == "CREATED"
    assert result[0].candidate_count == 3
    assert result[0].suppressed_count == 2
    assert shared.created == 1
    assert len(shared.suppressions) == 2
    assert {row.metrics["reason_code"] for row in shared.suppressions} == {
        REASON_LOWER_PRIORITY
    }
    consolidation = shared.last_extra["consolidation"]
    assert consolidation["primary_profile_name"] == "WINNER"
    assert consolidation["associated_profile_count"] == 2
    assert [item["rank"] for item in consolidation["candidates"]] == [1, 2, 3]
    assert shared.last_lineage.profile_name == "WINNER"


@pytest.mark.asyncio
async def test_legacy_active_rejected_blocks_new_owner_without_mutation(monkeypatch):
    shared = SharedState()
    user_id = uuid4()
    legacy = SimpleNamespace(
        id=uuid4(),
        symbol="ETH_USDT",
        direction="SPOT",
        status="RUNNING",
        l3_consolidation_enforced=False,
        created_at=NOW,
    )
    shared.active = legacy
    rows = [candidate(user_id=user_id), candidate(user_id=user_id, profile_name="B")]
    install_runtime(monkeypatch, shared)

    result = await consolidate_l3_rejected_candidates(rows, scan_run_id="scan-active")

    assert result[0].reason_code == REASON_ACTIVE_REJECTED
    assert shared.created == 0
    assert shared.active is legacy
    assert len(shared.suppressions) == 2


@pytest.mark.asyncio
async def test_rate_limit_counts_canonical_winners_after_grouping(monkeypatch):
    shared = SharedState(max_per_hour=1, created_last_hour=1)
    user_id = uuid4()
    rows = [candidate(user_id=user_id), candidate(user_id=user_id, profile_name="B")]
    install_runtime(monkeypatch, shared)

    result = await consolidate_l3_rejected_candidates(rows, scan_run_id="scan-limit")

    assert result[0].reason_code == REASON_RATE_LIMIT
    assert shared.created == 0
    assert len(shared.suppressions) == 2


@pytest.mark.asyncio
async def test_concurrent_workers_create_only_one_rejected_owner(monkeypatch):
    shared = SharedState()
    user_id = uuid4()
    rows = [candidate(user_id=user_id), candidate(user_id=user_id, profile_name="B")]
    install_runtime(monkeypatch, shared)

    first, second = await asyncio.gather(
        consolidate_l3_rejected_candidates(rows, scan_run_id="scan-race"),
        consolidate_l3_rejected_candidates(rows, scan_run_id="scan-race"),
    )

    assert shared.created == 1
    assert {first[0].decision, second[0].decision} == {"CREATED", "SUPPRESSED"}


def test_api_projects_consolidation_and_keeps_legacy_null():
    row = SimpleNamespace(
        l3_consolidation_enforced=True,
        config_snapshot={
            "consolidation": {
                "event_id": "event-1",
                "rule_version": "single_profile_per_symbol_v1",
                "lane": "L3_REJECTED",
                "candidate_count": 2,
                "associated_profile_count": 1,
                "candidates": [
                    {"rank": 1, "profile_id": str(uuid4()), "profile_name": "A"},
                    {"rank": 2, "profile_id": str(uuid4()), "profile_name": "B"},
                ],
                "selection_rule": ["decision_score_desc"],
                "selection_metrics": {"decision_score": 40.0},
            }
        },
    )
    payload = _consolidation_payload(row)
    assert payload["primary_profile"]["profile_name"] == "A"
    assert payload["associated_count"] == 1
    assert payload["associated_profiles"][0]["profile_name"] == "B"

    legacy = SimpleNamespace(
        l3_consolidation_enforced=False,
        config_snapshot={"l3_decision": "BLOCK"},
    )
    assert _consolidation_payload(legacy) is None


def _legacy_active_row(*, profile_name: str, score: float, profile_id=None):
    row_id = uuid4()
    decision_id = abs(hash((profile_name, str(row_id))))
    row = SimpleNamespace(
        id=row_id,
        decision_id=decision_id,
        user_id=uuid4(),
        symbol="LIT_USDT",
        direction="SPOT",
        source="L3_REJECTED",
        status="RUNNING",
        l3_consolidation_enforced=False,
        config_snapshot={},
        final_priority_score=None,
        profile_id=profile_id or uuid4(),
        profile_name=profile_name,
        profile_version=NOW,
        profile_version_id=uuid4(),
        watchlist_id=uuid4(),
        watchlist_name=f"WL_{profile_name}",
        reason_codes=["BLOCK"],
        timeframe="5m",
    )
    decision = SimpleNamespace(
        id=decision_id,
        score=score,
        reasons={"profile": profile_name},
        metrics={
            "source": "l3_filter_rejected",
            "score_components": {
                "market_structure_score": score,
                "momentum_score": score - 1,
                "liquidity_score": score - 2,
                "signal_score": score - 3,
            },
        },
    )
    return row, decision


def test_active_legacy_read_projection_exposes_one_primary_and_associations():
    low, low_decision = _legacy_active_row(profile_name="LOW", score=35)
    winner, winner_decision = _legacy_active_row(profile_name="WINNER", score=49)
    middle, middle_decision = _legacy_active_row(profile_name="MIDDLE", score=42)
    for row in (low, winner, middle):
        row.user_id = winner.user_id
    decisions = {
        low_decision.id: low_decision,
        winner_decision.id: winner_decision,
        middle_decision.id: middle_decision,
    }

    projection = _active_read_projection(
        winner, [low, winner, middle], decisions
    )

    assert projection["projection"] == "LEGACY_ACTIVE_READ"
    assert projection["primary_profile"]["profile_name"] == "WINNER"
    assert projection["candidate_count"] == 3
    assert projection["associated_count"] == 2
    assert [row["profile_name"] for row in projection["candidates"]] == [
        "WINNER",
        "MIDDLE",
        "LOW",
    ]


def test_active_legacy_projection_deduplicates_the_same_profile_identity():
    profile_id = uuid4()
    winner, winner_decision = _legacy_active_row(
        profile_name="SAME", score=49, profile_id=profile_id
    )
    duplicate, duplicate_decision = _legacy_active_row(
        profile_name="SAME", score=40, profile_id=profile_id
    )
    duplicate.user_id = winner.user_id
    projection = _active_read_projection(
        winner,
        [winner, duplicate],
        {
            winner_decision.id: winner_decision,
            duplicate_decision.id: duplicate_decision,
        },
    )
    assert projection["candidate_count"] == 1
    assert projection["associated_count"] == 0


def test_rejected_active_projection_query_groups_before_profile_filter():
    profile_id = uuid4()
    query = _rejected_projected_ids_query(
        user_id=uuid4(),
        status="OPEN",
        symbol=None,
        min_date=None,
        max_date=None,
        profile_id=profile_id,
        profile_version=None,
    )
    sql = str(
        query.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "DISTINCT" in sql
    assert "EXISTS" in sql
    assert "L3_REJECTED" in sql
    assert "shadow_trades_1.profile_id" in sql


def test_open_status_is_a_canonical_active_status_filter():
    assert _sanitize_status("open") == "OPEN"


def test_migration_is_scoped_to_new_rejected_canonical_rows():
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "205_l3_rejected_profile_consolidation.py"
    ).read_text(encoding="utf-8")
    assert "source = 'L3_REJECTED'" in migration
    assert "l3_consolidation_enforced = TRUE" in migration
    assert "status IN ('PENDING', 'RUNNING')" in migration
    assert "user_id, symbol, direction" in migration
    assert "ADD COLUMN" not in migration


def test_pipeline_accumulates_rejected_candidates_before_global_consolidation():
    source = (
        Path(__file__).parents[1] / "app" / "tasks" / "pipeline_scan.py"
    ).read_text(encoding="utf-8")
    append_at = source.index("l3_rejected_consolidation_candidates.append")
    consolidate_at = source.index("await consolidate_l3_rejected_candidates(")
    outbox_at = source.index("await process_l3_authorization_outbox(")
    assert append_at < consolidate_at < outbox_at
    assert "create_l3_rejected_inline_shadows" in source
    assert "if _wl_rejected_consolidation_enabled" in source
