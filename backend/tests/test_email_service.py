import pytest
from unittest.mock import MagicMock, patch


def make_session(smtp_enabled="1", host="smtp.test.com", recipients="test@test.com", password="senha"):
    """Cria session mock com config pré-configurada."""
    def get_config_mock(session, key, default=""):
        configs = {
            "smtp_enabled": smtp_enabled, "smtp_host": host, "smtp_port": "587",
            "smtp_user": "user@test.com", "smtp_password": password,
            "smtp_tls": "starttls", "smtp_from_email": "from@test.com",
            "smtp_from_name": "VPS Monitor", "smtp_recipients": recipients,
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


def test_send_alert_calls_smtp(monkeypatch):
    from notifications import email_service
    mock_cfg = make_session()
    sent = {}
    class FakeSMTP:
        def __init__(self, host, port): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def sendmail(self, f, t, m): sent["called"] = True

    with patch("smtplib.SMTP", FakeSMTP), \
         patch("notifications.email_service.get_config", mock_cfg):
        session = MagicMock()
        email_service.send_alert(make_alert(), session)
    assert sent.get("called")


def test_send_resolution_calls_smtp(monkeypatch):
    from notifications import email_service

    sent = {}
    class FakeSMTP:
        def __init__(self, h, p): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def sendmail(self, f, t, m): sent["called"] = True

    mock_cfg = make_session()
    with patch("smtplib.SMTP", FakeSMTP), \
         patch("notifications.email_service.get_config", mock_cfg):
        session = MagicMock()
        email_service.send_resolution({**make_alert(), "resolved_at": "2026-06-30T10:05:00Z"}, session)
    assert sent.get("called")


def test_smtp_not_called_when_no_host(monkeypatch):
    from notifications import email_service
    mock_cfg = make_session(host="", recipients="test@test.com")
    called = []
    with patch("smtplib.SMTP", lambda h, p: called.append(1)), \
         patch("notifications.email_service.get_config", mock_cfg):
        try:
            email_service.send_alert(make_alert(), MagicMock())
        except ValueError:
            pass
    assert not called
