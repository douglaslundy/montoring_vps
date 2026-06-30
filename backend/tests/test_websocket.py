import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(test_db):
    import main
    import importlib
    importlib.reload(main)
    return TestClient(main.app)


def test_websocket_conecta_e_recebe(client, test_db):
    from api.auth import create_token

    token = create_token("admin")
    with client.websocket_connect(f"/ws/metrics?token={token}") as ws:
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


@pytest.mark.asyncio
async def test_broadcast_para_dois_clientes():
    from ws.stream import manager
    from fastapi import WebSocket
    from unittest.mock import AsyncMock

    ws1 = AsyncMock(spec=WebSocket)
    ws2 = AsyncMock(spec=WebSocket)
    manager.active = [ws1, ws2]

    data = {"cpu": 50, "ram": 60}
    await manager.broadcast(data)

    ws1.send_json.assert_called_once_with(data)
    ws2.send_json.assert_called_once_with(data)


@pytest.mark.asyncio
async def test_broadcast_remove_conexao_morta():
    from ws.stream import manager
    from fastapi import WebSocket
    from unittest.mock import AsyncMock

    ws_morto = AsyncMock(spec=WebSocket)
    ws_morto.send_json.side_effect = Exception("conexão fechada")
    ws_vivo = AsyncMock(spec=WebSocket)
    manager.active = [ws_morto, ws_vivo]

    await manager.broadcast({"cpu": 50})

    assert ws_morto not in manager.active
    assert ws_vivo in manager.active
    ws_vivo.send_json.assert_called_once()
