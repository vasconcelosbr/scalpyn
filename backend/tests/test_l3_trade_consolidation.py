from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.backoffice import DecisionLog
from app.models.shadow_trade import ShadowTrade
from app.schemas.spot_engine_config import SpotEngineConfig
from app.services import shadow_trade_service
from app.tasks.pipeline_scan import _ensure_l3_consolidation_candidate_logged
from app.services.l3_trade_consolidation import (
    REASON_ACTIVE_TRADE,
    REASON_CONCURRENT_TRADE,
    REASON_LOWER_PRIORITY,
    EligibleL3Candidate,
    build_consolidation_event_id,
    candle_open_for_timeframe,
    consolidate_l3_candidates,
    candidate_from_decision,
    _lineage,
    rank_candidates,
)


NOW = datetime(2026, 8, 1, 14, 12, 9, tzinfo=timezone.utc)


def candidate(
    *,
    symbol: str = "ETH_USDT",
    profile_name: str = "PROFILE_A",
    profile_id=None,
    decision_id: int = 1,
    decision_score: float = 80.0,
    buy_threshold: float = 60.0,
    strong_buy_threshold: float = 90.0,
    market_structure_score=None,
    momentum_score=None,
    liquidity_score=None,
    signal_score=None,
    ml_score=None,
    user_id=None,
) -> EligibleL3Candidate:
    return EligibleL3Candidate(
        user_id=user_id or uuid4(),
        decision_id=decision_id,
        symbol=symbol,
        direction="SPOT",
        timeframe="5m",
        candle_open_timestamp=candle_open_for_timeframe(NOW, "5m"),
        profile_id=profile_id or uuid4(),
        profile_name=profile_name,
        profile_version=NOW,
        rules_snapshot={"filters": {"conditions": [{"indicator": "rsi"}]}},
        decision_score=decision_score,
        buy_threshold=buy_threshold,
        strong_buy_threshold=strong_buy_threshold,
        market_structure_score=market_structure_score,
        momentum_score=momentum_score,
        liquidity_score=liquidity_score,
        signal_score=signal_score,
        watchlist_id=str(uuid4()),
        watchlist_name=f"WL_{profile_name}",
        watchlist_level="L3",
        ml_score=ml_score,
    )


def test_seven_profiles_produce_one_deterministic_winner():
    user_id = uuid4()
    rows = [
        candidate(
            user_id=user_id,
            profile_name=f"PROFILE_{index}",
            decision_id=index,
            decision_score=70.0 + index,
        )
        for index in range(1, 8)
    ]
    ranked = rank_candidates(rows)
    assert len(ranked) == 7
    assert ranked[0].profile_name == "PROFILE_7"
    assert len(ranked[1:]) == 6


def test_different_symbols_form_independent_events():
    user_id = uuid4()
    eth = candidate(user_id=user_id, symbol="ETH_USDT", decision_id=1)
    bnb = candidate(user_id=user_id, symbol="BNB_USDT", decision_id=2)
    assert eth.event_id != bnb.event_id


def test_canonical_event_id_uses_candle_open_not_processing_second():
    first = build_consolidation_event_id(
        symbol="ETH_USDT",
        direction="SPOT",
        timeframe="5m",
        candle_open_timestamp=candle_open_for_timeframe(NOW, "5m"),
    )
    later_same_candle = build_consolidation_event_id(
        symbol="ETH_USDT",
        direction="SPOT",
        timeframe="5m",
        candle_open_timestamp=candle_open_for_timeframe(
            datetime(2026, 8, 1, 14, 14, 59, tzinfo=timezone.utc), "5m"
        ),
    )
    assert first == later_same_candle


def test_total_tie_uses_profile_name_then_profile_id():
    user_id = uuid4()
    lower_id = uuid4()
    higher_id = uuid4()
    if str(lower_id) > str(higher_id):
        lower_id, higher_id = higher_id, lower_id
    rows = [
        candidate(
            user_id=user_id,
            profile_name="B_PROFILE",
            profile_id=lower_id,
            decision_id=1,
        ),
        candidate(
            user_id=user_id,
            profile_name="A_PROFILE",
            profile_id=higher_id,
            decision_id=2,
        ),
    ]
    assert rank_candidates(rows)[0].profile_name == "A_PROFILE"

    same_name = [
        candidate(
            user_id=user_id,
            profile_name="SAME",
            profile_id=higher_id,
            decision_id=3,
        ),
        candidate(
            user_id=user_id,
            profile_name="SAME",
            profile_id=lower_id,
            decision_id=4,
        ),
    ]
    assert rank_candidates(same_name)[0].profile_id == lower_id


