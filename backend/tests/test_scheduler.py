import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime


@pytest.mark.asyncio
async def test_collect_and_store_salva_no_banco(test_db):
    mock_host = {
        "cpu": {"percent": 25.0, "load": [1.0, 0.8, 0.6], "cores": 4, "model": "Test CPU"},
        "ram": {"total_mb": 8192, "used_mb": 2048, "available_mb": 6144, "percent": 25.0},
        "disk": {"total_gb": 100.0, "used_gb": 30.0, "available_gb": 70.0, "percent": 30.0, "mountpoint": "/"},
        "net": {"rx_bytes_s": 1024, "tx_bytes_s": 512, "interface": "eth0"},
        "uptime": {"days": 1, "hours": 2, "minutes": 30, "seconds": 95400},
        "temperature_c": 42.5,
    }
    mock_containers = [
        {"id": "abc123", "id_full": "abc123def456", "name": "test", "image": "nginx",
         "status": "running", "status_text": "Up", "cpu_percent": 2.0,
         "mem_usage_mb": 100.0, "mem_limit_mb": 512.0, "mem_percent": 19.5,
         "net_rx_mb": 0, "net_tx_mb": 0, "restart_count": 0}
    ]

    import collector.scheduler as sched
    from sqlalchemy.orm import Session
    from models.database import MetricsHistory, ContainerMetrics

    with patch("collector.scheduler.collect_host_metrics", return_value=mock_host), \
         patch.object(sched.docker_client, "collect_all", AsyncMock(return_value=mock_containers)), \
         patch("collector.scheduler.manager") as mock_mgr:
        mock_mgr.broadcast = AsyncMock()
        await sched.collect_and_store()

    with Session(test_db.engine) as session:
        row = session.query(MetricsHistory).first()
        assert row is not None
        assert row.cpu_percent == 25.0
        assert row.temperature_c == 42.5
        c_row = session.query(ContainerMetrics).first()
        assert c_row is not None
        assert c_row.container_name == "test"


@pytest.mark.asyncio
async def test_collect_disk_usage_salva_no_banco(test_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    mock_data = [
        {"Id": "abc123def456", "Names": ["/logs-service"], "SizeRw": 13107200, "SizeRootFs": 356515840},
        {"Id": "def456abc123", "Names": ["/db"], "SizeRw": 1048576, "SizeRootFs": 209715200},
    ]

    import collector.scheduler as sched
    from sqlalchemy.orm import Session
    from models.database import ContainerDiskUsage

    with patch.object(sched.docker_client, "list_containers_with_size", AsyncMock(return_value=mock_data)):
        await sched.collect_disk_usage()

    with Session(test_db.engine) as session:
        rows = session.query(ContainerDiskUsage).order_by(ContainerDiskUsage.size_rw_mb.desc()).all()
    assert len(rows) == 2
    assert rows[0].container_name == "logs-service"
    assert rows[0].size_rw_mb == pytest.approx(12.5, abs=0.1)
    assert rows[0].size_rootfs_mb == pytest.approx(340.0, abs=0.1)


@pytest.mark.asyncio
async def test_collect_disk_usage_erro_docker_nao_lanca_excecao(test_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    import collector.scheduler as sched
    with patch.object(sched.docker_client, "list_containers_with_size", AsyncMock(side_effect=Exception("socket indisponivel"))):
        await sched.collect_disk_usage()  # não deve levantar
