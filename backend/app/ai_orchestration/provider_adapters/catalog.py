"""Authenticated, zero-generation provider catalog transports."""

from __future__ import annotations

import httpx


class ProviderCatalogError(RuntimeError):
    def __init__(self, code: str, status_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class AnthropicCatalogAdapter:
    async def list_model_ids(self, *, api_key: str, timeout_seconds: float = 15.0) -> tuple[str, ...]:
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "accept": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise ProviderCatalogError("PROVIDER_CATALOG_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise ProviderCatalogError("PROVIDER_CATALOG_CONNECTION_ERROR") from exc
        if response.status_code != 200:
            raise ProviderCatalogError("PROVIDER_CATALOG_HTTP_ERROR", response.status_code)
        try:
            model_ids = tuple(sorted({
                str(item["id"])
                for item in response.json().get("data", [])
                if item.get("id")
            }))
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID") from exc
        if not model_ids:
            raise ProviderCatalogError("PROVIDER_CATALOG_EMPTY")
        return model_ids
