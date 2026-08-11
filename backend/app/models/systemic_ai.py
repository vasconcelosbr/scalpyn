"""Persistent records for the tenant-scoped systemic AI foundation."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import TIMESTAMP

from ..database import Base


def _now():
    return datetime.now(timezone.utc)


class AIPromptVersion(Base):
    __tablename__ = "ai_prompt_versions"
    __table_args__ = (UniqueConstraint("prompt_key", "semantic_version", name="uq_ai_prompt_key_version"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prompt_key = Column(String(160), nullable=False)
    semantic_version = Column(String(40), nullable=False)
    status = Column(String(20), nullable=False)
    system_template = Column(Text, nullable=False)
    user_template = Column(Text, nullable=False)
    input_schema_json = Column(JSONB, nullable=False)
    output_schema_json = Column(JSONB, nullable=False)
    tool_policy_json = Column(JSONB, nullable=False, default=dict)
    provider_constraints_json = Column(JSONB, nullable=False, default=dict)
    content_hash = Column(String(64), nullable=False, unique=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    approved_at = Column(TIMESTAMP(timezone=True))
    deprecated_at = Column(TIMESTAMP(timezone=True))


class AIModuleCapabilityRecord(Base):
    __tablename__ = "ai_module_capabilities"
    __table_args__ = (
        UniqueConstraint("module_key", "semantic_version", name="uq_ai_module_capability_key_version"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    module_key = Column(String(120), nullable=False)
    semantic_version = Column(String(40), nullable=False)
    entities = Column(JSONB, nullable=False, default=list)
    read_tools = Column(JSONB, nullable=False, default=list)
    write_tools = Column(JSONB, nullable=False, default=list)
    dependencies = Column(JSONB, nullable=False, default=list)
    freshness_sla_seconds = Column(Integer)
    risk_class = Column(String(48), nullable=False)
    tenant_scoped = Column(Boolean, nullable=False, default=True)
    content_hash = Column(String(64), nullable=False, unique=True)
    status = Column(String(24), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    approved_at = Column(TIMESTAMP(timezone=True))
    deprecated_at = Column(TIMESTAMP(timezone=True))


class AIAnalysisProfileRecord(Base):
    __tablename__ = "ai_analysis_profiles"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_ai_analysis_profile_slug"),
        Index("ix_ai_analysis_profile_active_order", "is_active", "display_order"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String(80), nullable=False)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=False)
    provider = Column(String(40), nullable=False)
    model = Column(String(200), nullable=False)
    analysis_mode = Column(String(40), nullable=False)
    authority = Column(String(40), nullable=False, default="ANALYSIS_ONLY")
    question_template = Column(Text, nullable=False)
    max_cost_usd = Column(Numeric(18, 8), nullable=False)
    input_cost_per_million = Column(Numeric(18, 8), nullable=False)
    output_cost_per_million = Column(Numeric(18, 8), nullable=False)
    max_input_tokens = Column(Integer, nullable=False)
    max_output_tokens = Column(Integer, nullable=False)
    request_token_limit = Column(Integer, nullable=False)
    daily_token_limit = Column(Integer, nullable=False)
    monthly_token_limit = Column(Integer, nullable=False)
    pricing_source_url = Column(Text, nullable=False)
    pricing_observed_at = Column(TIMESTAMP(timezone=True), nullable=False)
    pricing_valid_until = Column(TIMESTAMP(timezone=True), nullable=False)
    profile_version = Column(Integer, nullable=False, default=1)
    profile_hash = Column(String(64), nullable=False, unique=True)
    display_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class AIModelApprovalRecord(Base):
    __tablename__ = "ai_model_approvals"
    __table_args__ = (
        Index("ix_ai_model_approval_tenant_model_time", "tenant_id", "provider", "model", "expires_at"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(40), nullable=False)
    model = Column(String(200), nullable=False)
    max_cost_usd = Column(Numeric(18, 8), nullable=False)
    input_cost_per_million = Column(Numeric(18, 8), nullable=False)
    output_cost_per_million = Column(Numeric(18, 8), nullable=False)
    max_output_tokens = Column(Integer, nullable=False)
    pricing_source_url = Column(Text, nullable=False)
    pricing_observed_at = Column(TIMESTAMP(timezone=True), nullable=False)
    pricing_snapshot_hash = Column(String(64), nullable=False)
    approval_phrase_hash = Column(String(64), nullable=False)
    approval_method = Column(String(40), nullable=False, default="HUMAN_PHRASE")
    analysis_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_analysis_profiles.id", ondelete="RESTRICT"),
    )
    scope = Column(String(80), nullable=False)
    status = Column(String(24), nullable=False, default="APPROVED")
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    approved_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    content_hash = Column(String(64), nullable=False, unique=True)


class AIModelAlias(Base):
    __tablename__ = "ai_model_aliases"
    __table_args__ = (UniqueConstraint("provider", "alias", name="uq_ai_model_alias_provider"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alias = Column(String(160), nullable=False)
    provider = Column(String(40), nullable=False)
    real_model_id = Column(String(200), nullable=False)
    valid_from = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    valid_to = Column(TIMESTAMP(timezone=True))
    status = Column(String(20), nullable=False, default="ACTIVE")
    capabilities = Column(JSONB, nullable=False, default=list)


class AIModelResolutionRecord(Base):
    __tablename__ = "ai_model_resolutions"
    __table_args__ = (Index("ix_ai_model_resolution_tenant_resolved", "tenant_id", "resolved_at"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    requested_provider = Column(String(40))
    requested_model = Column(String(200))
    configured_provider = Column(String(40))
    configured_model = Column(String(200))
    effective_provider = Column(String(40), nullable=False)
    effective_model = Column(String(200), nullable=False)
    catalog_snapshot_hash = Column(String(64), nullable=False)
    capabilities = Column(JSONB, nullable=False)
    resolution_policy_version = Column(String(80), nullable=False)
    resolution_reason = Column(String(120), nullable=False)
    resolved_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class AIConfigurationBundleRecord(Base):
    __tablename__ = "ai_configuration_bundles"
    __table_args__ = (Index("ix_ai_bundle_tenant_created", "tenant_id", "created_at"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    profile_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"))
    # These two legacy tables are SQL-only and have no SQLAlchemy mappings in
    # this codebase. The database migration owns their FK constraints; keeping
    # unresolved ORM ForeignKey objects here prevents every bundle flush.
    profile_version_id = Column(UUID(as_uuid=True))
    score_engine_version_id = Column(UUID(as_uuid=True))
    lineage_refs = Column(JSONB, nullable=False, default=dict)
    bundle_json = Column(JSONB, nullable=False)
    bundle_hash = Column(String(64), nullable=False)
    lineage_status = Column(String(40), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class AIDatasetSnapshotRecord(Base):
    __tablename__ = "ai_dataset_snapshots"
    __table_args__ = (Index("ix_ai_dataset_tenant_created", "tenant_id", "created_at"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    contract_version = Column(String(80), nullable=False)
    origin_module = Column(String(120))
    module_context_refs = Column(JSONB)
    context_manifest = Column(JSONB)
    source_tables = Column(JSONB, nullable=False)
    source_labels = Column(JSONB, nullable=False)
    event_identity_contract = Column(String(160), nullable=False)
    outcome_contract = Column(String(160), nullable=False)
    time_anchor = Column(String(80), nullable=False)
    window_start = Column(TIMESTAMP(timezone=True), nullable=False)
    window_end = Column(TIMESTAMP(timezone=True), nullable=False)
    filters = Column(JSONB, nullable=False, default=dict)
    exclusions = Column(JSONB, nullable=False, default=list)
    row_count = Column(Integer, nullable=False)
    row_ids_hash = Column(String(64), nullable=False)
    query_hash = Column(String(64), nullable=False)
    dataset_hash = Column(String(64), nullable=False)
    configuration_bundle_id = Column(UUID(as_uuid=True), ForeignKey("ai_configuration_bundles.id", ondelete="RESTRICT"), nullable=False)
    quality_status = Column(String(48), nullable=False)
    quality_findings = Column(JSONB, nullable=False, default=list)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class AIRequestRecord(Base):
    __tablename__ = "ai_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "correlation_id", name="uq_ai_request_tenant_correlation"),
        Index("ix_ai_request_tenant_created", "tenant_id", "created_at"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    origin_module = Column(String(120), nullable=False)
    origin_view = Column(String(200))
    analysis_mode = Column(String(40), nullable=False)
    authority = Column(String(40), nullable=False)
    question_hash = Column(String(64), nullable=False)
    correlation_id = Column(String(160), nullable=False)
    model_resolution_id = Column(UUID(as_uuid=True), ForeignKey("ai_model_resolutions.id", ondelete="RESTRICT"), nullable=False)
    prompt_version_id = Column(UUID(as_uuid=True), ForeignKey("ai_prompt_versions.id", ondelete="RESTRICT"), nullable=False)
    dataset_snapshot_id = Column(UUID(as_uuid=True), ForeignKey("ai_dataset_snapshots.id", ondelete="RESTRICT"), nullable=False)
    configuration_bundle_id = Column(UUID(as_uuid=True), ForeignKey("ai_configuration_bundles.id", ondelete="RESTRICT"), nullable=False)
    request_json = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class AIJobRecord(Base):
    __tablename__ = "ai_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_ai_job_tenant_dedupe"),
        Index("ix_ai_job_tenant_status_time", "tenant_id", "status", "queued_at"),
        Index("ix_ai_job_lease_expiry", "status", "lease_expires_at"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ai_request_id = Column(UUID(as_uuid=True), ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False)
    purpose = Column(String(120), nullable=False)
    dedupe_key = Column(String(64), nullable=False)
    status = Column(String(40), nullable=False)
    queued_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    started_at = Column(TIMESTAMP(timezone=True))
    heartbeat_at = Column(TIMESTAMP(timezone=True))
    lease_owner = Column(String(160))
    lease_expires_at = Column(TIMESTAMP(timezone=True))
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    retry_after = Column(TIMESTAMP(timezone=True))
    completed_at = Column(TIMESTAMP(timezone=True))
    terminal_reason = Column(String(160))
    last_error_code = Column(String(80))
    last_error_safe_message = Column(Text)


class AIResultRecord(Base):
    __tablename__ = "ai_results"
    __table_args__ = (UniqueConstraint("tenant_id", "ai_request_id", name="uq_ai_result_tenant_request"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ai_request_id = Column(UUID(as_uuid=True), ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(40), nullable=False)
    result_json = Column(JSONB, nullable=False)
    terminal_reason = Column(String(160))
    completed_at = Column(TIMESTAMP(timezone=True))


class AIUsageRecord(Base):
    __tablename__ = "ai_usage_records"
    __table_args__ = (Index("ix_ai_usage_tenant_created", "tenant_id", "created_at"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ai_request_id = Column(UUID(as_uuid=True), ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(40), nullable=False)
    model = Column(String(200), nullable=False)
    module = Column(String(120), nullable=False)
    tokens_input = Column(Integer, nullable=False)
    tokens_output = Column(Integer, nullable=False)
    estimated_cost = Column(Numeric(18, 8), nullable=False, default=0)
    actual_cost = Column(Numeric(18, 8), nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="USD")
    pricing_snapshot_version = Column(String(80), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class AIBudgetPolicyRecord(Base):
    __tablename__ = "ai_budget_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "model", "module", name="uq_ai_budget_scope"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(40), nullable=False)
    model = Column(String(200))
    module = Column(String(120))
    daily_token_limit = Column(Integer)
    monthly_token_limit = Column(Integer)
    request_token_limit = Column(Integer, nullable=False)
    null_limit_policy = Column(String(20), nullable=False, default="DENY")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class AIBudgetReservationRecord(Base):
    __tablename__ = "ai_budget_reservations"
    __table_args__ = (
        UniqueConstraint("ai_request_id", name="uq_ai_budget_reservation_request"),
        Index("ix_ai_budget_reservation_tenant_created", "tenant_id", "created_at"),
        Index("ix_ai_budget_reservation_status_updated", "status", "updated_at"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ai_request_id = Column(UUID(as_uuid=True), ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False)
    budget_policy_id = Column(UUID(as_uuid=True), ForeignKey("ai_budget_policies.id", ondelete="SET NULL"))
    model_approval_id = Column(UUID(as_uuid=True), ForeignKey("ai_model_approvals.id", ondelete="SET NULL"))
    request_intent = Column(String(40), nullable=False)
    provider = Column(String(40), nullable=False)
    model = Column(String(200), nullable=False)
    module = Column(String(120), nullable=False)
    status = Column(String(32), nullable=False)
    estimated_input_tokens = Column(Integer, nullable=False)
    max_output_tokens = Column(Integer, nullable=False)
    request_token_limit = Column(Integer, nullable=False)
    daily_token_limit = Column(Integer, nullable=False)
    monthly_token_limit = Column(Integer, nullable=False)
    reserved_tokens = Column(Integer, nullable=False)
    reserved_cost_usd = Column(Numeric(18, 8), nullable=False, default=0)
    actual_tokens = Column(Integer)
    actual_cost_usd = Column(Numeric(18, 8))
    released_tokens = Column(Integer, nullable=False, default=0)
    overage_tokens = Column(Integer, nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="USD")
    provider_transport_attempted = Column(Boolean, nullable=False, default=False)
    terminal_reason = Column(String(160))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now, onupdate=_now)
    transport_started_at = Column(TIMESTAMP(timezone=True))
    reconciled_at = Column(TIMESTAMP(timezone=True))
    released_at = Column(TIMESTAMP(timezone=True))


class AIToolCallAudit(Base):
    __tablename__ = "ai_tool_call_audits"
    __table_args__ = (Index("ix_ai_tool_audit_tenant_created", "tenant_id", "created_at"),)
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ai_request_id = Column(UUID(as_uuid=True), ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False)
    tool_name = Column(String(160), nullable=False)
    tool_version = Column(String(40), nullable=False)
    side_effect = Column(String(40), nullable=False)
    status = Column(String(40), nullable=False)
    input_hash = Column(String(64), nullable=False)
    output_hash = Column(String(64))
    denial_reason = Column(String(160))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)


class AIToolEvidenceRecord(Base):
    __tablename__ = "ai_tool_evidence"
    __table_args__ = (
        Index("ix_ai_tool_evidence_request_module", "tenant_id", "ai_request_id", "module_key", "created_at"),
    )
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ai_request_id = Column(UUID(as_uuid=True), ForeignKey("ai_requests.id", ondelete="CASCADE"), nullable=False)
    tool_call_audit_id = Column(UUID(as_uuid=True), ForeignKey("ai_tool_call_audits.id", ondelete="CASCADE"), nullable=False, unique=True)
    module_key = Column(String(120), nullable=False)
    tool_name = Column(String(160), nullable=False)
    output_json = Column(JSONB, nullable=False)
    output_hash = Column(String(64), nullable=False)
    freshness_json = Column(JSONB)
    quality = Column(String(48), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_now)
