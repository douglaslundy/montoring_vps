import pytest
import os
from fastapi.testclient import TestClient

@pytest.fixture
def client(test_db, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "senha123")
    monkeypatch.setenv("JWT_SECRET", "secret-de-teste-32-caracteres-ok")
    import importlib
    import api.auth as auth_mod
    importlib.reload(auth_mod)
    import main
    importlib.reload(main)
    return TestClient(main.app, raise_server_exceptions=True)

def test_login_correto(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "senha123"})
    assert r.status_code == 200
    assert "token" in r.json()

def test_login_senha_errada(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "errada"})
    assert r.status_code == 401

def test_rota_protegida_sem_token(client):
    r = client.get("/api/metrics/current")
    assert r.status_code == 401

def test_rota_protegida_com_token(client):
    token = client.post(
        "/api/auth/login", json={"username": "admin", "password": "senha123"}
    ).json()["token"]
    r = client.get("/api/metrics/current", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

def test_health_sem_auth(client):
    r = client.get("/api/health")
    assert r.status_code == 200
