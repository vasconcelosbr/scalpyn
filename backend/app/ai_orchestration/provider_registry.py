from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .errors import AIErrorCode, fail
from .hashing import canonical_hash


class ModelCatalogEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    model_id: str
    capabilities: frozenset[str]
    max_input: int
    max_output: int
    allowed: bool = True


class ModelAlias(BaseModel):
    model_config = ConfigDict(frozen=True)
    alias: str
    provider: str
    real_model_id: str
    status: str = "ACTIVE"
    valid_from: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_to: datetime | None = None
    capabilities: frozenset[str] = Field(default_factory=frozenset)


class ModelResolution(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID = Field(default_factory=uuid4)
    requested_provider: str | None
    requested_model: str | None
    configured_provider: str | None
    configured_model: str | None
    effective_provider: str
    effective_model: str
    catalog_snapshot_hash: str
    capabilities: frozenset[str]
    resolution_policy_version: str = "scalpyn-model-resolution-v1"
    resolution_reason: str
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProviderModelRegistry:
    def __init__(self, entries: Iterable[ModelCatalogEntry], aliases: Iterable[ModelAlias] = ()):
        self._entries = {(e.provider, e.model_id): e for e in entries}
        self._aliases = {(a.provider, a.alias): a for a in aliases}
        self.catalog_snapshot_hash = canonical_hash([
            entry.model_dump(mode="json") for entry in sorted(self._entries.values(), key=lambda x: (x.provider, x.model_id))
        ])

    def resolve(self, *, requested_provider: str | None, requested_model: str | None,
                configured_provider: str | None, configured_model: str | None,
                tenant_default: tuple[str, str] | None = None,
                global_default: tuple[str, str] | None = None,
                allow_request_override: bool = False,
                required_capabilities: set[str] | frozenset[str] = frozenset()) -> ModelResolution:
        if allow_request_override and requested_provider and requested_model:
            provider, model, reason = requested_provider, requested_model, "request_override"
        elif configured_provider and configured_model:
            provider, model, reason = configured_provider, configured_model, "module_configuration"
        elif tenant_default:
            provider, model, reason = *tenant_default, "tenant_default"
        elif global_default:
            provider, model, reason = *global_default, "authorized_global_default"
        else:
            raise fail(AIErrorCode.PROVIDER_NOT_CONFIGURED, "No provider/model configuration is available")

        alias = self._aliases.get((provider, model))
        now = datetime.now(timezone.utc)
        if alias and alias.status == "ACTIVE" and alias.valid_from <= now and (alias.valid_to is None or alias.valid_to > now):
            model = alias.real_model_id
            reason += ":alias"
        entry = self._entries.get((provider, model))
        if entry is None:
            raise fail(AIErrorCode.MODEL_UNKNOWN, f"Unknown model for provider {provider}")
        if not entry.allowed:
            raise fail(AIErrorCode.MODEL_NOT_ALLOWED, "The resolved model is not allowed")
        missing = set(required_capabilities) - set(entry.capabilities)
        if missing:
            raise fail(AIErrorCode.MODEL_CAPABILITY_MISMATCH, "The resolved model lacks required capabilities")
        return ModelResolution(
            requested_provider=requested_provider,
            requested_model=requested_model,
            configured_provider=configured_provider,
            configured_model=configured_model,
            effective_provider=provider,
            effective_model=model,
            catalog_snapshot_hash=self.catalog_snapshot_hash,
            capabilities=entry.capabilities,
            resolution_reason=reason,
        )


def default_registry() -> ProviderModelRegistry:
    common = frozenset({"text", "structured_output"})
    return ProviderModelRegistry([
        ModelCatalogEntry(provider="anthropic", model_id="claude-haiku-4-5-20251001", capabilities=common | {"tool_use"}, max_input=200_000, max_output=8_192),
        ModelCatalogEntry(provider="openai", model_id="gpt-4.1-mini", capabilities=common | {"tool_use"}, max_input=128_000, max_output=32_768),
        ModelCatalogEntry(provider="gemini", model_id="gemini-2.5-flash", capabilities=common | {"tool_use"}, max_input=1_000_000, max_output=65_536),
    ])