def test_missing_optional_components_are_ranked_as_zero():
    user_id = uuid4()
    missing = candidate(user_id=user_id, profile_name="MISSING", decision_id=1)
    present = candidate(
        user_id=user_id,
        profile_name="PRESENT",
        decision_id=2,
        market_structure_score=1.0,
    )
    assert rank_candidates([missing, present])[0] == present


def test_normalized_margin_uses_profile_specific_threshold_band():
    row = candidate(
        decision_score=80.0,
        buy_threshold=60.0,
        strong_buy_threshold=95.0,
    )
    assert row.normalized_score_margin == pytest.approx(20.0 / 35.0)


def test_feature_flag_defaults_to_legacy_off():
    scanner = SpotEngineConfig().scanner
    assert scanner.l3_single_profile_per_symbol_enabled is False
    assert (
        scanner.l3_profile_consolidation_rule_version
        == "single_profile_per_symbol_v1"
    )


def test_winner_lineage_preserves_immutable_profile_rules_snapshot():
    profile_rules = {
        "filters": {"conditions": [{"indicator": "rsi", "operator": ">", "value": 55}]},
        "default_timeframe": "5m",
    }
    row = candidate_from_decision(
        user_id=uuid4(),
        decision_id=7,
        decision={"symbol": "ETH_USDT", "direction": "SPOT", "score": 88, "created_at": NOW},
        buy_threshold=60,
        strong_buy_threshold=90,
        profile_id=uuid4(),
        profile_name="PROFILE_ORIGINAL",
        profile_version=NOW,
        rules_snapshot=profile_rules,
        watchlist_id=str(uuid4()),
        watchlist_name="WL_PROFILE_ORIGINAL",
        watchlist_level="L3",
        source_watchlist_id=None,
    )

    # Mutating the live profile config after candidate capture cannot alter the
    # immutable winner snapshot that will be persisted to shadow_trades.
    profile_rules["filters"]["conditions"][0]["value"] = 99
    lineage = _lineage(row)

    assert lineage.profile_id == str(row.profile_id)
    assert lineage.profile_name == "PROFILE_ORIGINAL"
    assert lineage.profile_version == NOW
    assert lineage.rules_snapshot["filters"]["conditions"][0]["value"] == 55


def test_canonical_shadow_insert_keeps_profile_rules_contract():
    sql = str(shadow_trade_service._INSERT_SHADOW_SQL)
    assert "profile_id, profile_version, profile_name, strategy_type, rules_snapshot" in sql
    assert "CAST(:rules_snapshot AS JSONB)" in sql


def test_winner_lineage_preserves_existing_ml_contract_fields():
    model_id = str(uuid4())
    ranking_id = str(uuid4())
    row = candidate(
        ml_score={
            "model_id": model_id,
            "probability": 0.73,
            "model_lane": "L3",
            "ranking_id": ranking_id,
            "model_version": "l3-model-v1",
            "threshold": 0.61,
            "score_status": "SCORED",
            "gate_action": "ALLOW",
            "reason_codes": ["ABOVE_THRESHOLD"],
            "orchestrator_payload": {"source": "test"},
        }
    )

    lineage = _lineage(row)

    assert lineage.ml_model_id == model_id
    assert lineage.ml_probability == 0.73
    assert lineage.model_lane == "L3"
    assert lineage.ranking_id == ranking_id
    assert lineage.model_version == "l3-model-v1"
    assert lineage.threshold_used == 0.61
    assert lineage.score_status == "SCORED"
    assert lineage.gate_action == "ALLOW"
    assert lineage.reason_codes == ["ABOVE_THRESHOLD"]
    assert lineage.orchestrator_payload == {"source": "test"}
    assert lineage.ml_gate_enabled is True


