"""
Tests for AI Critic hollow prevention — profile_intelligence_live_service.py
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_db(scalar_returns=None):
    """AsyncSession mock with configurable scalar returns for sequential calls."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    if scalar_returns:
        db.execute.return_value.scalar_one_or_none = MagicMock(side_effect=scalar_returns)
    return db


def _make_anthropic_response(input_tokens=500, output_tokens=200, content="test"):
    resp = MagicMock()
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    resp.content = [MagicMock(text=json.dumps({
        "summary": content,
        "findings": {"f1": "finding"},
        "recommendations": ["rec1"],
        "contradictions": [],
        "risk_flags": [],
    }))]
    return resp


# ── _needs_ai_cycle ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ai_cycle_not_needed_when_review_in_progress():
    """SCHEDULED or RUNNING review blocks new cycle."""
    from app.services.profile_intelligence_live_service import _needs_ai_cycle
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    # First call: pending count = 1
    db.execute.return_value.scalar = MagicMock(return_value=1)
    result = await _needs_ai_cycle(db)
    assert result is False


@pytest.mark.asyncio
async def test_ai_cycle_needed_when_no_real_completed():
    """No COMPLETED review with tokens > 0 means cycle is needed."""
    from app.services.profile_intelligence_live_service import _needs_ai_cycle
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    # pending=0, then completed_at=None (no real review ever)
    db.execute.return_value.scalar = MagicMock(return_value=0)
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
    result = await _needs_ai_cycle(db)
    assert result is True


# ── run_ai_review_cycle — key guards ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_ai_review_missing_key_does_not_complete():
    """No key → status must be FAILED_MISSING_KEY, not COMPLETED."""
    from app.services.profile_intelligence_live_service import run_ai_review_cycle

    with patch.dict("os.environ", {}, clear=False):
        with patch(
            "os.environ.get",
            side_effect=lambda k, d="": (
                "" if k == "ANTHROPIC_API_KEY"
                else "2026-07-12T18:21:57Z" if k == "NATIVE_CAPTURE_START_AT"
                else d
            ),
        ):
            db = AsyncMock()
            db.execute = AsyncMock(return_value=MagicMock())
            # Patch _log_activity and DB queries
            db.execute.return_value.fetchone.return_value = (0, 0, 0.0, 0.0)
            db.execute.return_value.fetchall.return_value = []
            db.execute.return_value.scalar_one_or_none.return_value = None  # no DB key

            with patch("app.services.profile_intelligence_live_service._log_activity", AsyncMock()):
                with patch("app.services.ai_keys_service.decrypt_value", side_effect=ValueError("no key")):
                    result = await run_ai_review_cycle(db)

    assert result["status"] != "COMPLETED"
    # Verify the persisted UPDATE is fail-closed.
    update_calls = [call for call in db.execute.await_args_list
                    if "UPDATE profile_ai_reviews" in str(call.args[0])]
    assert update_calls
    assert update_calls[-1].args[1]["status"] == "FAILED_MISSING_KEY"


@pytest.mark.asyncio
async def test_ai_review_zero_tokens_does_not_complete():
    """Response with tokens=0 → FAILED_EMPTY_AI_RESPONSE, never COMPLETED."""
    from app.services.profile_intelligence_live_service import run_ai_review_cycle

    hollow_response = MagicMock()
    hollow_response.usage.input_tokens = 0
    hollow_response.usage.output_tokens = 0
    hollow_response.content = []

    async def fake_create(**kwargs):
        return hollow_response

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock())
        db.execute.return_value.fetchone.return_value = (0, 0, 0.0, 0.0)
        db.execute.return_value.fetchall.return_value = []

        with patch("app.services.profile_intelligence_live_service._log_activity", AsyncMock()):
            with patch("anthropic.AsyncAnthropic") as mock_client:
                mock_client.return_value.messages.create = AsyncMock(return_value=hollow_response)
                result = await run_ai_review_cycle(db)

    assert result["status"] in ("FAILED_EMPTY_AI_RESPONSE", "FAILED_AI_CALL", "FAILED_EMPTY_SUMMARY")
    assert result["status"] != "COMPLETED"


@pytest.mark.asyncio
async def test_ai_review_completed_requires_tokens_and_summary():
    """Real response with tokens > 0 and summary → status COMPLETED."""
    from app.services.profile_intelligence_live_service import run_ai_review_cycle

    real_response = _make_anthropic_response(input_tokens=300, output_tokens=150, content="Real summary")

    async def fake_create(**kwargs):
        return real_response

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock())
        db.execute.return_value.fetchone.return_value = (5, 3, 0.01, 0.6)
        db.execute.return_value.fetchall.return_value = [("REDUCE_RISK", 10)]

        with patch("app.services.profile_intelligence_live_service._log_activity", AsyncMock()):
            with patch("anthropic.AsyncAnthropic") as mock_client:
                mock_client.return_value.messages.create = AsyncMock(return_value=real_response)
                result = await run_ai_review_cycle(db)

    assert result["status"] == "COMPLETED"
    assert result["summary"] == "Real summary"


@pytest.mark.asyncio
async def test_ai_review_api_failure_does_not_complete():
    """Anthropic API error → FAILED_AI_CALL, never COMPLETED."""
    from app.services.profile_intelligence_live_service import run_ai_review_cycle

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock())
        db.execute.return_value.fetchone.return_value = (0, 0, 0.0, 0.0)
        db.execute.return_value.fetchall.return_value = []

        with patch("app.services.profile_intelligence_live_service._log_activity", AsyncMock()):
            with patch("anthropic.AsyncAnthropic") as mock_client:
                mock_client.return_value.messages.create = AsyncMock(
                    side_effect=Exception("Connection refused")
                )
                result = await run_ai_review_cycle(db)

    assert result["status"] == "FAILED_AI_CALL"
    assert result["status"] != "COMPLETED"


