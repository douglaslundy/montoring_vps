import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from models.database import Config, get_session

auth_router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.environ.get("JWT_SECRET", "insecure-default-change-this-now-please")
ALGORITHM = "HS256"


class LoginRequest(BaseModel):
    username: str
    password: str


def _get_credentials() -> tuple[str, str]:
    with get_session() as session:
        u = session.get(Config, "auth_username")
        p = session.get(Config, "auth_password_hash")
    username = u.value if u else os.environ.get("MONITOR_USER", "admin")
    if p:
        return username, p.value
    raw = os.environ.get("MONITOR_PASSWORD", "admin")
    return username, pwd_context.hash(raw)


def create_token(username: str) -> str:
    exp = datetime.utcnow() + timedelta(hours=24)
    return jwt.encode({"sub": username, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


async def verify_token_header(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token ausente")
    if verify_token(authorization.removeprefix("Bearer ")) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")


@auth_router.post("/auth/login")
def login(body: LoginRequest):
    username, pw_hash = _get_credentials()
    if body.username != username or not pwd_context.verify(body.password, pw_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")
    return {"token": create_token(body.username)}
