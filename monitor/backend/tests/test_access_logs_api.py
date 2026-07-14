import importlib
import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import AsyncMock, patch


@pytest.fixture
def auth_client(test_db, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth
    importlib.reload(api.auth)
    import api.access_logs
    importlib.reload(api.access_logs)
    import main
    importlib.reload(main)
    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, test_db


def _seed_daily(db, day, ip, sistema, count):
    with Session(db.engine) as session:
        session.add(db.AccessLogDaily(day=day, ip=ip, sistema=sistema, count=count))
        session.commit()


def test_summary_agrega_por_ip(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 5)
    _seed_daily(db, today, "203.0.113.10", "monitor.dlsistemas.com.br", 2)
    _seed_daily(db, today, "198.51.100.20", "app2.dlsistemas.com.br", 1)

    r = client.get("/api/access-logs/summary?days=7")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    top = data[0]
    assert top["ip"] == "203.0.113.10"
    assert top["total_acessos"] == 7
    assert {s["sistema"] for s in top["sistemas"]} == {"app2.dlsistemas.com.br", "monitor.dlsistemas.com.br"}


def test_summary_filtra_por_sistema(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 5)
    _seed_daily(db, today, "203.0.113.10", "monitor.dlsistemas.com.br", 2)

    r = client.get("/api/access-logs/summary?days=7&sistema=monitor.dlsistemas.com.br")
    data = r.json()
    assert len(data) == 1
    assert data[0]["total_acessos"] == 2


def test_summary_filtra_por_ip_prefixo(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 5)
    _seed_daily(db, today, "198.51.100.20", "app2.dlsistemas.com.br", 1)

    r = client.get("/api/access-logs/summary?days=7&ip=203.0.113")
    data = r.json()
    assert len(data) == 1
    assert data[0]["ip"] == "203.0.113.10"


def test_summary_ignora_dias_fora_da_janela(auth_client):
    client, db = auth_client
    old_day = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d")
    _seed_daily(db, old_day, "203.0.113.10", "app2.dlsistemas.com.br", 5)

    r = client.get("/api/access-logs/summary?days=7")
    assert r.json() == []


def test_sistemas_retorna_lista_distinta(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 5)
    _seed_daily(db, today, "198.51.100.20", "monitor.dlsistemas.com.br", 1)

    r = client.get("/api/access-logs/sistemas")
    assert r.status_code == 200
    assert set(r.json()) == {"app2.dlsistemas.com.br", "monitor.dlsistemas.com.br"}


def test_ip_detail_retorna_geo_e_recentes(auth_client):
    client, db = auth_client
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 3)
    with Session(db.engine) as session:
        session.add(db.AccessLog(
            accessed_at=now, ip="203.0.113.10", sistema="app2.dlsistemas.com.br",
            path="/api/pedidos", method="GET", status_code=200,
        ))
        session.commit()

    fake_geo = {"is_private": False, "country": "Brazil", "region": "SP", "city": "São Paulo", "isp": "X", "org": "Y", "lat": -23.5, "lon": -46.6}
    with patch("api.access_logs.lookup_ip", AsyncMock(return_value=fake_geo)):
        r = client.get("/api/access-logs/ip/203.0.113.10?days=7")

    assert r.status_code == 200
    data = r.json()
    assert data["ip"] == "203.0.113.10"
    assert data["geo"]["country"] == "Brazil"
    assert data["total_acessos"] == 3
    assert len(data["acessos_recentes"]) == 1
    assert data["acessos_recentes"][0]["path"] == "/api/pedidos"


def test_endpoints_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/access-logs/summary").status_code == 401
    assert client.get("/api/access-logs/sistemas").status_code == 401
    assert client.get("/api/access-logs/ip/203.0.113.10").status_code == 401


def test_summary_por_sistema_agrega_por_sistema_e_ip(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "circuitodascorridas.dlsistemas.com.br", 5)
    _seed_daily(db, today, "198.51.100.20", "circuitodascorridas.dlsistemas.com.br", 3)
    _seed_daily(db, today, "203.0.113.10", "monitor.dlsistemas.com.br", 2)

    r = client.get("/api/access-logs/summary-por-sistema?days=7")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    top = data[0]
    assert top["sistema"] == "circuitodascorridas.dlsistemas.com.br"
    assert top["total_acessos"] == 8
    assert top["ips"][0]["ip"] == "203.0.113.10"
    assert top["ips"][0]["count"] == 5


def test_summary_por_sistema_filtra_por_ip_prefixo(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "circuitodascorridas.dlsistemas.com.br", 5)
    _seed_daily(db, today, "198.51.100.20", "circuitodascorridas.dlsistemas.com.br", 3)

    r = client.get("/api/access-logs/summary-por-sistema?days=7&ip=203.0.113")
    data = r.json()
    assert len(data) == 1
    assert data[0]["total_acessos"] == 5
    assert len(data[0]["ips"]) == 1
    assert data[0]["ips"][0]["ip"] == "203.0.113.10"


def test_summary_por_sistema_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/access-logs/summary-por-sistema").status_code == 401