def test_enabled_consolidation_collects_a_stable_allow_profile():
    assert _ensure_l3_consolidation_candidate_logged(
        {"decision": "ALLOW"},
        enabled=True,
        should_log=False,
        event_type=None,
    ) == (True, "L3_CONSOLIDATION_CANDIDATE")


def test_disabled_consolidation_preserves_legacy_edge_trigger():
    assert _ensure_l3_consolidation_candidate_logged(
        {"decision": "ALLOW"},
        enabled=False,
        should_log=False,
        event_type=None,
    ) == (False, None)


class FakeResult:
    def __init__(self, scalar=None, first=None):
        self._scalar = scalar
        self._first = first

    def scalar_one_or_none(self):
        return self._scalar

    def first(self):
        return self._first


class SharedState:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.active = None
        self.decisions = {}
        self.suppressions = []
        self.created = 0
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
            self.shared.suppressions.extend(self.pending_suppressions)
        finally:
            if self.lock_acquired:
                self.shared.lock.release()
                self.lock_acquired = False

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "pg_advisory_xact_lock" in sql:
            await self.shared.lock.acquire()
            self.lock_acquired = True
            return FakeResult(first=(1,))
        if "metrics->>'consolidation_event_id'" in sql:
            return FakeResult(first=None)

        entity = None
        descriptions = getattr(statement, "column_descriptions", None) or []
        if descriptions:
            entity = descriptions[0].get("entity")
        if entity is ShadowTrade:
            return FakeResult(scalar=self.shared.active)
        if entity is DecisionLog:
            decision_id = None
            # Each event uses one winner; the fake can return the sole matching
            # source decision without interpreting SQLAlchemy's bind tree.
            if self.shared.decisions:
                decision_id = sorted(self.shared.decisions)[0]
            return FakeResult(scalar=self.shared.decisions.get(decision_id))
        raise AssertionError(f"Unexpected SQL in fake session: {sql}")

    def add(self, row):
        self.pending_suppressions.append(row)


def install_fake_runtime(monkeypatch, shared: SharedState, *, fail_create=False):
    import app.database as database

    monkeypatch.setattr(database, "CeleryAsyncSessionLocal", lambda: FakeSession(shared))

    async def fake_load_config(_user_id):
        return {
            "tp_pct": 1.0,
            "sl_pct": 1.0,
            "timeout_candles": None,
            "l3_single_profile_per_symbol_enabled": True,
            "l3_profile_consolidation_rule_version": "single_profile_per_symbol_v1",
        }

    async def fake_create(db, decision, *_args, **_kwargs):
        if fail_create:
            raise RuntimeError("injected_create_failure")
        trade = SimpleNamespace(
            id=uuid4(),
            user_id=decision.user_id,
            symbol=decision.symbol,
            direction=decision.direction,
            source="L3",
            status="RUNNING",
            created_at=NOW,
        )
        db.pending_trade = trade
        shared.last_lineage = _kwargs.get("lineage")
        return trade.id

    monkeypatch.setattr(
        shadow_trade_service, "load_shadow_creation_config", fake_load_config
    )
    monkeypatch.setattr(shadow_trade_service, "_create_from_decision", fake_create)


def add_decisions(shared: SharedState, rows):
    for item in rows:
        shared.decisions[item.decision_id] = DecisionLog(
            id=item.decision_id,
            user_id=item.user_id,
            symbol=item.symbol,
            strategy="L3",
            timeframe=item.timeframe,
            score=item.decision_score,
            decision="ALLOW",
            direction=item.direction,
            created_at=NOW,
        )


@pytest.mark.asyncio
async def test_active_trade_suppresses_every_new_candidate(monkeypatch):
    shared = SharedState()
    user_id = uuid4()
    rows = [
        candidate(user_id=user_id, decision_id=1, profile_name="A"),
        candidate(user_id=user_id, decision_id=2, profile_name="B"),
    ]
    add_decisions(shared, rows)
    shared.active = SimpleNamespace(
        id=uuid4(),
        symbol="ETH_USDT",
        direction="SPOT",
        status="RUNNING",
        created_at=NOW,
    )
    install_fake_runtime(monkeypatch, shared)

    result = await consolidate_l3_candidates(rows, scan_run_id="scan-active")

    assert result[0].reason_code == REASON_ACTIVE_TRADE
    assert result[0].suppressed_count == 2
    assert shared.created == 0
    assert len(shared.suppressions) == 2


