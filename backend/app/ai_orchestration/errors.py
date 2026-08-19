from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class AIErrorCode(StrEnum):
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    TENANT_SCOPE_MISSING = "TENANT_SCOPE_MISSING"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    PROVIDER_BLOCKED = "PROVIDER_BLOCKED"
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


class ProviderBlockedError(RuntimeError):
    """Fail-closed provider gate with safe, machine-readable diagnostics."""

    error_kind = AIErrorCode.PROVIDER_BLOCKED.value
    provider_transport_attempted = False

    def __init__(self, reason_code: str, safe_message: str):
        self.reason_code = reason_code
        self.safe_message = safe_message
        super().__init__(reason_code)


class GraphNodeExecutionError(RuntimeError):
    """Carries the failing node across the node transaction rollback."""

    def __init__(self, node_name: str, cause: Exception):
        self.node_name = node_name
        self.cause = cause
        super().__init__(str(cause))


class ProviderTransportError(RuntimeError):
    """Redacted transport failure after an external call was attempted."""

    error_kind = "PROVIDER_TRANSPORT_FAILED"
    provider_transport_attempted = True

    def __init__(self, reason_code: str = "PROVIDER_TRANSPORT_FAILED"):
        self.reason_code = reason_code
        self.safe_message = "Provider transport failed after the request was attempted"
        super().__init__(reason_code)


class ProviderOutputError(RuntimeError):
    """Typed failure for an incomplete or invalid response after transport."""

    error_kind = "PROVIDER_OUTPUT_FAILED"
    provider_transport_attempted = True

    def __init__(self, reason_code: str, diagnostics: dict | None = None):
        self.reason_code = reason_code
        self.safe_message = "Provider returned an incomplete or invalid structured response"
        # AUD-IR-CTR-001 (4.3/L14): only the already-safe subset of
        # execute_prepared_request's return value -- stop_reason, schema
        # validation path, opaque provider request-id ref. Never raw prompt
        # or response text; callers must not widen this without re-reviewing
        # what's safe to persist to a queryable audit table.
        self.diagnostics = diagnostics
        super().__init__(reason_code)


class GovernedProposalError(RuntimeError):
    """Typed fail-closed rejection for an unsafe or inapplicable draft."""

    error_kind = "GOVERNED_PROPOSAL_INVALID"
    provider_transport_attempted = True

    def __init__(self, reason_code: str = "ANALYSIS_CHAT_PROPOSAL_NOT_APPLICABLE"):
        self.reason_code = reason_code
        self.safe_message = (
            "A governed preview could not be generated because the proposed paths "
            "do not match the current configuration contract"
        )
        super().__init__(reason_code)


def fail(code: AIErrorCode, message: str, *, retryable: bool = False, http_status: int = 422,
         operator_action: str = "Review the request and audit trail", attempt: int = 1) -> AIOrchestrationError:
    return AIOrchestrationError(AIError(
        code=code, retryable=retryable, http_status=http_status,
        operator_action=operator_action, safe_message=message, attempt=attempt,
    ))
