from __future__ import annotations

from dataclasses import dataclass

from .errors import AIErrorCode


@dataclass(frozen=True)
class ProviderFailurePolicy:
    code: AIErrorCode
    retryable: bool
    retry_after_seconds: int | None = None


def classify_provider_status(status: int, *, retry_after_seconds: int | None = None) -> ProviderFailurePolicy:
    if status in (401, 403):
        return ProviderFailurePolicy(AIErrorCode.PROVIDER_AUTH_ERROR, False)
    if status == 402:
        return ProviderFailurePolicy(AIErrorCode.PROVIDER_CREDIT_EXHAUSTED, False)
    if status == 408:
        return ProviderFailurePolicy(AIErrorCode.PROVIDER_TIMEOUT, True)
    if status == 429:
        return ProviderFailurePolicy(AIErrorCode.RATE_LIMITED, True, retry_after_seconds)
    if 500 <= status <= 599:
        return ProviderFailurePolicy(AIErrorCode.PROVIDER_SERVER_ERROR, True)
    return ProviderFailurePolicy(AIErrorCode.PROVIDER_BAD_REQUEST, False)


def retry_delays(policy: ProviderFailurePolicy, *, max_attempts: int, base_seconds: float = 1.0) -> tuple[float, ...]:
    if not policy.retryable:
        return ()
    return tuple(float(policy.retry_after_seconds or base_seconds * (2 ** attempt)) for attempt in range(max_attempts - 1))
