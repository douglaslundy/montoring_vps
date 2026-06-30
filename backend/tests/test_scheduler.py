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
         "mem_used_mb": 100.0, "mem_limit_mb": 512.0, "mem_percent": 19.5,
         "net_rx_bytes": 0, "net_tx_bytes": 0, "restart_count": 0}
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
