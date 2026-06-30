import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(test_db):
    import main
    import importlib
    importlib.reload(main)
    return TestClient(main.app)


def test_websocket_conecta_e_recebe(client):
    with client.websocket_connect("/ws/metrics") as ws:
        # Só verifica que conecta sem erro
        # Dados chegam quando o scheduler faz broadcast
        pass


@pytest.mark.asyncio
async def test_broadcast_envia_para_clientes(test_db):
    from ws.stream import manager
    from fastapi import WebSocket
    from unittest.mock import AsyncMock, MagicMock

    mock_ws = MagicMock(spec=WebSocket)
    mock_ws.send_json = AsyncMock()
    manager.active.append(mock_ws)

    await manager.broadcast({"ts": "2026-06-30", "cpu": {"percent": 10}})

    mock_ws.send_json.assert_called_once()
    manager.active.remove(mock_ws)
