import importlib

import httpx
import pytest


@pytest.fixture
def app(test_db, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "senha123")
    monkeypatch.setenv("JWT_SECRET", "secret-de-teste-32-caracteres-ok")
    # Reload limiter first so each test gets a fresh in-memory storage
    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth as auth_mod
    importlib.reload(auth_mod)
    import main
    importlib.reload(main)
    return main.app


@pytest.mark.asyncio
async def test_login_sucesso(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/auth/login", data={"username": "admin", "password": "senha123"})
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_senha_errada(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/auth/login", data={"username": "admin", "password": "errada"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_sem_token(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_com_token_valido(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        login_r = await ac.post(
            "/api/auth/login", data={"username": "admin", "password": "senha123"}
        )
        token = login_r.json()["token"]
        r = await ac.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "admin"
    assert data["role"] == "admin"
