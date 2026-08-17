from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from typing import Any

import httpx
from jsonschema import (
    FormatChecker,
    ValidationError as JSONSchemaValidationError,
    validate as validate_json_schema,
)

from ..errors import AIError, AIErrorCode, AIOrchestrationError
from ..reliability import classify_provider_status, retry_delays
from ..runtime import ProviderResponse


_ANTHROPIC_UNSUPPORTED_SCHEMA_CONSTRAINTS = {
    "maximum", "maxItems", "maxLength", "maxProperties",
    "minimum", "minLength",
}

# Content-level failures the model can plausibly self-correct on a second
# attempt, given the exact validation error. Truncation is deliberately
# excluded: retrying with the same max_output_tokens would not fix it.
_CONTENT_REPAIR_ERROR_CODES = frozenset({
    "PROVIDER_OUTPUT_JSON_INVALID", "PROVIDER_OUTPUT_SCHEMA_INVALID",
})


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
        enum_types: list[str] = []
        for value in enum:
            value_type = _enum_type([value])
            if value_type not in enum_types:
                enum_types.append(value_type)
        if len(enum_types) == 1:
            normalized["type"] = enum_types[0]
        else:
            # Mixed enums (for example ["score", null]) need an explicit
            # union. Inferring the type from only the first value leaves an
            # internally inconsistent schema that Anthropic rejects with 400.
            normalized["anyOf"] = [{"type": item} for item in enum_types]
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


def _raw_text(provider: str, payload: dict) -> str:
    if provider == "openai":
        return payload["choices"][0]["message"]["content"]
    if provider == "anthropic":
        return "\n".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )
    return payload["candidates"][0]["content"]["parts"][0]["text"]


def _correction_instruction(terminal_error_code: str, output_schema: dict[str, Any] | None) -> str:
    schema_text = (
        json.dumps(output_schema, ensure_ascii=False) if output_schema else "the required schema"
    )
    if terminal_error_code == "PROVIDER_OUTPUT_JSON_INVALID":
        problem = "was not valid JSON"
    else:
        problem = "was valid JSON but did not satisfy the required schema"
    return (
        f"Your previous response {problem}. Return ONLY a corrected JSON object matching "
        f"this schema, with no markdown fences and no explanation: {schema_text}"
    )


