import base64
from cryptography.fernet import Fernet, MultiFernet
from app.config import settings


def _normalize_key(raw: str) -> bytes:
    raw_key = raw.encode('utf-8')
    if len(raw_key) < 32:
        raw_key = raw_key.ljust(32, b'0')
    elif len(raw_key) > 32:
        raw_key = raw_key[:32]
    return base64.urlsafe_b64encode(raw_key)


def _parse_keys() -> list[str]:
    raw = settings.ENCRYPTION_KEY or ""
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise ValueError(
            "ENCRYPTION_KEY is empty. Set it to one or more comma-separated keys "
            "(first key is used to encrypt; all are tried for decrypt)."
        )
    return keys


def get_fernet() -> MultiFernet:
    keys = _parse_keys()
    return MultiFernet([Fernet(_normalize_key(k)) for k in keys])


def encrypt(data: str) -> bytes:
    if not data:
        return b""
    return get_fernet().encrypt(data.encode('utf-8'))


def decrypt(token) -> str:
    if not token:
        return ""
    if isinstance(token, memoryview):
        token = bytes(token)
    return get_fernet().decrypt(token).decode('utf-8')
