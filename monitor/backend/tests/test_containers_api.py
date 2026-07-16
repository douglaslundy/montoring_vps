import pytest
import importlib
from unittest.mock import AsyncMock, patch, MagicMock
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


def test_logs_container(auth_client):
    with patch("api.containers.docker_client") as mock_dc:
        mock_dc.get_logs = AsyncMock(return_value=["linha 1", "linha 2"])
        r = auth_client.get("/api/containers/abc123/logs")
    assert r.status_code == 200
    assert r.json()["logs"] == ["linha 1", "linha 2"]


def test_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)

    r = client.get("/api/containers")
    assert r.status_code == 401

    r = client.get("/api/containers/abc123/logs")
    assert r.status_code == 401


def test_start_container_sucesso(auth_client):
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.start_container = AsyncMock(return_value=None)
        r = auth_client.post("/api/containers/abc123/start")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    mock_dc.start_container.assert_awaited_once_with("abc123")


def test_stop_container_sucesso(auth_client):
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.stop_container = AsyncMock(return_value=None)
        r = auth_client.post("/api/containers/abc123/stop")
    assert r.status_code == 200
    mock_dc.stop_container.assert_awaited_once_with("abc123")


def test_restart_container_sucesso(auth_client):
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.restart_container = AsyncMock(return_value=None)
        r = auth_client.post("/api/containers/abc123/restart")
    assert r.status_code == 200
    mock_dc.restart_container.assert_awaited_once_with("abc123")


def test_start_container_registra_log_de_sucesso(auth_client, test_db):
    from sqlalchemy.orm import Session
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.start_container = AsyncMock(return_value=None)
        auth_client.post("/api/containers/abc123/start")

    with Session(test_db.engine) as session:
        log = session.query(test_db.ContainerActionLog).first()
    assert log is not None
    assert log.acao == "start"
    assert log.container_name == "web"
    assert log.sucesso == 1
    assert log.username == "admin"


def test_stop_container_erro_registra_log_de_falha(auth_client, test_db):
    import httpx
    from sqlalchemy.orm import Session
    mock_response = MagicMock()
    mock_response.status_code = 500
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.stop_container = AsyncMock(
            side_effect=httpx.HTTPStatusError("erro", request=MagicMock(), response=mock_response)
        )
        r = auth_client.post("/api/containers/abc123/stop")

    assert r.status_code == 502
    with Session(test_db.engine) as session:
        log = session.query(test_db.ContainerActionLog).first()
    assert log.sucesso == 0
    assert log.acao == "stop"


def test_start_container_404_quando_container_nao_existe(auth_client):
    import httpx
    mock_response = MagicMock()
    mock_response.status_code = 404
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": []}):
        mock_dc.start_container = AsyncMock(
            side_effect=httpx.HTTPStatusError("nao encontrado", request=MagicMock(), response=mock_response)
        )
        r = auth_client.post("/api/containers/inexistente/start")
    assert r.status_code == 404


def test_control_endpoints_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.post("/api/containers/abc123/start").status_code == 401
    assert client.post("/api/containers/abc123/stop").status_code == 401
    assert client.post("/api/containers/abc123/restart").status_code == 401


def test_remove_container_sucesso(auth_client):
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.remove_container = AsyncMock(return_value=None)
        r = auth_client.delete("/api/containers/abc123")
    assert r.status_code == 200
    mock_dc.remove_container.assert_awaited_once_with("abc123")


def test_remove_container_registra_log_de_sucesso(auth_client, test_db):
    from sqlalchemy.orm import Session
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.remove_container = AsyncMock(return_value=None)
        auth_client.delete("/api/containers/abc123")

    with Session(test_db.engine) as session:
        log = session.query(test_db.ContainerActionLog).first()
    assert log is not None
    assert log.acao == "remove"
    assert log.container_name == "web"
    assert log.sucesso == 1


def test_remove_container_bloqueia_container_do_proprio_monitor(auth_client):
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "xyz789", "name": "monitor-backend"}]}):
        r = auth_client.delete("/api/containers/xyz789")
    assert r.status_code == 403
    mock_dc.remove_container.assert_not_called()


def test_remove_container_erro_409_registra_log_de_falha(auth_client, test_db):
    import httpx
    from sqlalchemy.orm import Session
    mock_response = MagicMock()
    mock_response.status_code = 409
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.remove_container = AsyncMock(
            side_effect=httpx.HTTPStatusError("conflict", request=MagicMock(), response=mock_response)
        )
        r = auth_client.delete("/api/containers/abc123")

    assert r.status_code == 502
    with Session(test_db.engine) as session:
        log = session.query(test_db.ContainerActionLog).first()
    assert log.sucesso == 0
    assert log.acao == "remove"


def test_remove_container_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.delete("/api/containers/abc123").status_code == 401


def test_disk_usage_sem_amostra(auth_client):
    with patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        r = auth_client.get("/api/containers/abc123/disk-usage")
    assert r.status_code == 200
    assert r.json() == {"size_rw_mb": None, "size_rootfs_mb": None, "collected_at": None}


def test_disk_usage_com_amostra(auth_client, test_db):
    from sqlalchemy.orm import Session
    from datetime import datetime
    with Session(test_db.engine) as session:
        session.add(test_db.ContainerDiskUsage(
            collected_at=datetime(2026, 7, 16, 10, 0, 0),
            container_id="abc123", container_name="web",
            size_rw_mb=12.5, size_rootfs_mb=340.2,
        ))
        session.commit()

    with patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        r = auth_client.get("/api/containers/abc123/disk-usage")

    assert r.status_code == 200
    body = r.json()
    assert body["size_rw_mb"] == 12.5
    assert body["size_rootfs_mb"] == 340.2
    assert body["collected_at"] == "2026-07-16T10:00:00"


def test_disk_usage_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/containers/abc123/disk-usage").status_code == 401
