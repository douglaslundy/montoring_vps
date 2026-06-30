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
    r = client.get("/api/metrics/history?metric=cpu&range=1h")
    assert r.status_code == 200
    data = r.json()
    assert data["metric"] == "cpu"
    assert len(data["data"]) == 5
    assert "value" in data["data"][0]
    assert "ts" in data["data"][0]


def test_history_range_invalido_usa_1h(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/history?metric=cpu&range=invalido")
    assert r.status_code == 200


def test_history_metrica_invalida(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/history?metric=inexistente&range=1h")
    assert r.status_code == 200
