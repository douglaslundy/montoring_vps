import json
import os
import pytest
import importlib
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _garante_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")


@pytest.fixture
def auth_client(test_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    monkeypatch.setenv("FIREWALL_STATE_FILE", str(tmp_path / "firewall-state.json"))

    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth as auth_mod
    importlib.reload(auth_mod)
    import api.firewall as firewall_mod
    importlib.reload(firewall_mod)
    import main
    importlib.reload(main)

    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _escrever_estado(tmp_path, regras):
    caminho = tmp_path / "firewall-state.json"
    caminho.write_text(json.dumps({"regras": regras}), encoding="utf-8")


def test_listar_regras_sem_arquivo_de_estado(auth_client):
    r = auth_client.get("/api/firewall/rules")
    assert r.status_code == 200
    assert r.json()["regras"] == []
    assert r.json()["jobs_pendentes"] == []


def test_listar_regras_le_snapshot(auth_client, tmp_path):
    _escrever_estado(tmp_path, [
        {"porta": 22, "protocolo": "tcp", "permitir": True, "origem_ip": None, "protegida": True},
        {"porta": 8081, "protocolo": "tcp", "permitir": True, "origem_ip": None, "protegida": False},
    ])
    r = auth_client.get("/api/firewall/rules")
    assert r.status_code == 200
    regras = r.json()["regras"]
    assert len(regras) == 2
    assert regras[0]["protegida"] is True
    assert regras[1]["protegida"] is False


def test_criar_regra_add_sucesso(auth_client, test_db):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": True, "porta": 8081, "protocolo": "tcp", "origem_ip": None,
    })
    assert r.status_code == 202
    request_id = r.json()["request_id"]

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        req = session.get(test_db.FirewallRuleRequest, request_id)
    assert req.acao == "add"
    assert req.porta == 8081
    assert req.status == "pending"


def test_criar_regra_bloqueia_porta_22_add(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": True, "porta": 22, "protocolo": "tcp", "origem_ip": None,
    })
    assert r.status_code == 400


def test_criar_regra_bloqueia_porta_80_remove(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "remove", "permitir": True, "porta": 80, "protocolo": "tcp", "origem_ip": None,
    })
    assert r.status_code == 400


def test_criar_regra_bloqueia_porta_443(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": False, "porta": 443, "protocolo": "tcp", "origem_ip": None,
    })
    assert r.status_code == 400


def test_criar_regra_protocolo_invalido(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": True, "porta": 8081, "protocolo": "icmp", "origem_ip": None,
    })
    assert r.status_code == 400


def test_criar_regra_porta_fora_do_range(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": True, "porta": 70000, "protocolo": "tcp", "origem_ip": None,
    })
    assert r.status_code == 400


def test_criar_regra_origem_ip_invalida(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": True, "porta": 8081, "protocolo": "tcp", "origem_ip": "999.999.999.999",
    })
    assert r.status_code == 400


def test_criar_regra_origem_cidr_valido_aceito(auth_client, test_db):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": True, "porta": 8081, "protocolo": "tcp", "origem_ip": "203.0.113.0/24",
    })
    assert r.status_code == 202


def test_criar_regra_409_se_pedido_identico_pendente(auth_client):
    body = {"acao": "add", "permitir": True, "porta": 8081, "protocolo": "tcp", "origem_ip": None}
    auth_client.post("/api/firewall/rules", json=body)
    r = auth_client.post("/api/firewall/rules", json=body)
    assert r.status_code == 409


def test_criar_regra_acao_invalida(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "modificar", "permitir": True, "porta": 8081, "protocolo": "tcp", "origem_ip": None,
    })
    assert r.status_code == 400


def test_firewall_endpoints_sem_autenticacao_401():
    import main
    client = TestClient(main.app)
    assert client.get("/api/firewall/rules").status_code == 401
    assert client.post("/api/firewall/rules", json={}).status_code == 401
