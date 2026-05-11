"""Encryption health + admin rewrap endpoints (Task #275).

Public probe
------------

``GET /api/health/encryption`` — read-only counters covering every row in
``exchange_connections``. Returns the current key-id, plus how many rows
decrypt with each known key (``by_key_id``) and how many fail outright.
No secret material is ever exposed; only short hash-ids.

Admin rewrap
------------

``POST /api/admin/encryption/rewrap`` — re-encrypts every legacy row in
``exchange_connections`` with the current (first) key in the
``ENCRYPTION_KEY`` rotation. Bearer-token gated by
``ADMIN_DIAGNOSTICS_TOKEN`` (same gate as ``/api/admin/symbol-health``).
Returns ``{scanned, rewrapped, already_current, failed, by_key_id}``.

Operational flow:
  1. Add the legacy key as a secondary entry in ``ENCRYPTION_KEY``
     (CSV, current key first).
  2. Probe ``/api/health/encryption`` to confirm the legacy rows now
     decrypt under the secondary key.
  3. POST ``/api/admin/encryption/rewrap`` to migrate them to the
     current key.
  4. Probe again, then drop the legacy key from the CSV.

See ``backend/docs/runbooks/encryption-key-rotation.md`` for the
end-to-end procedure.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy import select, update

from ..database import AsyncSessionLocal
from ..models.exchange_connection import ExchangeConnection
from ..utils.encryption import (
    all_key_ids,
    current_key_id,
    encrypt,
    try_decrypt,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Encryption"])

_BEARER_PREFIX = "Bearer "


def _expected_admin_token() -> Optional[str]:
    token = os.environ.get("ADMIN_DIAGNOSTICS_TOKEN", "").strip()
    return token or None


def _enforce_admin(authorization: Optional[str]) -> None:
    expected = _expected_admin_token()
    if expected is None:
        # Same convention as admin_diagnostics: hidden when no token configured.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    presented: Optional[str] = None
    if authorization and authorization.startswith(_BEARER_PREFIX):
        presented = authorization[len(_BEARER_PREFIX):].strip() or None
    if presented is None or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _coerce_blob(value: Any) -> Optional[bytes]:
    if value is None:
        return None
    if isinstance(value, memoryview):
        return bytes(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    return None


@router.get("/api/health/encryption")
async def encryption_health() -> Dict[str, Any]:
    """Report Fernet decryptability across all stored exchange credentials.

    Public read-only probe. Returns aggregate counters only — no secrets,
    no per-user breakdown.
    """
    cur_id = current_key_id()
    known_ids = all_key_ids()

    by_key_id: Dict[str, int] = {kid: 0 for kid in known_ids}
    scanned = 0
    decryptable = 0
    indecryptable = 0
    legacy_rows = 0  # decryptable, but NOT with the current key

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(
                    ExchangeConnection.id,
                    ExchangeConnection.api_key_encrypted,
                    ExchangeConnection.api_secret_encrypted,
                )
            )
            for _row_id, api_key_blob, api_secret_blob in result.all():
                scanned += 1
                key_blob = _coerce_blob(api_key_blob)
                secret_blob = _coerce_blob(api_secret_blob)

                ok_key, kid_key, _ = try_decrypt(key_blob) if key_blob else (False, None, "empty")
                ok_sec, kid_sec, _ = try_decrypt(secret_blob) if secret_blob else (False, None, "empty")

                if ok_key and ok_sec:
                    decryptable += 1
                    # Bucket by the api_key's key-id (typically the same key
                    # encrypted both blobs). If they diverge, prefer the
                    # OLDER one (= worst case for rotation purposes).
                    used = kid_key
                    if kid_sec and kid_key and kid_sec != kid_key:
                        # Prefer whichever is NOT the current key — that's
                        # the one that still needs migration.
                        used = kid_sec if kid_sec != cur_id else kid_key
                    if used:
                        by_key_id[used] = by_key_id.get(used, 0) + 1
                    if cur_id and used and used != cur_id:
                        legacy_rows += 1
                else:
                    indecryptable += 1
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[encryption-health] probe failed: %s", exc)
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "current_key_id": cur_id,
            "known_key_ids": known_ids,
        }

    return {
        "ok": True,
        # `total` is an alias of `scanned` kept for contract clarity
        # (matches the field name in the original task spec); both
        # always agree.
        "total": scanned,
        "scanned": scanned,
        "decryptable": decryptable,
        "indecryptable": indecryptable,
        "legacy_rows": legacy_rows,
        "current_key_id": cur_id,
        "known_key_ids": known_ids,
        "by_key_id": by_key_id,
        "rotation_complete": (
            scanned > 0 and indecryptable == 0 and legacy_rows == 0
        ),
    }


@router.post("/api/admin/encryption/rewrap", include_in_schema=False)
async def encryption_rewrap(
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Re-encrypt every ``exchange_connections`` row with the current key.

    Idempotent: rows already encrypted with the current key are
    short-circuited as ``already_current``. Rows that fail to decrypt
    under any configured key are counted as ``failed`` and left
    untouched (operator must re-register the credentials manually).
    """
    _enforce_admin(authorization)

    cur_id = current_key_id()
    if cur_id is None:
        raise HTTPException(
            status_code=503,
            detail="ENCRYPTION_KEY is unset; cannot rewrap.",
        )

    scanned = 0
    rewrapped = 0
    already_current = 0
    failed = 0
    by_key_id: Dict[str, int] = {}
    failed_ids: list[str] = []

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(
                ExchangeConnection.id,
                ExchangeConnection.api_key_encrypted,
                ExchangeConnection.api_secret_encrypted,
            )
        )
        rows = result.all()

        for row_id, api_key_blob, api_secret_blob in rows:
            scanned += 1
            key_blob = _coerce_blob(api_key_blob)
            secret_blob = _coerce_blob(api_secret_blob)

            from ..utils.encryption import decrypt as _decrypt  # local import keeps top tidy

            ok_key, kid_key, _err_k = (
                try_decrypt(key_blob) if key_blob else (False, None, "empty")
            )
            ok_sec, kid_sec, _err_s = (
                try_decrypt(secret_blob) if secret_blob else (False, None, "empty")
            )

            if not (ok_key and ok_sec):
                failed += 1
                failed_ids.append(str(row_id))
                continue

            used = kid_key if kid_key == kid_sec else (
                kid_sec if kid_sec != cur_id else kid_key
            )
            if used:
                by_key_id[used] = by_key_id.get(used, 0) + 1

            if kid_key == cur_id and kid_sec == cur_id:
                already_current += 1
                continue

            try:
                plain_key = _decrypt(key_blob)
                plain_secret = _decrypt(secret_blob)
                new_key_blob = encrypt(plain_key)
                new_secret_blob = encrypt(plain_secret)
            except Exception as exc:
                logger.warning(
                    "[encryption-rewrap] decrypt/encrypt failed for row %s: %s",
                    row_id, exc,
                )
                failed += 1
                failed_ids.append(str(row_id))
                continue

            await db.execute(
                update(ExchangeConnection)
                .where(ExchangeConnection.id == row_id)
                .values(
                    api_key_encrypted=new_key_blob,
                    api_secret_encrypted=new_secret_blob,
                )
            )
            rewrapped += 1

        if rewrapped > 0:
            await db.commit()

    return {
        "ok": True,
        "scanned": scanned,
        "rewrapped": rewrapped,
        "already_current": already_current,
        "failed": failed,
        "failed_row_ids": failed_ids[:50],  # cap to avoid huge payloads
        "current_key_id": cur_id,
        "by_key_id_before": by_key_id,
    }
