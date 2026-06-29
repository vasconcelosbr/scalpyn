"""Centralized constants for profile mutation actions and statuses.

Used by autopilot_engine, profile_intelligence_live_service, and API serializers
to ensure a consistent audit trail vocabulary across all mutation sources.
"""


class MutationActionType:
    # Signal (entry conditions)
    SIGNAL_INSERTED             = "SIGNAL_INSERTED"
    SIGNAL_UPDATED              = "SIGNAL_UPDATED"
    SIGNAL_REMOVED              = "SIGNAL_REMOVED"
    # Scoring rules
    SCORING_RULE_INSERTED       = "SCORING_RULE_INSERTED"
    SCORING_RULE_POINTS_ADJUSTED = "SCORING_RULE_POINTS_ADJUSTED"
    SCORING_RULE_REMOVED        = "SCORING_RULE_REMOVED"
    # Block rules
    BLOCK_RULE_INSERTED         = "BLOCK_RULE_INSERTED"
    BLOCK_RULE_UPDATED          = "BLOCK_RULE_UPDATED"
    BLOCK_RULE_REMOVED          = "BLOCK_RULE_REMOVED"
    # Thresholds / ranges
    RANGE_UPDATED               = "RANGE_UPDATED"
    BUY_THRESHOLD_UPDATED       = "BUY_THRESHOLD_UPDATED"
    MIN_SCORE_ADJUSTED          = "MIN_SCORE_ADJUSTED"
    # No-op
    NO_ACTION_AUTO_REJECTED     = "NO_ACTION_AUTO_REJECTED"

    # Legacy aliases used by autopilot_engine.py (kept for backward compat)
    RULES_ADJUSTED              = "RULES_ADJUSTED"
    MUTATED                     = "MUTATED"
    BLOCK_RULES_ADJUSTED        = "BLOCK_RULES_ADJUSTED"
    ENTRY_TRIGGERS_ADJUSTED     = "ENTRY_TRIGGERS_ADJUSTED"


class MutationStatus:
    # Applied states
    DRY_RUN_ONLY                    = "DRY_RUN_ONLY"
    APPLIED_TO_SHADOW               = "APPLIED_TO_SHADOW"
    APPLIED_TO_PROFILE_CONFIG       = "APPLIED_TO_PROFILE_CONFIG"
    # Rejection states
    AUTO_REJECTED_INSUFFICIENT_EVIDENCE = "AUTO_REJECTED_INSUFFICIENT_EVIDENCE"
    AUTO_REJECTED_RISK              = "AUTO_REJECTED_RISK"
    AUTO_REJECTED_PROFILE_CONFLICT  = "AUTO_REJECTED_PROFILE_CONFLICT"
    AUTO_REJECTED_DUPLICATE_RULE    = "AUTO_REJECTED_DUPLICATE_RULE"
    AUTO_REJECTED_LOW_SAMPLE        = "AUTO_REJECTED_LOW_SAMPLE"
    AUTO_REJECTED_NEGATIVE_EV       = "AUTO_REJECTED_NEGATIVE_EV"
    # Approved but not yet applied
    AUTO_APPROVED_FOR_SHADOW        = "AUTO_APPROVED_FOR_SHADOW"
    # Archived without decision
    AUTO_ARCHIVED_HYPOTHESIS        = "AUTO_ARCHIVED_HYPOTHESIS"

    @staticmethod
    def from_dry_run_and_applied(dry_run: bool, mutation_applied: bool) -> str:
        if mutation_applied:
            return MutationStatus.APPLIED_TO_PROFILE_CONFIG
        if dry_run:
            return MutationStatus.DRY_RUN_ONLY
        return MutationStatus.APPLIED_TO_SHADOW


# Human-readable labels for the UI
MUTATION_STATUS_LABELS: dict[str, str] = {
    MutationStatus.DRY_RUN_ONLY:                    "Simulação (dry run)",
    MutationStatus.APPLIED_TO_SHADOW:               "Aplicado em shadow",
    MutationStatus.APPLIED_TO_PROFILE_CONFIG:       "Aplicado no profile versionado",
    MutationStatus.AUTO_REJECTED_INSUFFICIENT_EVIDENCE: "Rejeitado — evidência insuficiente",
    MutationStatus.AUTO_REJECTED_RISK:              "Rejeitado — risco elevado",
    MutationStatus.AUTO_REJECTED_PROFILE_CONFLICT:  "Rejeitado — conflito com skill do profile",
    MutationStatus.AUTO_REJECTED_DUPLICATE_RULE:    "Rejeitado — regra duplicada",
    MutationStatus.AUTO_REJECTED_LOW_SAMPLE:        "Rejeitado — amostra insuficiente",
    MutationStatus.AUTO_REJECTED_NEGATIVE_EV:       "Rejeitado — EV negativo",
    MutationStatus.AUTO_APPROVED_FOR_SHADOW:        "Aprovado para shadow",
    MutationStatus.AUTO_ARCHIVED_HYPOTHESIS:        "Hipótese arquivada",
}

# Map from combination blocked_reason → MutationStatus
BLOCKED_REASON_TO_DECISION: dict[str, str] = {
    "blocked_no_validation":            MutationStatus.AUTO_REJECTED_LOW_SAMPLE,
    "blocked_low_discovery_support":    MutationStatus.AUTO_REJECTED_LOW_SAMPLE,
    "blocked_low_validation_support":   MutationStatus.AUTO_REJECTED_LOW_SAMPLE,
    "blocked_missing_feature":          MutationStatus.AUTO_REJECTED_PROFILE_CONFLICT,
    "blocked_validation_lift":          MutationStatus.AUTO_REJECTED_INSUFFICIENT_EVIDENCE,
    "blocked_validation_winrate":       MutationStatus.AUTO_REJECTED_INSUFFICIENT_EVIDENCE,
    "blocked_single_symbol_dependency": MutationStatus.AUTO_REJECTED_RISK,
    "blocked_single_day_dependency":    MutationStatus.AUTO_REJECTED_RISK,
    "migration_requires_registry_review": MutationStatus.AUTO_REJECTED_PROFILE_CONFLICT,
    "exploratory_only":                 MutationStatus.AUTO_ARCHIVED_HYPOTHESIS,
}
