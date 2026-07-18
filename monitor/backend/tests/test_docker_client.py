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


# ---------------------------------------------------------------------------
# Container control (start/stop/restart) + list_containers_with_size
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_container_chama_endpoint_correto():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        await client.start_container("abc123")

    mock_http.post.assert_called_once_with("/containers/abc123/start", params={})


@pytest.mark.asyncio
async def test_stop_container_passa_timeout():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        await client.stop_container("abc123", timeout=5)

    mock_http.post.assert_called_once_with("/containers/abc123/stop", params={"t": 5})


@pytest.mark.asyncio
async def test_restart_container_trata_304_como_sucesso():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 304
    mock_response.raise_for_status = MagicMock(side_effect=AssertionError("não deveria chamar raise_for_status em 304"))
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        await client.restart_container("abc123")  # não deve levantar exceção


@pytest.mark.asyncio
async def test_start_container_propaga_erro_404():
    import httpx
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("not found", request=MagicMock(), response=mock_response)
    )
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        with pytest.raises(httpx.HTTPStatusError):
            await client.start_container("inexistente")


@pytest.mark.asyncio
async def test_list_containers_with_size():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_data = [
        {"Id": "abc123def456", "Names": ["/meu-container"], "SizeRw": 13107200, "SizeRootFs": 356515840},
    ]
    mock_http = _make_mock_http_client(mock_data)
    with patch.object(client, "_client", return_value=mock_http):
        result = await client.list_containers_with_size()

    assert result == mock_data
    mock_http.get.assert_called_once_with("/containers/json", params={"all": True, "size": True})


@pytest.mark.asyncio
async def test_remove_container_chama_endpoint_correto():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_response.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.delete = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        await client.remove_container("abc123")

    mock_http.delete.assert_called_once_with("/containers/abc123")


@pytest.mark.asyncio
async def test_remove_container_propaga_erro_409_quando_rodando():
    import httpx
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 409
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("conflict", request=MagicMock(), response=mock_response)
    )
    mock_http = AsyncMock()
    mock_http.delete = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        with pytest.raises(httpx.HTTPStatusError):
            await client.remove_container("abc123")


@pytest.mark.asyncio
async def test_list_images_chama_endpoint_correto():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_data = [
        {"Id": "sha256:abc", "RepoTags": ["corridas-app:latest"], "Size": 1330000000, "Containers": 1},
        {"Id": "sha256:def", "RepoTags": ["corridas-app:rollback-old"], "Size": 1320000000, "Containers": 0},
    ]
    mock_http = _make_mock_http_client(mock_data)
    with patch.object(client, "_client", return_value=mock_http):
        result = await client.list_images()

    assert result == mock_data
    mock_http.get.assert_called_once_with("/images/json", params={"all": False})


@pytest.mark.asyncio
async def test_prune_build_cache_chama_endpoint_correto():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"CachesDeleted": ["abc123"], "SpaceReclaimed": 131600000000}
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        result = await client.prune_build_cache()

    assert result == {"CachesDeleted": ["abc123"], "SpaceReclaimed": 131600000000}
    mock_http.post.assert_called_once_with("/build/prune", params={"all": "true"})


@pytest.mark.asyncio
async def test_collect_all_inclui_labels():
    from collector.docker_client import DockerClient

    containers_com_label = [{
        **MOCK_CONTAINERS[0],
        "Labels": {"com.docker.compose.project": "mecanicapro"},
    }]

    async def mock_list():
        return containers_com_label

    async def mock_container_stats(cid):
        return MOCK_PROCESSED_STATS

    client = DockerClient()
    with patch.object(client, "list_containers", mock_list), \
         patch.object(client, "container_stats", mock_container_stats):
        result = await client.collect_all()

    assert result[0]["labels"] == {"com.docker.compose.project": "mecanicapro"}


@pytest.mark.asyncio
async def test_collect_all_labels_ausentes_vira_dict_vazio():
    from collector.docker_client import DockerClient

    containers_sem_label = [dict(MOCK_CONTAINERS[0])]
    containers_sem_label[0].pop("Labels", None)

    async def mock_list():
        return containers_sem_label

    async def mock_container_stats(cid):
        return MOCK_PROCESSED_STATS

    client = DockerClient()
    with patch.object(client, "list_containers", mock_list), \
         patch.object(client, "container_stats", mock_container_stats):
        result = await client.collect_all()

    assert result[0]["labels"] == {}
