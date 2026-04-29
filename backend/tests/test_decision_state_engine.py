"""Tests for decision state engine - duplicate prevention."""

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.services.decision_state_engine import (
    DecisionStateEngine,
    OpportunityState,
    STATE_IDLE,
    STATE_ACTIVE,
    STATE_CLOSED,
)


class TestDecisionStateEngine:
    """Test suite for DecisionStateEngine core logic."""

    @pytest.fixture
    def engine(self):
        """Create a fresh state engine instance."""
        return DecisionStateEngine(cooldown_minutes=30)

    @pytest.fixture
    def user_id(self):
        """Generate a test user ID."""
        return uuid4()

    @pytest.fixture
    def sample_decision(self):
        """Create a sample ALLOW decision."""
        return {
            "symbol": "BTC_USDT",
            "strategy": "SPOT",
            "timeframe": "5m",
            "score": 75.5,
            "decision": "ALLOW",
            "l1_pass": True,
            "l2_pass": True,
            "l3_pass": True,
            "reasons": {
                "rsi_bullish": "OK",
                "adx_trending": "OK",
                "volume_high": "OK",
            },
            "metrics": {
                "price": 45000.0,
                "rsi": 65.0,
                "adx": 28.0,
                "macd": 150.0,
                "volume_24h": 1500000000.0,
            },
            "latency_ms": 45,
            "created_at": datetime.now(timezone.utc),
        }

    def test_compute_state_hash_deterministic(self, engine, sample_decision):
        """Test that state hash is deterministic for same input."""
        hash1 = engine.compute_state_hash(sample_decision)
        hash2 = engine.compute_state_hash(sample_decision)
        assert hash1 == hash2
        assert len(hash1) == 16  # First 16 chars of SHA256

    def test_compute_state_hash_changes_on_key_feature_change(self, engine, sample_decision):
        """Test that state hash changes when key features change."""
        hash1 = engine.compute_state_hash(sample_decision)

        # Change score significantly
        modified = sample_decision.copy()
        modified["score"] = 85.5
        hash2 = engine.compute_state_hash(modified)
        assert hash1 != hash2

        # Change decision outcome
        modified["decision"] = "BLOCK"
        hash3 = engine.compute_state_hash(modified)
        assert hash1 != hash3
        assert hash2 != hash3

    def test_compute_state_hash_stable_for_minor_changes(self, engine, sample_decision):
        """Test that state hash is stable for minor metric fluctuations."""
        hash1 = engine.compute_state_hash(sample_decision)

        # Tiny price change (within rounding threshold)
        modified = sample_decision.copy()
        modified["metrics"]["price"] = 45000.01
        hash2 = engine.compute_state_hash(modified)
        assert hash1 == hash2

    def test_create_opportunity_idle_to_active(self, engine, sample_decision, user_id):
        """Test CREATE transition: IDLE → ACTIVE."""
        new_state, should_log, group_id = engine.evaluate_state_transition(
            current_state=None,
            decision=sample_decision,
            user_id=user_id,
        )

        assert new_state == STATE_ACTIVE
        assert should_log is True
        assert group_id is not None

    def test_hold_opportunity_active_to_active(self, engine, sample_decision, user_id):
        """Test HOLD transition: ACTIVE → ACTIVE (same state)."""
        state_hash = engine.compute_state_hash(sample_decision)
        current_state = OpportunityState(
            symbol="BTC_USDT",
            strategy="SPOT",
            user_id=user_id,
            state=STATE_ACTIVE,
            state_hash=state_hash,
            score=75.5,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )

        new_state, should_log, group_id = engine.evaluate_state_transition(
            current_state=current_state,
            decision=sample_decision,
            user_id=user_id,
        )

        assert new_state == STATE_ACTIVE
        assert should_log is False  # No new decision
        assert group_id is None

    def test_close_opportunity_active_to_closed(self, engine, sample_decision, user_id):
        """Test CLOSE transition: ACTIVE → CLOSED (decision blocked)."""
        state_hash = engine.compute_state_hash(sample_decision)
        current_state = OpportunityState(
            symbol="BTC_USDT",
            strategy="SPOT",
            user_id=user_id,
            state=STATE_ACTIVE,
            state_hash=state_hash,
            score=75.5,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )

        # Decision now blocked
        blocked_decision = sample_decision.copy()
        blocked_decision["decision"] = "BLOCK"
        blocked_decision["l3_pass"] = False

        new_state, should_log, group_id = engine.evaluate_state_transition(
            current_state=current_state,
            decision=blocked_decision,
            user_id=user_id,
        )

        assert new_state == STATE_CLOSED
        assert should_log is False
        assert group_id is None

    def test_reentry_after_cooldown(self, engine, sample_decision, user_id):
        """Test RE-ENTRY: CLOSED → ACTIVE after cooldown period."""
        state_hash = engine.compute_state_hash(sample_decision)
        current_state = OpportunityState(
            symbol="BTC_USDT",
            strategy="SPOT",
            user_id=user_id,
            state=STATE_CLOSED,
            state_hash=state_hash,
            score=75.5,
            last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=35),  # Past cooldown
        )

        new_state, should_log, group_id = engine.evaluate_state_transition(
            current_state=current_state,
            decision=sample_decision,
            user_id=user_id,
        )

        assert new_state == STATE_ACTIVE
        assert should_log is True
        assert group_id is not None

    def test_reentry_blocked_during_cooldown(self, engine, sample_decision, user_id):
        """Test that re-entry is blocked during cooldown period."""
        state_hash = engine.compute_state_hash(sample_decision)
        current_state = OpportunityState(
            symbol="BTC_USDT",
            strategy="SPOT",
            user_id=user_id,
            state=STATE_CLOSED,
            state_hash=state_hash,
            score=75.5,
            last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=15),  # Still in cooldown
        )

        new_state, should_log, group_id = engine.evaluate_state_transition(
            current_state=current_state,
            decision=sample_decision,
            user_id=user_id,
        )

        assert new_state == STATE_CLOSED
        assert should_log is False

    def test_state_change_triggers_new_decision(self, engine, sample_decision, user_id):
        """Test that significant state change triggers new decision."""
        old_hash = engine.compute_state_hash(sample_decision)
        current_state = OpportunityState(
            symbol="BTC_USDT",
            strategy="SPOT",
            user_id=user_id,
            state=STATE_ACTIVE,
            state_hash=old_hash,
            score=75.5,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )

        # Change score significantly
        modified_decision = sample_decision.copy()
        modified_decision["score"] = 90.0
        new_hash = engine.compute_state_hash(modified_decision)
        assert old_hash != new_hash

        new_state, should_log, group_id = engine.evaluate_state_transition(
            current_state=current_state,
            decision=modified_decision,
            user_id=user_id,
        )

        assert new_state == STATE_ACTIVE
        assert should_log is True  # New decision due to state change
        assert group_id is not None

    def test_multiple_symbols_independent(self, engine, sample_decision, user_id):
        """Test that different symbols maintain independent states."""
        # First symbol
        hash1 = engine.compute_state_hash(sample_decision)
        state1 = OpportunityState(
            symbol="BTC_USDT",
            strategy="SPOT",
            user_id=user_id,
            state=STATE_ACTIVE,
            state_hash=hash1,
        )

        # Second symbol
        decision2 = sample_decision.copy()
        decision2["symbol"] = "ETH_USDT"
        hash2 = engine.compute_state_hash(decision2)

        # Hashes should be different (different symbols)
        assert hash1 != hash2

        # Can create both opportunities simultaneously
        new_state1, should_log1, _ = engine.evaluate_state_transition(
            state1, sample_decision, user_id
        )
        new_state2, should_log2, _ = engine.evaluate_state_transition(
            None, decision2, user_id
        )

        assert new_state1 == STATE_ACTIVE
        assert should_log1 is False  # Existing state
        assert new_state2 == STATE_ACTIVE
        assert should_log2 is True  # New state

    def test_opportunity_state_should_create_new_decision(self, user_id):
        """Test OpportunityState.should_create_new_decision logic."""
        # IDLE always creates
        idle_state = OpportunityState(
            symbol="BTC_USDT",
            strategy="SPOT",
            user_id=user_id,
            state=STATE_IDLE,
        )
        assert idle_state.should_create_new_decision("newhash") is True

        # ACTIVE with different hash creates
        active_state = OpportunityState(
            symbol="BTC_USDT",
            strategy="SPOT",
            user_id=user_id,
            state=STATE_ACTIVE,
            state_hash="oldhash",
        )
        assert active_state.should_create_new_decision("newhash") is True
        assert active_state.should_create_new_decision("oldhash") is False

        # CLOSED after cooldown creates
        closed_state = OpportunityState(
            symbol="BTC_USDT",
            strategy="SPOT",
            user_id=user_id,
            state=STATE_CLOSED,
            state_hash="hash",
            last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=35),
        )
        assert closed_state.should_create_new_decision("hash", cooldown_minutes=30) is True

        # CLOSED within cooldown doesn't create
        closed_recent = OpportunityState(
            symbol="BTC_USDT",
            strategy="SPOT",
            user_id=user_id,
            state=STATE_CLOSED,
            state_hash="hash",
            last_seen_at=datetime.now(timezone.utc) - timedelta(minutes=15),
        )
        assert closed_recent.should_create_new_decision("hash", cooldown_minutes=30) is False

    def test_format_state_summary(self, engine, user_id):
        """Test state summary formatting."""
        opportunity = OpportunityState(
            symbol="BTC_USDT",
            strategy="SPOT",
            user_id=user_id,
            state=STATE_ACTIVE,
            state_hash="abc123",
            score=75.5,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )

        summary = engine.format_state_summary(opportunity)

        assert summary["symbol"] == "BTC_USDT"
        assert summary["state"] == STATE_ACTIVE
        assert summary["score"] == 75.5
        assert summary["state_hash"] == "abc123"
        assert summary["duration_minutes"] == 10

    def test_should_log_decision_integration(self, engine, sample_decision, user_id):
        """Test the integrated should_log_decision method."""
        # First decision: should log
        should_log, group_id, state_hash = engine.should_log_decision(
            sample_decision,
            current_state=None,
            user_id=user_id,
        )
        assert should_log is True
        assert group_id is not None
        assert state_hash is not None

        # Create active state
        active_state = OpportunityState(
            symbol="BTC_USDT",
            strategy="SPOT",
            user_id=user_id,
            state=STATE_ACTIVE,
            state_hash=state_hash,
            started_at=datetime.now(timezone.utc),
        )

        # Same decision again: should NOT log
        should_log, group_id, _ = engine.should_log_decision(
            sample_decision,
            current_state=active_state,
            user_id=user_id,
        )
        assert should_log is False
        assert group_id is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
