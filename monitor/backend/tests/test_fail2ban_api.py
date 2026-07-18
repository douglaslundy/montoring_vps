import pytest
import importlib
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def auth_client(test_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    monkeypatch.setenv("FAIL2BAN_JAIL_DIR", str(tmp_path / "jail.d"))
    monkeypatch.setenv("FAIL2BAN_FILTER_DIR", str(tmp_path / "filter.d"))
    (tmp_path / "jail.d").mkdir()
    (tmp_path / "filter.d").mkdir()

    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth as auth_mod
    importlib.reload(auth_mod)
    import api.fail2ban as fail2ban_mod
    importlib.reload(fail2ban_mod)
    import main
    importlib.reload(main)

    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_list_jails(auth_client):
    with patch("api.fail2ban.fail2ban_client.status_all", AsyncMock(return_value=[
        {"nome": "sshd", "managed": False, "currently_banned": 0, "total_banned": 0, "currently_failed": 0, "banned_ips": []}
    ])):
        r = auth_client.get("/api/fail2ban/jails")
    assert r.status_code == 200
    assert r.json()[0]["nome"] == "sshd"


def test_criar_jail_sucesso(auth_client, test_db):
    # create_jail so escreve os arquivos e valida via dry-run — nao chama
    # reload diretamente. O fail2ban-client, invocado de dentro do container
    # do monitor, valida do lado do cliente os logpaths de TODOS os jails
    # configurados (nao so o novo), e o container nao enxerga arquivos de
    # log de outros projetos — confirmado em producao. Um watcher no host
    # (scripts/fail2ban-reload-watcher.sh) aplica o reload de la.
    import os
    with patch("api.fail2ban.fail2ban_client.dry_run_regex", AsyncMock(return_value=(True, "1 matched"))):
        r = auth_client.post("/api/fail2ban/jails", json={
            "nome_exibicao": "Teste de Bloqueio",
            "log_path": "/var/log/teste.log",
            "sample_log_line": "203.0.113.5 - erro de teste",
            "regex": r"^<HOST> - erro de teste$",
            "maxretry": 5, "findtime": 600, "bantime": 3600, "port": "http,https",
        })
    assert r.status_code == 201
    assert r.json()["slug"] == "vps-monitor-teste-de-bloqueio"
    assert os.path.exists(os.path.join(os.environ["FAIL2BAN_JAIL_DIR"], "vps-monitor-teste-de-bloqueio.local"))
    assert os.path.exists(os.path.join(os.environ["FAIL2BAN_FILTER_DIR"], "vps-monitor-teste-de-bloqueio.conf"))

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        log = session.query(test_db.Fail2banActionLog).first()
    assert log.acao == "create"
    assert log.sucesso == 1


def test_criar_jail_regex_invalido(auth_client):
    r = auth_client.post("/api/fail2ban/jails", json={
        "nome_exibicao": "Teste Invalido",
        "log_path": "/var/log/teste.log",
        "sample_log_line": "linha qualquer",
        "regex": "(sem fechar",
        "maxretry": 5, "findtime": 600, "bantime": 3600, "port": "http,https",
    })
    assert r.status_code == 400


def test_criar_jail_dry_run_nao_bate(auth_client, test_db):
    with patch("api.fail2ban.fail2ban_client.dry_run_regex", AsyncMock(return_value=(False, "0 matched"))):
        r = auth_client.post("/api/fail2ban/jails", json={
            "nome_exibicao": "Nao Bate",
            "log_path": "/var/log/teste.log",
            "sample_log_line": "linha que nao bate",
            "regex": r"^padrao-que-nao-existe$",
            "maxretry": 5, "findtime": 600, "bantime": 3600, "port": "http,https",
        })
    assert r.status_code == 400
    assert "0 matched" in r.json()["detail"]

    import os
    assert not os.path.exists(os.path.join(os.environ["FAIL2BAN_FILTER_DIR"], "vps-monitor-nao-bate.conf"))


def test_editar_jail_bloqueia_sem_prefixo(auth_client):
    r = auth_client.put("/api/fail2ban/jails/sshd", json={
        "nome_exibicao": "sshd", "log_path": "/var/log/auth.log", "sample_log_line": "x",
        "regex": "^<HOST>$", "maxretry": 5, "findtime": 600, "bantime": 3600, "port": "ssh",
    })
    assert r.status_code == 403


def test_editar_jail_dry_run_nao_bate_restaura_filtro_anterior(auth_client, test_db):
    import os
    jail_path = os.path.join(os.environ["FAIL2BAN_JAIL_DIR"], "vps-monitor-teste.local")
    filter_path = os.path.join(os.environ["FAIL2BAN_FILTER_DIR"], "vps-monitor-teste.conf")
    conteudo_filtro_original = "[Definition]\nfailregex = original\n"
    with open(jail_path, "w") as f:
        f.write("[vps-monitor-teste]\nlogpath = /var/log/original.log\n")
    with open(filter_path, "w") as f:
        f.write(conteudo_filtro_original)

    with patch("api.fail2ban.fail2ban_client.dry_run_regex", AsyncMock(return_value=(False, "0 matched"))):
        r = auth_client.put("/api/fail2ban/jails/vps-monitor-teste", json={
            "nome_exibicao": "vps-monitor-teste", "log_path": "/var/log/novo.log",
            "sample_log_line": "x", "regex": "^novo$",
            "maxretry": 5, "findtime": 600, "bantime": 3600, "port": "ssh",
        })

    assert r.status_code == 400
    with open(filter_path) as f:
        assert f.read() == conteudo_filtro_original


def test_editar_jail_sucesso_sobrescreve_arquivos(auth_client, test_db):
    import os
    jail_path = os.path.join(os.environ["FAIL2BAN_JAIL_DIR"], "vps-monitor-teste.local")
    filter_path = os.path.join(os.environ["FAIL2BAN_FILTER_DIR"], "vps-monitor-teste.conf")
    with open(jail_path, "w") as f:
        f.write("[vps-monitor-teste]\nlogpath = /var/log/original.log\n")
    with open(filter_path, "w") as f:
        f.write("[Definition]\nfailregex = original\n")

    with patch("api.fail2ban.fail2ban_client.dry_run_regex", AsyncMock(return_value=(True, "1 matched"))):
        r = auth_client.put("/api/fail2ban/jails/vps-monitor-teste", json={
            "nome_exibicao": "vps-monitor-teste", "log_path": "/var/log/novo.log",
            "sample_log_line": "x", "regex": "^novo$",
            "maxretry": 5, "findtime": 600, "bantime": 3600, "port": "ssh",
        })

    assert r.status_code == 200
    with open(jail_path) as f:
        assert "logpath = /var/log/novo.log" in f.read()


def test_excluir_jail_bloqueia_sem_prefixo(auth_client):
    r = auth_client.delete("/api/fail2ban/jails/sshd")
    assert r.status_code == 403


def test_excluir_jail_gerenciado_sucesso(auth_client, test_db):
    import os
    jail_path = os.path.join(os.environ["FAIL2BAN_JAIL_DIR"], "vps-monitor-teste.local")
    filter_path = os.path.join(os.environ["FAIL2BAN_FILTER_DIR"], "vps-monitor-teste.conf")
    with open(jail_path, "w") as f:
        f.write("[vps-monitor-teste]\n")
    with open(filter_path, "w") as f:
        f.write("[Definition]\nfailregex = x\n")

    r = auth_client.delete("/api/fail2ban/jails/vps-monitor-teste")

    assert r.status_code == 200
    assert not os.path.exists(jail_path)
    assert not os.path.exists(filter_path)


def test_unban_funciona_em_qualquer_jail(auth_client, test_db):
    with patch("api.fail2ban.fail2ban_client.unban_ip", AsyncMock(return_value=None)) as mock_unban:
        r = auth_client.post("/api/fail2ban/jails/sshd/unban", json={"ip": "203.0.113.5"})
    assert r.status_code == 200
    mock_unban.assert_awaited_once_with("sshd", "203.0.113.5")

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        log = session.query(test_db.Fail2banActionLog).first()
    assert log.acao == "unban"
    assert log.jail_nome == "sshd"


def test_fail2ban_endpoints_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/fail2ban/jails").status_code == 401
    assert client.post("/api/fail2ban/jails", json={}).status_code == 401
    assert client.delete("/api/fail2ban/jails/vps-monitor-x").status_code == 401
