from .http_adapter import HTTPProviderAdapter
from .anthropic_sdk import AnthropicSDKTextAdapter
from .catalog import AnthropicCatalogAdapter, ProviderCatalogError
from .copilot_transport import CopilotProviderTransport
from ..runtime import ProviderAdapterRegistry


def default_adapter_registry() -> ProviderAdapterRegistry:
    registry = ProviderAdapterRegistry()
    adapter = HTTPProviderAdapter()
    for provider in ("anthropic", "openai", "gemini"):
        registry.register(provider, adapter)
    return registry

__all__ = [
    "AnthropicCatalogAdapter", "AnthropicSDKTextAdapter", "CopilotProviderTransport",
    "HTTPProviderAdapter", "ProviderCatalogError",
    "default_adapter_registry",
]