@pytest.mark.asyncio
async def test_ai_review_prefers_validated_user_key_over_env_fallback():
    """A validated per-user key wins over a potentially stale environment fallback."""
    from app.services.profile_intelligence_live_service import run_ai_review_cycle

    real_response = _make_anthropic_response()

    db_key_query_called = []

    original_execute = MagicMock()
    original_execute.return_value.fetchone.return_value = (5, 3, 0.01, 0.6)
    original_execute.return_value.fetchall.return_value = [("REDUCE_RISK", 10)]

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-env-key"}):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock())
        db.execute.return_value.fetchone.return_value = (5, 3, 0.01, 0.6)
        db.execute.return_value.fetchall.return_value = [("REDUCE_RISK", 10)]
        db.execute.return_value.scalar_one_or_none.return_value = b"encrypted_blob"

        with patch("app.services.profile_intelligence_live_service._log_activity", AsyncMock()):
            with patch("anthropic.AsyncAnthropic") as mock_client:
                mock_client.return_value.messages.create = AsyncMock(return_value=real_response)
                with patch(
                    "app.services.ai_keys_service.decrypt_value",
                    return_value="sk-ant-user-db-key",
                ) as mock_decrypt:
                    result = await run_ai_review_cycle(db)
                    assert mock_decrypt.called
                    mock_client.assert_called_with(api_key="sk-ant-user-db-key")

    assert result["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_ai_review_reads_db_key_when_env_missing():
    """When ANTHROPIC_API_KEY absent, reads from ai_provider_keys via decrypt_value."""
    from app.services.profile_intelligence_live_service import run_ai_review_cycle

    real_response = _make_anthropic_response()

    with patch.dict("os.environ", {k: v for k, v in __import__("os").environ.items()
                                   if k != "ANTHROPIC_API_KEY"}, clear=True):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock())
        db.execute.return_value.fetchone.return_value = (5, 3, 0.01, 0.6)
        db.execute.return_value.fetchall.return_value = [("REDUCE_RISK", 10)]
        db.execute.return_value.scalar_one_or_none.return_value = b"encrypted_blob"

        with patch("app.services.profile_intelligence_live_service._log_activity", AsyncMock()):
            with patch("app.services.ai_keys_service.decrypt_value",
                       return_value="sk-ant-db-key") as mock_decrypt:
                with patch("anthropic.AsyncAnthropic") as mock_client:
                    mock_client.return_value.messages.create = AsyncMock(return_value=real_response)
                    result = await run_ai_review_cycle(db)
                    # decrypt_value should have been called once with the encrypted blob
                    assert mock_decrypt.called

    assert result["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_ai_review_once_does_not_mutate_profiles_or_suggestions():
    """AI review must never touch profiles, suggestions, watchlists, or shadow_trades."""
    from app.services.profile_intelligence_live_service import run_ai_review_cycle

    real_response = _make_anthropic_response()

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock())
        db.execute.return_value.fetchone.return_value = (5, 3, 0.01, 0.6)
        db.execute.return_value.fetchall.return_value = [("REDUCE_RISK", 10)]

        with patch("app.services.profile_intelligence_live_service._log_activity", AsyncMock()):
            with patch("anthropic.AsyncAnthropic") as mock_client:
                mock_client.return_value.messages.create = AsyncMock(return_value=real_response)
                await run_ai_review_cycle(db)

    # Verify no forbidden table mutations
    forbidden_patterns = [
        "UPDATE profiles",
        "INSERT INTO profiles",
        "UPDATE profile_adjustment_suggestions SET mutation_applied",
        "UPDATE shadow_trades",
        "UPDATE pipeline_watchlists",
        "live_trading_enabled = true",
        "ml_gate_enabled = true",
    ]
    all_sql = " ".join(str(c) for c in db.execute.call_args_list)
    for pattern in forbidden_patterns:
        assert pattern not in all_sql, f"Forbidden SQL found: {pattern}"


@pytest.mark.asyncio
async def test_ai_review_persists_model_tokens_summary():
    """Completed review must persist model_name, tokens_input, tokens_output, summary."""
    from app.services.profile_intelligence_live_service import run_ai_review_cycle

    real_response = _make_anthropic_response(input_tokens=400, output_tokens=180, content="Portfolio looks healthy")

    captured_params = {}

    original_execute = MagicMock()
    original_execute.return_value.fetchone.return_value = (5, 3, 0.01, 0.6)
    original_execute.return_value.fetchall.return_value = [("REDUCE_RISK", 10)]

    def capture_execute(sql, params=None, **kwargs):
        if params and "model_name" in params:
            captured_params.update(params)
        return original_execute.return_value

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test", "PI_AI_MODEL": "claude-haiku-4-5-20251001"}):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock())
        db.execute.side_effect = capture_execute

        with patch("app.services.profile_intelligence_live_service._log_activity", AsyncMock()):
            with patch("anthropic.AsyncAnthropic") as mock_client:
                mock_client.return_value.messages.create = AsyncMock(return_value=real_response)
                result = await run_ai_review_cycle(db)

    assert result["status"] == "COMPLETED"
    assert captured_params.get("ti") == 400
    assert captured_params.get("to") == 180
    assert captured_params.get("model_name") == "claude-haiku-4-5-20251001"
    assert "healthy" in str(captured_params.get("summary") or "")
    assert captured_params.get("status") == "COMPLETED"
