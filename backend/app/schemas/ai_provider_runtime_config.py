from pydantic import BaseModel, ConfigDict, Field, model_validator


class AIProviderRuntimeConfig(BaseModel):
    """Tenant-governed operational gate; canary authority is intentionally absent."""

    model_config = ConfigDict(extra="forbid")
    normal_analysis_provider_enabled: bool = False
    shadow_full_canonical_capture_enabled: bool = False
    shadow_full_canonical_provider_enabled: bool = False
    shadow_shard_max_output_tokens: int = Field(default=0, ge=0)
    shadow_synthesis_max_output_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def canonical_limits_required_when_enabled(self):
        if self.shadow_full_canonical_provider_enabled and not self.shadow_full_canonical_capture_enabled:
            raise ValueError("canonical Shadow provider requires canonical capture")
        if self.shadow_full_canonical_provider_enabled and (
            self.shadow_shard_max_output_tokens <= 0
            or self.shadow_synthesis_max_output_tokens <= 0
        ):
            raise ValueError("canonical Shadow provider output limits are required when enabled")
        return self
