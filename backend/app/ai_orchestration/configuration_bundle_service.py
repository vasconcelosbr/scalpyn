from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .errors import AIErrorCode, fail
from .hashing import canonical_hash


class ConfigurationBundle(BaseModel):
    model_config = ConfigDict(frozen=True)
    configuration_bundle_id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    profile_id: UUID | None = None
    profile_version_id: UUID | None = None
    score_engine_version_id: UUID | None = None
    risk_policy_version_id: UUID | None = None
    strategy_policy_version_id: UUID | None = None
    spot_policy_version_id: UUID | None = None
    feature_contract_version: str | None = None
    label_contract_version: str | None = None
    ml_model_id: UUID | None = None
    model_lane: str | None = None
    market_regime_id: UUID | None = None
    exit_policy_version_id: UUID | None = None
    bundle_json: dict[str, Any]
    bundle_hash: str
    lineage_status: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConfigurationBundleService:
    @staticmethod
    def create(*, tenant_id: UUID, bundle_json: dict[str, Any], **lineage: Any) -> ConfigurationBundle:
        normalized = {"tenant_id": str(tenant_id), "bundle_json": bundle_json, **{
            key: str(value) if isinstance(value, UUID) else value for key, value in lineage.items()
        }}
        required = ("profile_version_id", "score_engine_version_id") if lineage.get("profile_id") else ()
        complete = all(lineage.get(key) is not None for key in required)
        return ConfigurationBundle(
            tenant_id=tenant_id, bundle_json=bundle_json, bundle_hash=canonical_hash(normalized),
            lineage_status="COMPLETE" if complete else "INCOMPLETE_LEGACY", **lineage,
        )

    @staticmethod
    def require_change_set_ready(bundle: ConfigurationBundle) -> None:
        if bundle.lineage_status != "COMPLETE":
            raise fail(AIErrorCode.CONFIGURATION_BUNDLE_INCOMPLETE, "A complete immutable configuration bundle is required")
