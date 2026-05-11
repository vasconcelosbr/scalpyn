import importlib

import pytest
from cryptography.fernet import InvalidToken


def _reload_with_key(monkeypatch, key_value: str):
    from app import config as config_module
    monkeypatch.setattr(config_module.settings, "ENCRYPTION_KEY", key_value, raising=False)
    import app.utils.encryption as encryption
    importlib.reload(encryption)
    return encryption


def test_round_trip_single_key(monkeypatch):
    enc = _reload_with_key(monkeypatch, "single-key-for-tests")
    token = enc.encrypt("hello world")
    assert isinstance(token, bytes)
    assert enc.decrypt(token) == "hello world"


def test_decrypt_old_token_after_rotation(monkeypatch):
    old = _reload_with_key(monkeypatch, "old-key-original")
    legacy_token = old.encrypt("super-secret-api-key")

    rotated = _reload_with_key(monkeypatch, "new-key-primary, old-key-original")
    assert rotated.decrypt(legacy_token) == "super-secret-api-key"

    new_token = rotated.encrypt("fresh-payload")
    assert rotated.decrypt(new_token) == "fresh-payload"


def test_old_key_alone_fails_to_decrypt_new_token(monkeypatch):
    new = _reload_with_key(monkeypatch, "new-key-primary")
    new_token = new.encrypt("payload")

    old_only = _reload_with_key(monkeypatch, "old-key-original")
    with pytest.raises(InvalidToken):
        old_only.decrypt(new_token)


def test_empty_key_raises(monkeypatch):
    enc = _reload_with_key(monkeypatch, "   ,  ,")
    with pytest.raises(ValueError):
        enc.encrypt("anything")


def test_memoryview_input_supported(monkeypatch):
    enc = _reload_with_key(monkeypatch, "mv-key")
    token = enc.encrypt("from-bytea")
    assert enc.decrypt(memoryview(token)) == "from-bytea"


def test_csv_whitespace_is_stripped(monkeypatch):
    a = _reload_with_key(monkeypatch, "  key-a  ")
    token = a.encrypt("x")
    b = _reload_with_key(monkeypatch, "  key-b , key-a  ")
    assert b.decrypt(token) == "x"


def test_empty_input_short_circuits(monkeypatch):
    enc = _reload_with_key(monkeypatch, "anything")
    assert enc.encrypt("") == b""
    assert enc.decrypt(b"") == ""
    assert enc.decrypt(None) == ""
