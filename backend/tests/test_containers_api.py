import pytest
import importlib
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def auth_client(test_db, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")

    # Reload limiter first
    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth as auth_mod
    importlib.reload(auth_mod)
    import main
    importlib.reload(main)

    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_lista_containers(auth_client):
    with patch("collector.scheduler._last_metrics", {
        "containers": [{"id": "abc", "name": "web", "status": "running", "cpu_percent": 1.0}]
    }):
        r = auth_client.get("/api/containers")
    assert r.status_code == 200
    assert len(r.json()["containers"]) == 1


def test_containers_vazio(auth_client):
    with patch("collector.scheduler._last_metrics", {}):
        r = auth_client.get("/api/containers")
    assert r.status_code == 200
    assert r.json()["containers"] == []


@pytest.mark.asyncio
async def test_logs_container(auth_client):
    with patch("collector.scheduler.docker_client") as mock_dc:
        mock_dc.get_logs = AsyncMock(return_value=["linha 1", "linha 2"])
        r = auth_client.get("/api/containers/abc123/logs")
    assert r.status_code == 200
    assert r.json()["logs"] == ["linha 1", "linha 2"]
