import os

from .http_adapter import HTTPProviderAdapter
from .anthropic_sdk import AnthropicSDKTextAdapter
from .catalog import AnthropicCatalogAdapter, ProviderCatalogError
from .copilot_transport import CopilotProviderTransport
from ..runtime import ProviderAdapterRegistry


def default_adapter_registry() -> ProviderAdapterRegistry:
    registry = ProviderAdapterRegistry()
    adapter = HTTPProviderAdapter(
        timeout_seconds=max(1.0, float(os.getenv("AI_PROVIDER_TIMEOUT_SECONDS", "180"))),
        max_attempts=max(1, int(os.getenv("AI_PROVIDER_MAX_ATTEMPTS", "3"))),
    )
    for provider in ("anthropic", "openai", "gemini"):
        registry.register(provider, adapter)
    return registry

__all__ = [
    "AnthropicCatalogAdapter", "AnthropicSDKTextAdapter", "CopilotProviderTransport",
    "HTTPProviderAdapter", "ProviderCatalogError",
    "default_adapter_registry",
]
