"""Shared helper for extracting decrypted exchange credentials from a row.

Historically every callsite (``/api/exchanges/{id}/test``,
``/api/live/balance``, future spot/futures execution paths) inlined the
same three-step ritual: coerce ``bytea`` → ``bytes`` (Postgres returns
``memoryview`` under asyncpg), call :func:`decrypt`, strip surrounding
whitespace introduced by some manual SQL inserts. Drift between the
copies has caused subtle bugs when one was updated and another wasn't
(e.g. one place fixing a ``memoryview`` regression while another
silently swallowed it as ``InvalidToken``).

Centralizing here means the diagnostic ``/test`` endpoint and the
production ``/balance`` endpoint exercise the **exact same code path**
on the credential side — ruling out adapter/decrypt drift as a cause
when one works and the other doesn't.
"""

from __future__ import annotations

from typing import Tuple

from ..models.exchange_connection import ExchangeConnection
from ..utils.encryption import decrypt


def decrypt_credentials(conn: ExchangeConnection) -> Tuple[str, str]:
    """Return ``(api_key, api_secret)`` decrypted from an already-fetched row.

    Caller is responsible for the SELECT and any ``user_id`` /
    ``is_active`` filtering — this helper only owns the bytes-coercion
    + Fernet decrypt + strip pipeline so callers don't reimplement it.

    Raises whatever :func:`decrypt` raises — typically
    :class:`cryptography.fernet.InvalidToken` when the row was
    encrypted under a key that's no longer in the ``ENCRYPTION_KEY``
    rotation. Callers should handle that case explicitly (the
    diagnostic endpoints log the key-id mismatch via ``try_decrypt``).
    """
    raw_key = conn.api_key_encrypted
    raw_secret = conn.api_secret_encrypted
    if isinstance(raw_key, memoryview):
        raw_key = bytes(raw_key)
    if isinstance(raw_secret, memoryview):
        raw_secret = bytes(raw_secret)
    return decrypt(raw_key).strip(), decrypt(raw_secret).strip()


__all__ = ["decrypt_credentials"]
