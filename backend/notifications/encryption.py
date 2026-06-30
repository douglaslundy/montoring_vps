import base64
import hashlib
import os

from cryptography.fernet import Fernet

_SENSITIVE_KEYS = {"smtp_password", "evolution_api_key", "admin_password"}


def _fernet() -> Fernet:
    secret = os.environ.get("JWT_SECRET", "")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def is_sensitive(key: str) -> bool:
    return key in _SENSITIVE_KEYS


def mask(value: str) -> str:
    """Retorna '****...últimos6' para campos sensíveis."""
    suffix = value[-6:] if len(value) >= 6 else value
    return f"****...{suffix}"
