from pydantic import BaseModel, ConfigDict


class AIProviderRuntimeConfig(BaseModel):
    """Tenant-governed operational gate; canary authority is intentionally absent."""

    model_config = ConfigDict(extra="forbid")
    normal_analysis_provider_enabled: bool = False
