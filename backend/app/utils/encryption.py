import base64
import hashlib
from typing import List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import settings


def _normalize_key(raw: str) -> bytes:
    raw_key = raw.encode('utf-8')
    if len(raw_key) < 32:
        raw_key = raw_key.ljust(32, b'0')
    elif len(raw_key) > 32:
        raw_key = raw_key[:32]
    return base64.urlsafe_b64encode(raw_key)


def _parse_keys() -> List[str]:
    raw = settings.ENCRYPTION_KEY or ""
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise ValueError(
            "ENCRYPTION_KEY is empty. Set it to one or more comma-separated keys "
            "(first key is used to encrypt; all are tried for decrypt)."
        )
    return keys


def _key_id(raw_key: str) -> str:
    """Stable, non-secret identifier for a key (first 12 hex of sha256).

    Lets ops/health endpoints distinguish "current" vs "legacy" keys without
    exposing the secret material itself.
    """
    digest = hashlib.sha256(_normalize_key(raw_key)).hexdigest()
    return digest[:12]


def get_fernet() -> MultiFernet:
    keys = _parse_keys()
    return MultiFernet([Fernet(_normalize_key(k)) for k in keys])


def encrypt(data: str) -> bytes:
    if not data:
        return b""
    return get_fernet().encrypt(data.encode('utf-8'))


def _coerce_token(token) -> bytes:
    if isinstance(token, memoryview):
        return bytes(token)
    if isinstance(token, str):
        return token.encode('utf-8')
    return token


def decrypt(token) -> str:
    if not token:
        return ""
    return get_fernet().decrypt(_coerce_token(token)).decode('utf-8')


def current_key_id() -> Optional[str]:
    """Hash-id of the key currently used to encrypt new payloads.

    Returns None when ENCRYPTION_KEY is unset/empty (does not raise — used
    by health probes that must always return a payload).
    """
    try:
        keys = _parse_keys()
    except ValueError:
        return None
    return _key_id(keys[0])


def all_key_ids() -> List[str]:
    """Hash-ids of every key in the rotation, in priority order (current first)."""
    try:
        keys = _parse_keys()
    except ValueError:
        return []
    return [_key_id(k) for k in keys]


def try_decrypt(token) -> Tuple[bool, Optional[str], Optional[str]]:
    """Attempt to decrypt with the configured MultiFernet rotation.

    Returns ``(ok, key_id_used, error)``:

    * ``ok=True``  → ``key_id_used`` is the hash-id of the key that succeeded
      (matches an entry from :func:`all_key_ids`). Useful to detect rows
      still on a legacy key vs the current one.
    * ``ok=False`` → ``error`` is a short, non-secret reason string
      (``"InvalidToken"``, ``"empty"``, etc.).

    Never raises — callers (health probes, batch rewrap) need to count
    failures rather than abort on the first bad row.
    """
    if not token:
        return False, None, "empty"
    raw = _coerce_token(token)
    try:
        keys = _parse_keys()
    except ValueError as exc:
        return False, None, f"config: {exc}"
    for raw_key in keys:
        try:
            Fernet(_normalize_key(raw_key)).decrypt(raw)
            return True, _key_id(raw_key), None
        except InvalidToken:
            continue
        except Exception as exc:  # pragma: no cover - defensive
            return False, None, f"{type(exc).__name__}: {exc}"
    return False, None, "InvalidToken"


__all__ = [
    "InvalidToken",
    "encrypt",
    "decrypt",
    "get_fernet",
    "current_key_id",
    "all_key_ids",
    "try_decrypt",
]
