from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ..errors import AIError, AIErrorCode, AIOrchestrationError
from ..reliability import classify_provider_status, retry_delays
from ..runtime import ProviderResponse


_ANTHROPIC_UNSUPPORTED_SCHEMA_CONSTRAINTS = {
    "maximum", "maxItems", "maxLength", "maxProperties",
    "minimum", "minLength",
}


def _constraint_description(schema: dict[str, Any]) -> str | None:
    constraints = [
        f"{key}={schema[key]}"
        for key in sorted(_ANTHROPIC_UNSUPPORTED_SCHEMA_CONSTRAINTS)
        if key in schema
    ]
    if not constraints:
        return None
    return "Advisory constraints: " + "; ".join(constraints) + "."


def _enum_type(values: list[Any]) -> str:
    sample = values[0]
    if isinstance(sample, bool):
        return "boolean"
    if isinstance(sample, str):
        return "string"
    if isinstance(sample, int):
        return "integer"
    if isinstance(sample, float):
        return "number"
    if sample is None:
        return "null"
    raise ValueError("unsupported enum type for Anthropic structured output")


def prepare_anthropic_output_schema(
    schema: dict[str, Any],
    *,
    required: bool = True,
) -> dict[str, Any] | None:
    """Build the strict subset accepted by Anthropic structured outputs."""
    if not schema:
        if required:
            raise ValueError("required output field cannot use an unconstrained schema")
        return None

    normalized: dict[str, Any] = {}
    required_properties = set(schema.get("required") or ())
    for key, value in schema.items():
        if key in _ANTHROPIC_UNSUPPORTED_SCHEMA_CONSTRAINTS or key == "additionalProperties":
            continue
        if key == "properties":
            properties: dict[str, Any] = {}
            for name, property_schema in value.items():
                prepared = prepare_anthropic_output_schema(
                    property_schema,
                    required=name in required_properties,
                )
                if prepared is not None:
                    properties[name] = prepared
            normalized[key] = properties
            continue
        if key == "items":
            normalized[key] = prepare_anthropic_output_schema(value, required=True)
            continue
        if key in {"anyOf", "oneOf", "allOf"}:
            normalized[key] = [
                prepare_anthropic_output_schema(option, required=True)
                for option in value
            ]
            continue
        if key == "type" and isinstance(value, list):
            normalized["anyOf"] = [{"type": item} for item in value]
            continue
        normalized[key] = value

    advisory = _constraint_description(schema)
    if advisory:
        existing = str(normalized.get("description") or "").strip()
        normalized["description"] = f"{existing} {advisory}".strip()

    if not any(key in normalized for key in ("type", "anyOf", "oneOf", "allOf")):
        enum = normalized.get("enum")
        if not isinstance(enum, list) or not enum:
            raise ValueError("structured output schema requires a concrete type")
        normalized["type"] = _enum_type(enum)
    if normalized.get("type") == "object":
        normalized["additionalProperties"] = False
    return normalized


def anthropic_output_config(schema: dict[str, Any]) -> dict[str, Any]:
    prepared = prepare_anthropic_output_schema(schema)
    if prepared is None:
        raise ValueError("Anthropic structured output schema is empty")
    return {"format": {"type": "json_schema", "schema": prepared}}


def _json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        cleaned = cleaned[:-3].strip() if cleaned.endswith("```") else cleaned
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("provider output must be a JSON object")
    return parsed


