from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import ai_keys_service


class _CatalogResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _CatalogClient:
    response = _CatalogResponse(200, {"data": [{"id": "catalog-model-id"}]})
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, **kwargs):
        self.__class__.calls.append((url, kwargs))
        return self.__class__.response


@pytest.mark.asyncio
async def test_anthropic_validation_uses_zero_generation_catalog(monkeypatch):
    record = SimpleNamespace(api_key_encrypted=b"encrypted")
    saved = []

    async def get_record(*_args):
        return record

    async def save_result(_db, _record, success, message):
        saved.append((success, message))

    _CatalogClient.calls = []
    _CatalogClient.response = _CatalogResponse(200, {"data": [{"id": "catalog-model-id"}]})
    monkeypatch.setattr(ai_keys_service, "_get_record", get_record)
    monkeypatch.setattr(ai_keys_service, "_save_test_result", save_result)
    monkeypatch.setattr(ai_keys_service, "decrypt_value", lambda _value: "secret-provider-key")
    monkeypatch.setattr("httpx.AsyncClient", _CatalogClient)

    success, message = await ai_keys_service.test_anthropic_key(object(), uuid4())

    assert success is True
    assert "sem geração" in message
    assert saved == [(True, "")]
    assert len(_CatalogClient.calls) == 1
    url, kwargs = _CatalogClient.calls[0]
    assert url == "https://api.anthropic.com/v1/models"
    assert kwargs["headers"]["x-api-key"] == "secret-provider-key"


@pytest.mark.asyncio
async def test_anthropic_catalog_rejection_marks_key_invalid(monkeypatch):
    record = SimpleNamespace(api_key_encrypted=b"encrypted")
    saved = []

    async def get_record(*_args):
        return record

    async def save_result(_db, _record, success, message):
        saved.append((success, message))

    _CatalogClient.calls = []
    _CatalogClient.response = _CatalogResponse(401, {})
    monkeypatch.setattr(ai_keys_service, "_get_record", get_record)
    monkeypatch.setattr(ai_keys_service, "_save_test_result", save_result)
    monkeypatch.setattr(ai_keys_service, "decrypt_value", lambda _value: "secret-provider-key")
    monkeypatch.setattr("httpx.AsyncClient", _CatalogClient)

    success, message = await ai_keys_service.test_anthropic_key(object(), uuid4())

    assert success is False
    assert "inválida" in message
    assert saved == [(False, message)]
