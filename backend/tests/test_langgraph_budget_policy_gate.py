from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.ai_modules import CreateModelApprovalRequest


def _payload(**overrides):
    payload = {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "max_cost_usd": Decimal("0.01"),
        "input_cost_per_million": Decimal("1"),
        "output_cost_per_million": Decimal("5"),
        "pricing_source_url": "https://docs.anthropic.com/en/docs/about-claude/pricing",
        "pricing_observed_at": datetime.now(timezone.utc),
        "approval_phrase": "APROVO MODELO E CUSTO",
        "scope": "SYSTEMIC_MODULE_ANALYSIS",
        "module": "shadow_portfolio",
        "max_input_tokens": 1_500,
        "max_output_tokens": 300,
        "request_token_limit": 1_800,
        "daily_token_limit": 1_800,
        "monthly_token_limit": 1_800,
    }
    payload.update(overrides)
    return payload


def test_model_approval_requires_request_budget_to_cover_reservation():
    with pytest.raises(ValidationError, match="request_token_limit"):
        CreateModelApprovalRequest(**_payload(request_token_limit=1_799))


def test_model_approval_requires_bounded_daily_and_monthly_limits():
    with pytest.raises(ValidationError, match="daily_token_limit"):
        CreateModelApprovalRequest(**_payload(daily_token_limit=1_799))
    with pytest.raises(ValidationError, match="monthly_token_limit"):
        CreateModelApprovalRequest(**_payload(monthly_token_limit=1_799))


def test_runtime_budget_denials_are_before_provider_transport():
    backend = Path(__file__).resolve().parents[1]
    source = (backend / "app/services/systemic_langgraph_bridge.py").read_text(encoding="utf-8")
    provider_call = source.index("response = await SystemicLangGraphBridge.execute_json_provider")
    for marker in (
        "BOUNDED_AI_BUDGET_POLICY_REQUIRED",
        "AI_INPUT_RESERVATION_EXCEEDED",
        "AI_REQUEST_TOKEN_BUDGET_EXCEEDED",
        "AI_DAILY_TOKEN_BUDGET_EXCEEDED",
        "AI_MONTHLY_TOKEN_BUDGET_EXCEEDED",
        "MODEL_COST_APPROVAL_LIMIT_EXCEEDED_BEFORE_CALL",
    ):
        assert source.index(marker) < provider_call


def test_model_approval_persists_exact_module_budget_policy():
    backend = Path(__file__).resolve().parents[1]
    source = (backend / "app/api/ai_modules.py").read_text(encoding="utf-8")
    assert "AIBudgetPolicyRecord.tenant_id == user_id" in source
    assert "AIBudgetPolicyRecord.module == payload.module" in source
    assert 'budget.null_limit_policy = "DENY"' in source
    assert "MODEL_COST_CAP_BELOW_WORST_CASE" in source