class HTTPProviderAdapter:
    """One audited transport for Anthropic, OpenAI and Gemini JSON calls."""

    def __init__(
        self, *, timeout_seconds: float = 180.0, max_attempts: int = 3,
        content_repair_attempts: int = 1,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.content_repair_attempts = content_repair_attempts

    async def execute(self, *, provider: str, model: str, system_prompt: str, user_prompt: str,
                      tools: list[dict], api_key: str, request_id: str,
                      max_output_tokens: int,
                      output_schema: dict[str, Any] | None = None) -> ProviderResponse:
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds, connect=20.0)) as client:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    response = await self._post(
                        client, provider, model, system_prompt, user_prompt, api_key,
                        request_id, max_output_tokens, output_schema,
                    )
                except httpx.TransportError as exc:
                    # Connection/timeout failures never reach a response object,
                    # so the status-code retry branch below never runs for them.
                    # Observed in production as PROVIDER_TRANSPORT_FAILED with
                    # 0 tokens billed -- the request never completed transport.
                    if attempt >= self.max_attempts:
                        raise AIOrchestrationError(AIError(
                            code=AIErrorCode.PROVIDER_TIMEOUT, retryable=True, http_status=0,
                            operator_action="Review provider network reachability and timeout budget",
                            safe_message="AI provider request failed", provider_error_code=type(exc).__name__,
                            internal_detail_redacted=f"provider={provider}; transport_error={type(exc).__name__}",
                            attempt=attempt,
                        )) from exc
                    await asyncio.sleep(1.0 * (2 ** attempt))
                    continue
                if response.is_success:
                    return await self._decode_with_repair(
                        client, provider, model, system_prompt, user_prompt, api_key,
                        request_id, max_output_tokens, output_schema, response,
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
        max_output_tokens, output_schema=None, prior_attempt=None,
    ):
        headers = {"x-scalpyn-ai-request-id": request_id}
        if provider == "openai":
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            if prior_attempt is not None:
                messages.append({"role": "assistant", "content": prior_attempt["raw_text"]})
                messages.append({"role": "user", "content": prior_attempt["correction"]})
            return await client.post("https://api.openai.com/v1/chat/completions",
                headers={**headers, "Authorization": f"Bearer {api_key}"},
                json={"model": model, "temperature": 0, "max_tokens": max_output_tokens,
                      "response_format": {"type": "json_object"},
                      "messages": messages})
        if provider == "anthropic":
            messages = [{"role": "user", "content": user}]
            if prior_attempt is not None:
                messages.append({"role": "assistant", "content": prior_attempt["raw_text"]})
                messages.append({"role": "user", "content": prior_attempt["correction"]})
            payload = {
                "model": model, "max_tokens": max_output_tokens,
                "system": system, "messages": messages,
            }
            if output_schema is not None:
                payload["output_config"] = anthropic_output_config(output_schema)
            return await client.post("https://api.anthropic.com/v1/messages",
                headers={**headers, "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json=payload)
        if provider == "gemini":
            contents = [{"role": "user", "parts": [{"text": user}]}]
            if prior_attempt is not None:
                contents.append({"role": "model", "parts": [{"text": prior_attempt["raw_text"]}]})
                contents.append({"role": "user", "parts": [{"text": prior_attempt["correction"]}]})
            return await client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": api_key}, headers=headers,
                json={"systemInstruction": {"parts": [{"text": system}]},
                      "generationConfig": {"temperature": 0, "maxOutputTokens": max_output_tokens,
                                           "responseMimeType": "application/json"},
                      "contents": contents})
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
        output_schema: dict[str, Any] | None = None,
    ) -> ProviderResponse:
        text = _raw_text(provider, payload)
        if provider == "openai":
            usage = payload.get("usage") or {}
            tokens_in = usage.get("prompt_tokens", 0); tokens_out = usage.get("completion_tokens", 0)
            stop_reason = payload["choices"][0].get("finish_reason")
        elif provider == "anthropic":
            usage = payload.get("usage") or {}; tokens_in = usage.get("input_tokens", 0); tokens_out = usage.get("output_tokens", 0)
            stop_reason = payload.get("stop_reason")
        else:
            usage = payload.get("usageMetadata") or {}
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
        elif terminal_error_code is None and output_schema is not None:
            try:
                validate_json_schema(output, output_schema, format_checker=FormatChecker())
            except JSONSchemaValidationError:
                terminal_error_code = "PROVIDER_OUTPUT_SCHEMA_INVALID"

        return ProviderResponse(
            output=output,
            tokens_input=int(tokens_in or 0),
            tokens_output=int(tokens_out or 0),
            raw_response_ref=raw_response_ref,
            stop_reason=stop_reason,
            terminal_error_code=terminal_error_code,
        )

    async def _decode_with_repair(
        self, client, provider, model, system_prompt, user_prompt, api_key,
        request_id, max_output_tokens, output_schema, http_response,
    ) -> ProviderResponse:
        """Decode a successful HTTP response, giving the model one bounded
        chance to self-correct when the content fails JSON/schema validation.
        The original call already happened and is billable; every repair
        attempt's usage is accumulated into the returned totals so callers
        never under-report actual token/cost consumption (FIX: Cenario C,
        DIAG-AC-001 -- ProviderOutputError with no recovery path)."""
        raw_response_ref = (
            http_response.headers.get("request-id")
            or http_response.headers.get("x-request-id")
        )
        raw_payload = http_response.json()
        response = self._decode(
            provider, raw_payload, raw_response_ref=raw_response_ref, output_schema=output_schema,
        )
        total_tokens_input = response.tokens_input
        total_tokens_output = response.tokens_output
        prior_raw_text = _raw_text(provider, raw_payload)
        repair_attempts_left = self.content_repair_attempts

        while response.terminal_error_code in _CONTENT_REPAIR_ERROR_CODES and repair_attempts_left > 0:
            repair_attempts_left -= 1
            repair_http_response = await self._post(
                client, provider, model, system_prompt, user_prompt, api_key, request_id,
                max_output_tokens, output_schema,
                prior_attempt={
                    "raw_text": prior_raw_text,
                    "correction": _correction_instruction(response.terminal_error_code, output_schema),
                },
            )
            if not repair_http_response.is_success:
                # The original attempt already succeeded at the transport
                # level; surface its content error rather than a transport
                # failure from the repair call.
                break
            repair_payload = repair_http_response.json()
            response = self._decode(
                provider, repair_payload, raw_response_ref=raw_response_ref, output_schema=output_schema,
            )
            total_tokens_input += response.tokens_input
            total_tokens_output += response.tokens_output
            prior_raw_text = _raw_text(provider, repair_payload)

        return replace(response, tokens_input=total_tokens_input, tokens_output=total_tokens_output)
