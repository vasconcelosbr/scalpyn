from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ModuleContextRefs(BaseModel):
    model_config = ConfigDict(frozen=True)
    shadow_snapshot_id: UUID | None = None
    social_score_snapshot_id: UUID | None = None
    market_regime_snapshot_id: UUID | None = None
    ml_authority_snapshot_id: UUID | None = None


class AnalysisContextManifest(BaseModel):
    model_config = ConfigDict(frozen=True)
    contract_version: str = "analysis-context-manifest-v1"
    modules_requested: tuple[str, ...]
    modules_consulted: tuple[str, ...]
    tools_called: tuple[str, ...]
    freshness: dict[str, str | int | None]
    quality: dict[str, str]
    evidence_ids: tuple[str, ...]
    conflicts: tuple[dict[str, Any], ...] = ()
    vetoes: tuple[dict[str, Any], ...] = ()
    input_contract_version: str | None = None
    capture_at: datetime | None = None
    report_run_id: UUID | None = None
    source_item_count: int | None = None
    processed_item_count: int | None = None
    coverage_status: str | None = None
    shard_count: int | None = None
    dataset_hash: str | None = None
    legacy_incomplete: bool = True
    coverage_by_path: dict[str, dict[str, int]] = Field(default_factory=dict)
    ordered_item_hashes: tuple[str, ...] = ()
    shard_plan: tuple[dict[str, Any], ...] = ()
    missing_required_fields: tuple[dict[str, Any], ...] = ()


class StructuredRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True)
    target_module: str
    target_entity_id: str | None = None
    target_path: str
    current_value: Any = None
    proposed_value: Any = None
    operation: str
    side_effect_class: str = "NONE"
    confidence: float = Field(ge=0, le=1)
    expected_impact: dict[str, Any] = Field(default_factory=dict)
    risk_conflicts: tuple[dict[str, Any], ...] = ()
    strategy_conflicts: tuple[dict[str, Any], ...] = ()
    validation_plan: dict[str, Any] = Field(default_factory=dict)
    rollback_plan: dict[str, Any] = Field(default_factory=dict)


class SystemicAnalysisOutput(BaseModel):
    model_config = ConfigDict(frozen=True)
    diagnosis: str
    root_cause_classification: str
    affected_modules: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...]
    discarded_hypotheses: tuple[dict[str, Any], ...] = ()
    data_quality: dict[str, Any]
    market_regime: dict[str, Any]
    memory_hits: tuple[dict[str, Any], ...] = ()
    recommendations: tuple[StructuredRecommendation, ...] = ()
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class SocialScoreSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    window_start: datetime | None = None
    window_end: datetime | None = None
    sources: tuple[str, ...] = ()
    mentions: int | None = Field(default=None, ge=0)
    sentiment: float | None = None
    trend: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    coverage: float | None = Field(default=None, ge=0, le=1)
    freshness_seconds: int | None = Field(default=None, ge=0)
    missingness: tuple[str, ...] = ()
    anomaly_flags: tuple[str, ...] = ()
    contract_version: str = "social-score-snapshot-v1"
    snapshot_id: UUID | None = None
    snapshot_hash: str | None = None


_INSTRUCTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?\.?", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL),
)


def sanitize_social_text(value: str, *, max_length: int = 2_000) -> str:
    sanitized = value
    for pattern in _INSTRUCTION_PATTERNS:
        sanitized = pattern.sub("[removed-untrusted-instruction]", sanitized)
    sanitized = re.sub(r"<[^>]+>", "", sanitized)
    return sanitized[:max_length]
