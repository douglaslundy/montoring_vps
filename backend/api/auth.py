import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext

from sqlalchemy.orm import Session

from limiter import limiter
from models.database import Config, engine

auth_router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.environ.get("JWT_SECRET")
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET não definido. Configure no arquivo .env")
ALGORITHM = "HS256"


def _get_credentials() -> tuple[str, str]:
    with Session(engine) as session:
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


def verify_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


async def verify_token_header(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token ausente")
    if verify_token(authorization.removeprefix("Bearer ")) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")


async def get_token_data(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token ausente")
    payload = verify_token(authorization.removeprefix("Bearer "))
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    return payload


@auth_router.post("/auth/login")
@limiter.limit("5/minute")
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    username, pw_hash = _get_credentials()
    if form_data.username != username or not pwd_context.verify(form_data.password, pw_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")
    token = create_token(form_data.username)
    return {"token": token, "token_type": "bearer"}


@auth_router.get("/auth/me")
async def me(token_data: dict = Depends(get_token_data)):
    return {"username": token_data["sub"], "role": "admin"}
