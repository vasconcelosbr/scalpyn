"""Unit tests for the Task #275 rewrap helpers in app.utils.encryption.

Covers the bits the /api/admin/encryption/rewrap endpoint depends on
without spinning up the full FastAPI app + Postgres:

- ``current_key_id`` / ``all_key_ids`` are stable, non-secret hashes.
- ``try_decrypt`` returns the id of the key that succeeded so the caller
  can bucket rows as "current" vs "legacy".
- ``try_decrypt`` never raises — bad blobs return ``ok=False`` with a
  short reason instead.
"""
import importlib

from cryptography.fernet import Fernet


def _reload_with_key(monkeypatch, key_value: str):
    from app import config as config_module
    monkeypatch.setattr(config_module.settings, "ENCRYPTION_KEY", key_value, raising=False)
    import app.utils.encryption as encryption
    importlib.reload(encryption)
    return encryption


def test_current_key_id_is_stable_and_short(monkeypatch):
    enc = _reload_with_key(monkeypatch, "alpha-key")
    kid = enc.current_key_id()
    assert isinstance(kid, str) and len(kid) == 12
    # Reloading with the same key returns the same id.
    enc2 = _reload_with_key(monkeypatch, "alpha-key")
    assert enc2.current_key_id() == kid


def test_all_key_ids_preserves_order_and_distinguishes(monkeypatch):
    enc = _reload_with_key(monkeypatch, "alpha-key, beta-key")
    ids = enc.all_key_ids()
    assert len(ids) == 2
    assert ids[0] == enc.current_key_id()
    assert ids[0] != ids[1]


def test_try_decrypt_reports_legacy_key(monkeypatch):
    old = _reload_with_key(monkeypatch, "old-key")
    legacy_id = old.current_key_id()
    legacy_token = old.encrypt("payload-A")

    rotated = _reload_with_key(monkeypatch, "new-key, old-key")
    cur_id = rotated.current_key_id()
    assert cur_id != legacy_id

    ok, used_id, err = rotated.try_decrypt(legacy_token)
    assert ok is True
    assert err is None
    assert used_id == legacy_id  # decrypted under the secondary key

    # A freshly-encrypted token reports the current key id.
    fresh_token = rotated.encrypt("payload-B")
    ok2, used2, _ = rotated.try_decrypt(fresh_token)
    assert ok2 is True and used2 == cur_id


def test_try_decrypt_never_raises_on_garbage(monkeypatch):
    enc = _reload_with_key(monkeypatch, "any-key")
    ok, used, err = enc.try_decrypt(b"not-a-fernet-token")
    assert ok is False and used is None
    assert err == "InvalidToken"

    ok2, used2, err2 = enc.try_decrypt(b"")
    assert ok2 is False and used2 is None
    assert err2 == "empty"


def test_try_decrypt_handles_memoryview(monkeypatch):
    enc = _reload_with_key(monkeypatch, "mv-key")
    token = enc.encrypt("hello")
    ok, used, err = enc.try_decrypt(memoryview(token))
    assert ok is True and err is None
    assert used == enc.current_key_id()


def test_try_decrypt_unknown_key_marked_invalid(monkeypatch):
    # Encrypted under a key that is NOT in the rotation at all.
    foreign = Fernet.generate_key()
    foreign_token = Fernet(foreign).encrypt(b"secret")

    enc = _reload_with_key(monkeypatch, "rotation-key-1, rotation-key-2")
    ok, used, err = enc.try_decrypt(foreign_token)
    assert ok is False and used is None
    assert err == "InvalidToken"
