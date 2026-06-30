import os
from typing import Any

from fastapi import APIRouter, Depends
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.auth import verify_token_header
from models.database import Config, get_session
from notifications.encryption import decrypt, encrypt, is_sensitive, mask

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

router = APIRouter(prefix="/api/config", dependencies=[Depends(verify_token_header)])

_DEFAULTS: dict[str, str] = {
    "server_name": "VPS Monitor",
    "public_url": os.environ.get("PUBLIC_URL", "http://localhost"),
    "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_password": "",
    "smtp_tls": "starttls", "smtp_from_email": "", "smtp_from_name": "VPS Monitor",
    "smtp_recipients": "", "smtp_enabled": "0",
    "evolution_url": os.environ.get("EVOLUTION_URL", ""),
    "evolution_api_key": os.environ.get("EVOLUTION_API_KEY", ""),
    "evolution_instance": os.environ.get("EVOLUTION_INSTANCE", "vps-monitor"),
    "evolution_recipients": "", "evolution_enabled": "0",
    "admin_user": os.environ.get("MONITOR_USER", "admin"),
    "admin_password": "", "require_auth": "1",
    "retention_detailed_days": os.environ.get("RETENTION_DETAILED_DAYS", "7"),
    "retention_aggregated_days": os.environ.get("RETENTION_AGGREGATED_DAYS", "30"),
}


def get_config(session: Session, key: str, default: str = "") -> str:
    """Lê config do DB (descriptografando se necessário). Uso interno."""
    row = session.get(Config, key)
    if row is None:
        return _DEFAULTS.get(key, default)
    if is_sensitive(key):
        try:
            return decrypt(row.value)
        except Exception:
            return row.value
    return row.value


@router.get("")
def read_config(session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = {r.key: r.value for r in session.execute(select(Config)).scalars().all()}
    result = {}
    for key, default_val in _DEFAULTS.items():
        raw = rows.get(key, default_val)
        if is_sensitive(key) and raw:
            try:
                decrypted = decrypt(raw)
                result[key] = mask(decrypted)
            except Exception:
                result[key] = mask(raw) if raw else ""
        else:
            result[key] = raw
    return result


@router.put("")
def write_config(body: dict[str, Any], session: Session = Depends(get_session)):
    for key, value in body.items():
        if key not in _DEFAULTS:
            continue
        str_val = str(value)
        # Não sobrescreve sensível se o valor enviado é máscara
        if is_sensitive(key) and str_val.startswith("****"):
            continue
        stored = encrypt(str_val) if is_sensitive(key) and str_val else str_val
        row = session.get(Config, key)
        if row:
            row.value = stored
        else:
            session.add(Config(key=key, value=stored))
        # Bridge: sincroniza credenciais de autenticação quando user/password são alterados
        if key == "admin_user" and str_val:
            _upsert(session, "auth_username", str_val)
        if key == "admin_password" and str_val:
            _upsert(session, "auth_password_hash", _pwd_context.hash(str_val))
    session.commit()
    return {"ok": True}


def _upsert(session: Session, key: str, value: str) -> None:
    row = session.get(Config, key)
    if row:
        row.value = value
    else:
        session.add(Config(key=key, value=value))
