from unittest.mock import AsyncMock

import httpx
import pytest

from app.ai_orchestration.errors import AIOrchestrationError
from app.ai_orchestration.provider_adapters.http_adapter import HTTPProviderAdapter


_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class _FakeResponse:
    def __init__(self, payload, *, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        self.headers = {}

    def json(self):
        return self._payload


def _anthropic_payload(text, *, tokens_in=100, tokens_out=20, stop_reason="end_turn"):
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
        "stop_reason": stop_reason,
    }


def _deepseek_payload(text, *, tokens_in=100, tokens_out=20, stop_reason="stop"):
    return {
        "choices": [{
            "message": {"content": text, "reasoning_content": "reasoning"},
            "finish_reason": stop_reason,
        }],
        "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
    }


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, _url, **kwargs):
        self.calls.append(kwargs)
        item = self._responses[len(self.calls) - 1]
        if isinstance(item, BaseException):
            raise item
        return item


def _adapter(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: client)
    return HTTPProviderAdapter(), client


async def _run(adapter, output_schema=_SCHEMA):
    return await adapter.execute(
        provider="anthropic", model="claude-test", system_prompt="system",
        user_prompt="user", tools=[], api_key="key", request_id="req-1",
        max_output_tokens=1024, output_schema=output_schema,
    )


@pytest.mark.asyncio
async def test_repairs_invalid_json_on_second_attempt(monkeypatch):
    adapter, client = _adapter(monkeypatch, [
        _FakeResponse(_anthropic_payload("not json at all", tokens_in=100, tokens_out=20)),
        _FakeResponse(_anthropic_payload('{"answer": "fixed"}', tokens_in=110, tokens_out=15)),
    ])

    response = await _run(adapter)

    assert response.terminal_error_code is None
    assert response.output == {"answer": "fixed"}
    assert response.tokens_input == 210
    assert response.tokens_output == 35
    assert len(client.calls) == 2
    repair_messages = client.calls[1]["json"]["messages"]
    assert repair_messages[0] == {"role": "user", "content": "user"}
    assert repair_messages[1] == {"role": "assistant", "content": "not json at all"}
    assert "not valid JSON" in repair_messages[2]["content"]


@pytest.mark.asyncio
async def test_repairs_schema_invalid_output_on_second_attempt(monkeypatch):
    adapter, client = _adapter(monkeypatch, [
        _FakeResponse(_anthropic_payload('{"wrong_field": "oops"}', tokens_in=100, tokens_out=20)),
        _FakeResponse(_anthropic_payload('{"answer": "fixed"}', tokens_in=105, tokens_out=18)),
    ])

    response = await _run(adapter)

    assert response.terminal_error_code is None
    assert response.output == {"answer": "fixed"}
    assert response.tokens_input == 205
    assert response.tokens_output == 38
    assert len(client.calls) == 2
    repair_messages = client.calls[1]["json"]["messages"]
    assert "did not satisfy the required schema" in repair_messages[2]["content"]


@pytest.mark.asyncio
async def test_gives_up_after_exhausting_repair_attempts(monkeypatch):
    adapter, client = _adapter(monkeypatch, [
        _FakeResponse(_anthropic_payload("still not json", tokens_in=100, tokens_out=20)),
        _FakeResponse(_anthropic_payload("also not json", tokens_in=90, tokens_out=25)),
    ])

    response = await _run(adapter)

    assert response.terminal_error_code == "PROVIDER_OUTPUT_JSON_INVALID"
    assert response.tokens_input == 190
    assert response.tokens_output == 45
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_no_repair_call_when_first_attempt_is_valid(monkeypatch):
    adapter, client = _adapter(monkeypatch, [
        _FakeResponse(_anthropic_payload('{"answer": "ok"}', tokens_in=100, tokens_out=20)),
    ])

    response = await _run(adapter)

    assert response.terminal_error_code is None
    assert response.output == {"answer": "ok"}
    assert response.tokens_input == 100
    assert response.tokens_output == 20
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_truncated_output_is_not_retried(monkeypatch):
    adapter, client = _adapter(monkeypatch, [
        _FakeResponse(_anthropic_payload(
            '{"answer": "cut off', tokens_in=100, tokens_out=1024, stop_reason="max_tokens",
        )),
    ])

    response = await _run(adapter)

    assert response.terminal_error_code == "PROVIDER_OUTPUT_TRUNCATED"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_deepseek_uses_full_provider_output_and_has_no_read_timeout(monkeypatch):
    client = _FakeClient([
        _FakeResponse(_deepseek_payload('{"answer": "complete"}')),
    ])
    client_options = {}

    def client_factory(**kwargs):
        client_options.update(kwargs)
        return client

    monkeypatch.setattr("httpx.AsyncClient", client_factory)
    adapter = HTTPProviderAdapter()

    response = await adapter.execute(
        provider="deepseek", model="deepseek-v4-flash",
        system_prompt="system", user_prompt="user", tools=[], api_key="key",
        request_id="req-deepseek-full", max_output_tokens=384_000,
        output_schema=_SCHEMA,
    )

    assert response.terminal_error_code is None
    assert response.output == {"answer": "complete"}
    assert client_options["timeout"].connect == 20.0
    assert client_options["timeout"].read is None
    payload = client.calls[0]["json"]
    assert payload["max_tokens"] == 384_000
    assert payload["thinking"] == {"type": "enabled"}


@pytest.mark.asyncio
async def test_recovers_from_transport_error_on_retry(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    adapter, client = _adapter(monkeypatch, [
        httpx.ConnectTimeout("connect timed out"),
        _FakeResponse(_anthropic_payload('{"answer": "ok"}', tokens_in=100, tokens_out=20)),
    ])

    response = await _run(adapter)

    assert response.terminal_error_code is None
    assert response.output == {"answer": "ok"}
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_gives_up_after_exhausting_transport_retries(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    adapter, client = _adapter(monkeypatch, [
        httpx.ConnectTimeout("connect timed out"),
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectError("connection refused"),
    ])

    with pytest.raises(AIOrchestrationError):
        await _run(adapter)

    assert len(client.calls) == 3