@pytest.mark.asyncio
async def test_completed_trade_allows_a_new_event(monkeypatch):
    shared = SharedState()
    user_id = uuid4()
    rows = [candidate(user_id=user_id, decision_id=1)]
    add_decisions(shared, rows)
    install_fake_runtime(monkeypatch, shared)

    result = await consolidate_l3_candidates(rows, scan_run_id="scan-new")

    assert result[0].decision == "CREATED"
    assert shared.created == 1
    assert shared.active.status == "RUNNING"


@pytest.mark.asyncio
async def test_seven_approved_profiles_create_one_trade_and_six_audits(monkeypatch):
    shared = SharedState()
    user_id = uuid4()
    rows = [
        candidate(
            user_id=user_id,
            decision_id=index,
            profile_name=f"PROFILE_{index}",
            decision_score=70.0 + index,
        )
        for index in range(1, 8)
    ]
    add_decisions(shared, rows)
    install_fake_runtime(monkeypatch, shared)

    result = await consolidate_l3_candidates(rows, scan_run_id="scan-seven")

    assert result[0].decision == "CREATED"
    assert result[0].candidate_count == 7
    assert result[0].suppressed_count == 6
    assert result[0].winner_profile_id == str(rows[-1].profile_id)
    assert shared.created == 1
    assert len(shared.suppressions) == 6
    assert shared.last_lineage.profile_id == str(rows[-1].profile_id)
    assert shared.last_lineage.profile_name == rows[-1].profile_name
    assert shared.last_lineage.profile_version == rows[-1].profile_version
    assert shared.last_lineage.rules_snapshot == rows[-1].rules_snapshot
    assert {
        row.metrics["reason_code"] for row in shared.suppressions
    } == {REASON_LOWER_PRIORITY}


@pytest.mark.asyncio
async def test_two_workers_persist_only_one_active_trade(monkeypatch):
    shared = SharedState()
    user_id = uuid4()
    rows = [
        candidate(user_id=user_id, decision_id=1, profile_name="A"),
        candidate(user_id=user_id, decision_id=2, profile_name="B"),
    ]
    add_decisions(shared, rows)
    install_fake_runtime(monkeypatch, shared)

    first, second = await asyncio.gather(
        consolidate_l3_candidates(rows, scan_run_id="scan-race"),
        consolidate_l3_candidates(rows, scan_run_id="scan-race"),
    )

    assert shared.created == 1
    decisions = {first[0].decision, second[0].decision}
    assert decisions == {"CREATED", "SUPPRESSED"}
    suppressed_reason = (
        first[0].reason_code if first[0].decision == "SUPPRESSED" else second[0].reason_code
    )
    assert suppressed_reason in {REASON_ACTIVE_TRADE, REASON_CONCURRENT_TRADE}


@pytest.mark.asyncio
async def test_create_failure_rolls_back_suppressions_and_is_retryable(monkeypatch):
    shared = SharedState()
    user_id = uuid4()
    rows = [
        candidate(user_id=user_id, decision_id=1, profile_name="A"),
        candidate(user_id=user_id, decision_id=2, profile_name="B"),
    ]
    add_decisions(shared, rows)
    install_fake_runtime(monkeypatch, shared, fail_create=True)

    failed = await consolidate_l3_candidates(rows, scan_run_id="scan-fail")
    assert failed[0].decision == "ERROR"
    assert shared.active is None
    assert shared.suppressions == []

    install_fake_runtime(monkeypatch, shared, fail_create=False)
    retried = await consolidate_l3_candidates(rows, scan_run_id="scan-fail")
    assert retried[0].decision == "CREATED"
    assert len(shared.suppressions) == 1
    assert shared.suppressions[0].metrics["reason_code"] == REASON_LOWER_PRIORITY


def test_migration_scopes_uniqueness_to_enabled_active_l3_rows():
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "141_l3_profile_consolidation.py"
    ).read_text(encoding="utf-8")
    assert "l3_consolidation_enforced = TRUE" in migration
    assert "source = 'L3'" in migration
    assert "status IN ('PENDING', 'RUNNING')" in migration
    assert "user_id, symbol, direction" in migration
