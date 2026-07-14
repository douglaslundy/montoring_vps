import pytest
import importlib
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
from sqlalchemy.orm import Session


@pytest.fixture
def auth_client(test_db, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth
    importlib.reload(api.auth)
    import api.metrics
    importlib.reload(api.metrics)
    import main
    importlib.reload(main)
    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, test_db


def test_current_metrics_vazio(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/current")
    assert r.status_code == 200


def test_history_retorna_dados(auth_client):
    client, db = auth_client
    now = datetime.utcnow()
    with Session(db.engine) as session:
        for i in range(5):
            session.add(db.MetricsHistory(
                collected_at=now - timedelta(minutes=i*5),
                cpu_percent=float(10 + i),
                ram_percent=float(50 + i),
                disk_percent=30.0,
            ))
        session.commit()
    r = client.get("/api/metrics/history?metric=cpu&hours=1")
    assert r.status_code == 200
    data = r.json()
    assert data["metric"] == "cpu"
    assert len(data["data"]) == 5
    assert "value" in data["data"][0]
    assert "ts" in data["data"][0]


def test_history_hours_invalido_usa_24(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/history?metric=cpu&hours=invalid")
    assert r.status_code == 422


def test_history_metrica_invalida(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/history?metric=inexistente&hours=1")
    assert r.status_code == 200


def test_sem_autenticacao_401(test_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth
    importlib.reload(api.auth)
    import api.metrics
    importlib.reload(api.metrics)
    import main
    importlib.reload(main)
    client = TestClient(main.app)

    # Sem token no header
    response = client.get("/api/metrics/current")
    assert response.status_code == 401

    response = client.get("/api/metrics/history")
    assert response.status_code == 401


def test_container_history_hour_agrega_media_por_bucket(auth_client):
    client, db = auth_client
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    with Session(db.engine) as session:
        session.add(db.ContainerMetrics(
            collected_at=now, container_id="abc", container_name="circuitodascorridas-app",
            cpu_percent=10.0, mem_percent=40.0, net_rx_mb=1.0, net_tx_mb=0.5,
        ))
        session.add(db.ContainerMetrics(
            collected_at=now + timedelta(minutes=10), container_id="abc", container_name="circuitodascorridas-app",
            cpu_percent=20.0, mem_percent=50.0, net_rx_mb=2.0, net_tx_mb=1.5,
        ))
        session.commit()

    r = client.get("/api/metrics/container-history?container_name=circuitodascorridas-app&granularity=hour")
    assert r.status_code == 200
    data = r.json()
    assert data["granularity"] == "hour"
    last_bucket = data["data"][-1]
    assert last_bucket["cpu_percent"] == 15.0
    assert last_bucket["mem_percent"] == 45.0


def test_container_history_bucket_sem_amostra_retorna_null(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/container-history?container_name=inexistente&granularity=hour")
    data = r.json()
    assert all(p["cpu_percent"] is None for p in data["data"])


def test_container_history_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/metrics/container-history?container_name=x").status_code == 401
