import json
import os
from datetime import datetime

import pytest


def _traefik_line(client_host="203.0.113.10", host="app2.dlsistemas.com.br", path="/api/pedidos", status=200, when=None):
    when = when or datetime.utcnow()
    return json.dumps({
        "ClientHost": client_host,
        "RequestHost": host,
        "RequestPath": path,
        "RequestMethod": "GET",
        "DownstreamStatus": status,
        "time": when.isoformat() + "Z",
        "request_User-Agent": "Mozilla/5.0",
    })


@pytest.mark.asyncio
async def test_processa_linha_valida_grava_access_log_e_daily(test_db, tmp_path, monkeypatch):
    log_file = tmp_path / "access.log"
    log_file.write_text(_traefik_line() + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        access = session.query(test_db.AccessLog).first()
        daily = session.query(test_db.AccessLogDaily).first()

    assert access is not None
    assert access.ip == "203.0.113.10"
    assert access.sistema == "app2.dlsistemas.com.br"
    assert access.path == "/api/pedidos"
    assert daily.count == 1
    assert daily.ip == "203.0.113.10"


@pytest.mark.asyncio
async def test_linha_de_asset_estatico_e_descartada(test_db, tmp_path, monkeypatch):
    log_file = tmp_path / "access.log"
    log_file.write_text(_traefik_line(path="/static/app.js") + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        assert session.query(test_db.AccessLog).count() == 0


@pytest.mark.asyncio
async def test_linha_de_health_check_e_descartada(test_db, tmp_path, monkeypatch):
    log_file = tmp_path / "access.log"
    log_file.write_text(_traefik_line(path="/health") + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        assert session.query(test_db.AccessLog).count() == 0


@pytest.mark.asyncio
async def test_linha_invalida_nao_interrompe_processamento(test_db, tmp_path, monkeypatch):
    log_file = tmp_path / "access.log"
    log_file.write_text("isso nao e json\n" + _traefik_line() + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        assert session.query(test_db.AccessLog).count() == 1


@pytest.mark.asyncio
async def test_offset_persiste_entre_chamadas(test_db, tmp_path, monkeypatch):
    log_file = tmp_path / "access.log"
    log_file.write_text(_traefik_line(client_host="203.0.113.10") + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(_traefik_line(client_host="198.51.100.20") + "\n")

    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        ips = {row.ip for row in session.query(test_db.AccessLog).all()}
    assert ips == {"203.0.113.10", "198.51.100.20"}


@pytest.mark.asyncio
async def test_mudanca_de_inode_reseta_offset(test_db, tmp_path, monkeypatch):
    log_file = tmp_path / "access.log"
    log_file.write_text(_traefik_line(client_host="203.0.113.10") + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    os.remove(log_file)
    log_file.write_text(_traefik_line(client_host="198.51.100.20") + "\n", encoding="utf-8")

    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        ips = {row.ip for row in session.query(test_db.AccessLog).all()}
    assert ips == {"203.0.113.10", "198.51.100.20"}


@pytest.mark.asyncio
async def test_arquivo_ausente_nao_lanca_excecao(test_db, tmp_path, monkeypatch):
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(tmp_path / "nao-existe.log"))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()  # não deve levantar

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        assert session.query(test_db.AccessLog).count() == 0
