from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class GuardDecision(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    VETO = "VETO"
    INVARIANT_CONFLICT = "INVARIANT_CONFLICT"


class RecommendationValidation(BaseModel):
    model_config = ConfigDict(frozen=True)
    module: str
    decision: GuardDecision
    reasons: tuple[str, ...] = ()


class CandidateGuardResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    allowed: bool
    terminal_reason: str
    validations: tuple[RecommendationValidation, ...] = ()


class RecommendationGuard:
    @staticmethod
    def require_candidate_allowed(
        risk: RecommendationValidation,
        strategy: RecommendationValidation,
    ) -> CandidateGuardResult:
        validations = (risk, strategy)
        if risk.decision is GuardDecision.VETO:
            return CandidateGuardResult(
                allowed=False, terminal_reason="GLOBAL_RISK_VETO", validations=validations,
            )
        if risk.decision is GuardDecision.INVARIANT_CONFLICT:
            return CandidateGuardResult(
                allowed=False, terminal_reason="GLOBAL_RISK_INVARIANT_CONFLICT", validations=validations,
            )
        if strategy.decision is GuardDecision.VETO:
            return CandidateGuardResult(
                allowed=False, terminal_reason="STRATEGY_VETO", validations=validations,
            )
        if strategy.decision is GuardDecision.INVARIANT_CONFLICT:
            return CandidateGuardResult(
                allowed=False, terminal_reason="STRATEGY_INVARIANT_CONFLICT", validations=validations,
            )
        return CandidateGuardResult(allowed=True, terminal_reason="GUARDS_PASS", validations=validations)

    @staticmethod
    def validate_spot_authority(
        *, target_path: str, human_decision_id: str | None,
    ) -> CandidateGuardResult:
        if "spot" in target_path.lower() and not human_decision_id:
            return CandidateGuardResult(allowed=False, terminal_reason="AI_SPOT_AUTHORITY_BLOCKED")
        return CandidateGuardResult(allowed=True, terminal_reason="SPOT_HUMAN_AUTHORITY_PROVEN")
