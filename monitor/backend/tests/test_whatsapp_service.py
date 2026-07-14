from unittest.mock import MagicMock, patch


def make_session():
    def get_config_mock(session, key, default=""):
        configs = {
            "evolution_url": "http://evo.test", "evolution_api_key": "key",
            "evolution_instance": "vps-monitor", "evolution_recipients": "5511999999999",
            "server_name": "Test Server", "public_url": "http://localhost",
        }
        return configs.get(key, default)
    return get_config_mock


def make_alert():
    return {
        "id": 1, "severidade": "aviso", "metrica": "cpu_percent",
        "mensagem": "CPU Alta: 90.0 > 80.0",
        "triggered_at": "2026-06-30T10:00:00Z",
        "valor_no_disparo": 90.0, "threshold": 80.0,
    }


def test_send_alert_converts_utc_to_local_timezone():
    """triggered_at é salvo em UTC; a mensagem deve mostrar o horário de America/Sao_Paulo (UTC-3)."""
    from notifications import whatsapp_service
    sent = {}
    with patch("notifications.whatsapp_service.get_config", make_session()), \
         patch("notifications.whatsapp_service._send_text", lambda *a: sent.setdefault("text", a[-1])):
        whatsapp_service.send_alert(make_alert(), MagicMock())
    assert "07:00:00" in sent["text"]
    assert "10:00:00" not in sent["text"]