class HTTPProviderAdapter:
    """One audited transport for Anthropic, OpenAI and Gemini JSON calls."""

    def __init__(self, *, timeout_seconds: float = 180.0, max_attempts: int = 3):
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts

    async def execute(self, *, provider: str, model: str, system_prompt: str, user_prompt: str,
                      tools: list[dict], api_key: str, request_id: str,
                      max_output_tokens: int,
                      output_schema: dict[str, Any] | None = None) -> ProviderResponse:
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds, connect=20.0)) as client:
            for attempt in range(1, self.max_attempts + 1):
                response = await self._post(
                    client, provider, model, system_prompt, user_prompt, api_key,
                    request_id, max_output_tokens, output_schema,
                )
                if response.is_success:
                    return self._decode(
                        provider,
                        response.json(),
                        raw_response_ref=(
                            response.headers.get("request-id")
                            or response.headers.get("x-request-id")
                        ),
                    )
                retry_after = int(response.headers.get("retry-after", "0") or 0) or None
                policy = classify_provider_status(response.status_code, retry_after_seconds=retry_after)
                delays = retry_delays(policy, max_attempts=self.max_attempts)
                if not policy.retryable or attempt >= self.max_attempts:
                    raise AIOrchestrationError(AIError(
                        code=policy.code, retryable=policy.retryable, http_status=response.status_code,
                        operator_action="Review provider configuration, budget, and audit telemetry",
                        safe_message="AI provider request failed", provider_error_code=str(response.status_code),
                        internal_detail_redacted=f"provider={provider}; status={response.status_code}", attempt=attempt,
                    ))
                await asyncio.sleep(delays[attempt - 1])
        raise AssertionError("unreachable")

    async def _post(
        self, client, provider, model, system, user, api_key, request_id,
        max_output_tokens, output_schema=None,
    ):
        headers = {"x-scalpyn-ai-request-id": request_id}
        if provider == "openai":
            return await client.post("https://api.openai.com/v1/chat/completions",
                headers={**headers, "Authorization": f"Bearer {api_key}"},
                json={"model": model, "temperature": 0, "max_tokens": max_output_tokens,
                      "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        if provider == "anthropic":
            payload = {
                "model": model, "max_tokens": max_output_tokens, "temperature": 0,
                "system": system, "messages": [{"role": "user", "content": user}],
            }
            if output_schema is not None:
                payload["output_config"] = anthropic_output_config(output_schema)
            return await client.post("https://api.anthropic.com/v1/messages",
                headers={**headers, "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json=payload)
        if provider == "gemini":
            return await client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": api_key}, headers=headers,
                json={"systemInstruction": {"parts": [{"text": system}]},
                      "generationConfig": {"temperature": 0, "maxOutputTokens": max_output_tokens,
                                           "responseMimeType": "application/json"},
                      "contents": [{"role": "user", "parts": [{"text": user}]}]})
        raise AIOrchestrationError(AIError(
            code=AIErrorCode.PROVIDER_NOT_CONFIGURED, retryable=False, http_status=422,
            operator_action="Configure an allowed provider", safe_message="Unsupported AI provider",
        ))

    @staticmethod
    def _decode(
        provider: str,
        payload: dict,
        *,
        raw_response_ref: str | None = None,
    ) -> ProviderResponse:
        if provider == "openai":
            text = payload["choices"][0]["message"]["content"]; usage = payload.get("usage") or {}
            tokens_in = usage.get("prompt_tokens", 0); tokens_out = usage.get("completion_tokens", 0)
            stop_reason = payload["choices"][0].get("finish_reason")
        elif provider == "anthropic":
            text = "\n".join(block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text")
            usage = payload.get("usage") or {}; tokens_in = usage.get("input_tokens", 0); tokens_out = usage.get("output_tokens", 0)
            stop_reason = payload.get("stop_reason")
        else:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]; usage = payload.get("usageMetadata") or {}
            tokens_in = usage.get("promptTokenCount", 0); tokens_out = usage.get("candidatesTokenCount", 0)
            stop_reason = payload["candidates"][0].get("finishReason")

        terminal_error_code = None
        try:
            output = _json_object(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            # The provider call has already happened and must remain auditable.
            # Return usage metadata without retaining or logging the raw output.
            output = {}
            terminal_error_code = "PROVIDER_OUTPUT_JSON_INVALID"

        if stop_reason in {"max_tokens", "length", "MAX_TOKENS"}:
            terminal_error_code = "PROVIDER_OUTPUT_TRUNCATED"

        return ProviderResponse(
            output=output,
            tokens_input=int(tokens_in or 0),
            tokens_output=int(tokens_out or 0),
            raw_response_ref=raw_response_ref,
            stop_reason=stop_reason,
            terminal_error_code=terminal_error_code,
        )
