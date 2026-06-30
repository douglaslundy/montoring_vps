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

MOCK_PROCESSED_STATS = {
    "cpu_percent": 20.0,
    "mem_usage_mb": 100.0,
    "mem_limit_mb": 1024.0,
    "mem_percent": 9.8,
    "net_rx_mb": 0.001,
    "net_tx_mb": 0.0,
    "block_read_mb": 0.0,
    "block_write_mb": 0.0,
}


def _make_mock_http_client(json_return):
    """Return a mock async HTTP client whose GET always returns json_return."""
    mock_response = MagicMock()
    mock_response.json.return_value = json_return
    mock_response.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    return mock_http


# ---------------------------------------------------------------------------
# Standalone helper tests
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Required tests (Fix 4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_containers():
    """test_list_containers — verifica que list_containers() retorna lista de containers."""
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_http = _make_mock_http_client(MOCK_CONTAINERS)
    with patch.object(client, "_client", return_value=mock_http):
        result = await client.list_containers()

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["Id"] == "abc123def456"
    assert result[0]["State"] == "running"


@pytest.mark.asyncio
async def test_container_stats_calcula_cpu():
    """Verifica que container_stats() calcula cpu_percent corretamente."""
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_http = _make_mock_http_client(MOCK_STATS)
    with patch.object(client, "_client", return_value=mock_http):
        result = await client.container_stats("abc123def456")

    assert result is not None
    assert result["cpu_percent"] == pytest.approx(20.0, abs=0.1)
    # Verify all required fields are present
    for field in ("mem_usage_mb", "mem_limit_mb", "mem_percent",
                  "net_rx_mb", "net_tx_mb", "block_read_mb", "block_write_mb"):
        assert field in result, f"Campo ausente: {field}"


@pytest.mark.asyncio
async def test_container_stopped():
    """Verifica que list_containers(all=True) retorna containers parados (exited)."""
    from collector.docker_client import DockerClient

    stopped_containers = [
        {
            "Id": "dead123beef456",
            "Names": ["/parado"],
            "Image": "ubuntu:latest",
            "State": "exited",
            "Status": "Exited (0) 1 hour ago",
            "HostConfig": {"RestartCount": 0},
        }
    ]

    client = DockerClient()
    mock_http = _make_mock_http_client(stopped_containers)
    with patch.object(client, "_client", return_value=mock_http):
        result = await client.list_containers()

    assert len(result) == 1
    assert result[0]["State"] == "exited"
    assert result[0]["Names"] == ["/parado"]


@pytest.mark.asyncio
async def test_container_inspect():
    """Verifica que container_inspect() retorna o JSON completo do container."""
    from collector.docker_client import DockerClient

    mock_inspect = {
        "Id": "abc123def456",
        "Name": "/meu-container",
        "State": {"Status": "running", "Running": True},
        "Config": {"Image": "nginx:latest"},
    }

    client = DockerClient()
    mock_http = _make_mock_http_client(mock_inspect)
    with patch.object(client, "_client", return_value=mock_http):
        result = await client.container_inspect("abc123def456")

    assert result["Id"] == "abc123def456"
    assert result["Name"] == "/meu-container"
    assert result["State"]["Running"] is True


# ---------------------------------------------------------------------------
# collect_all integration (updated for new API)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_collect_all():
    from collector.docker_client import DockerClient

    async def mock_list():
        return MOCK_CONTAINERS

    async def mock_container_stats(cid):
        return MOCK_PROCESSED_STATS

    client = DockerClient()
    with patch.object(client, "list_containers", mock_list), \
         patch.object(client, "container_stats", mock_container_stats):
        result = await client.collect_all()

    assert len(result) == 1
    c = result[0]
    assert c["name"] == "meu-container"
    assert c["status"] == "running"
    assert c["cpu_percent"] == pytest.approx(20.0, abs=0.1)
    assert c["mem_usage_mb"] == pytest.approx(100.0, abs=1)
    assert c["mem_percent"] == pytest.approx(9.8, abs=0.2)
