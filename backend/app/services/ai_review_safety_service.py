"""Fail-closed rules for Profile Intelligence AI reviews."""
from __future__ import annotations
from datetime import datetime

COMPLETED = "COMPLETED"
LEGACY_HOLLOW_REVIEW = "LEGACY_HOLLOW_REVIEW"
FAILED_EMPTY_AI_RESPONSE = "FAILED_EMPTY_AI_RESPONSE"


def completed_review_contract_is_valid(*, status: str, tokens_input: int | None,
                                       tokens_output: int | None, summary: str | None,
                                       model_name: str | None,
                                       completed_at: datetime | None) -> bool:
    """A completed review is real only when every persisted proof is present."""
    if status != COMPLETED:
        return True
    return ((tokens_input or 0) > 0 and (tokens_output or 0) > 0
            and bool((summary or "").strip()) and bool((model_name or "").strip())
            and completed_at is not None)


def is_hollow_completed_review(*, status: str, tokens_input: int | None,
                               tokens_output: int | None, summary: str | None) -> bool:
    """Match the operational hollow-review definition used by the safety guard."""
    return (status == COMPLETED and (tokens_input or 0) == 0
            and (tokens_output or 0) == 0 and not (summary or "").strip())


def reclassified_status(*, requested_at: datetime | None, created_at: datetime,
                        fix_deployed_at: datetime) -> str:
    """Keep pre-fix artifacts as legacy; fail closed for any post-fix artifact."""
    return (LEGACY_HOLLOW_REVIEW if (requested_at or created_at) < fix_deployed_at
            else FAILED_EMPTY_AI_RESPONSE)
