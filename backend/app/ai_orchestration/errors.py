from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class AIErrorCode(StrEnum):
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    TENANT_SCOPE_MISSING = "TENANT_SCOPE_MISSING"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    PROVIDER_KEY_INVALID = "PROVIDER_KEY_INVALID"
    MODEL_UNKNOWN = "MODEL_UNKNOWN"
    MODEL_NOT_ALLOWED = "MODEL_NOT_ALLOWED"
    MODEL_CAPABILITY_MISMATCH = "MODEL_CAPABILITY_MISMATCH"
    MODEL_RESOLUTION_CONFLICT = "MODEL_RESOLUTION_CONFLICT"
    PROMPT_NOT_FOUND = "PROMPT_NOT_FOUND"
    PROMPT_HASH_MISMATCH = "PROMPT_HASH_MISMATCH"
    DATASET_CONTRACT_INVALID = "DATASET_CONTRACT_INVALID"
    DATASET_EMPTY = "DATASET_EMPTY"
    DATASET_LINEAGE_INCOMPLETE = "DATASET_LINEAGE_INCOMPLETE"
    CONFIGURATION_BUNDLE_INCOMPLETE = "CONFIGURATION_BUNDLE_INCOMPLETE"
    BUDGET_DENIED = "BUDGET_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_AUTH_ERROR = "PROVIDER_AUTH_ERROR"
    PROVIDER_CREDIT_EXHAUSTED = "PROVIDER_CREDIT_EXHAUSTED"
    PROVIDER_BAD_REQUEST = "PROVIDER_BAD_REQUEST"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_SERVER_ERROR = "PROVIDER_SERVER_ERROR"
    OUTPUT_SCHEMA_INVALID = "OUTPUT_SCHEMA_INVALID"
    PROMPT_INJECTION_BLOCKED = "PROMPT_INJECTION_BLOCKED"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    TOOL_SIDE_EFFECT_DENIED = "TOOL_SIDE_EFFECT_DENIED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    STALE_JOB_RECOVERED = "STALE_JOB_RECOVERED"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AIError(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: AIErrorCode
    retryable: bool
    http_status: int
    operator_action: str
    safe_message: str
    internal_detail_redacted: str | None = None
    provider_error_code: str | None = None
    attempt: int = 1


class AIOrchestrationError(RuntimeError):
    def __init__(self, detail: AIError):
        self.detail = detail
        super().__init__(detail.safe_message)


def fail(code: AIErrorCode, message: str, *, retryable: bool = False, http_status: int = 422,
         operator_action: str = "Review the request and audit trail", attempt: int = 1) -> AIOrchestrationError:
    return AIOrchestrationError(AIError(
        code=code, retryable=retryable, http_status=http_status,
        operator_action=operator_action, safe_message=message, attempt=attempt,
    ))
