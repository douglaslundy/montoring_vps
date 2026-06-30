import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

MOCK_CONTAINERS = [
    {
        "Id": "abc123def456",
        "Names": ["/meu-container"],
        "Image": "nginx:latest",
        "State": "running",
        "Status": "Up 2 hours",
        "HostConfig": {"RestartCount": 0},
    }
]

MOCK_STATS = {
    "cpu_stats": {
        "cpu_usage": {"total_usage": 200000000},
        "system_cpu_usage": 2000000000,
        "online_cpus": 2,
    },
    "precpu_stats": {
        "cpu_usage": {"total_usage": 100000000},
        "system_cpu_usage": 1000000000,
    },
    "memory_stats": {
        "usage": 104857600,
        "limit": 1073741824,
        "stats": {"cache": 0},
    },
    "networks": {"eth0": {"rx_bytes": 1024, "tx_bytes": 512}},
}


def test_calculate_cpu_percent():
    from collector.docker_client import calculate_cpu_percent
    result = calculate_cpu_percent(MOCK_STATS)
    # (100M / 1000M) * 2 cpus * 100 = 20%
    assert result == pytest.approx(20.0, abs=0.1)


def test_calculate_cpu_percent_zeros():
    from collector.docker_client import calculate_cpu_percent
    bad_stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 0}, "system_cpu_usage": 0, "online_cpus": 1},
        "precpu_stats": {"cpu_usage": {"total_usage": 0}, "system_cpu_usage": 0},
    }
    assert calculate_cpu_percent(bad_stats) == 0.0


@pytest.mark.asyncio
async def test_collect_all():
    from collector.docker_client import DockerClient

    async def mock_list():
        return MOCK_CONTAINERS

    async def mock_stats(cid):
        return MOCK_STATS

    client = DockerClient()
    with patch.object(client, "list_containers", mock_list), \
         patch.object(client, "get_stats", mock_stats):
        result = await client.collect_all()

    assert len(result) == 1
    c = result[0]
    assert c["name"] == "meu-container"
    assert c["status"] == "running"
    assert c["cpu_percent"] == pytest.approx(20.0, abs=0.1)
    assert c["mem_used_mb"] == pytest.approx(100.0, abs=1)
    assert c["mem_percent"] == pytest.approx(9.8, abs=0.2)
