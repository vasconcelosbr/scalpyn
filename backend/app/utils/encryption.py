import base64
from cryptography.fernet import Fernet
from app.config import settings

def get_fernet() -> Fernet:
    # Ensure key is exactly 32 bytes and url-safe base64 encoded for Fernet
    raw_key = settings.ENCRYPTION_KEY.encode('utf-8')
    if len(raw_key) < 32:
        raw_key = raw_key.ljust(32, b'0')
    elif len(raw_key) > 32:
        raw_key = raw_key[:32]
    
    encoded_key = base64.urlsafe_b64encode(raw_key)
    return Fernet(encoded_key)

def encrypt(data: str) -> bytes:
    if not data:
        return b""
    f = get_fernet()
    return f.encrypt(data.encode('utf-8'))

def decrypt(token) -> str:
    if not token:
        return ""
    # Fix: asyncpg returns memoryview for BYTEA columns — convert to bytes before decrypting
    if isinstance(token, memoryview):
        token = bytes(token)
    f = get_fernet()
    return f.decrypt(token).decode('utf-8')
