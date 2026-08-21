from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol


@dataclass(frozen=True)
class ProviderResponse:
    output: dict[str, Any]
    tokens_input: int
    tokens_output: int
    raw_response_ref: str | None = None
    stop_reason: str | None = None
    terminal_error_code: str | None = None
    schema_error_path: tuple[str, ...] = ()
    schema_validator: str | None = None
    repair_attempts: int = 0


class ProviderAdapter(Protocol):
    async def execute(self, *, provider: str, model: str, system_prompt: str, user_prompt: str,
                      tools: list[dict], api_key: str, request_id: str,
                      max_output_tokens: int,
                      output_schema: dict[str, Any] | None = None) -> ProviderResponse: ...


class ProviderAdapterRegistry:
    def __init__(self):
        self._adapters: dict[str, ProviderAdapter] = {}

    def register(self, provider: str, adapter: ProviderAdapter) -> None:
        self._adapters[provider] = adapter

    def get(self, provider: str) -> ProviderAdapter:
        try:
            return self._adapters[provider]
        except KeyError as exc:
            raise LookupError(f"provider adapter not registered: {provider}") from exc


KeyResolver = Callable[[str], Awaitable[str]]
