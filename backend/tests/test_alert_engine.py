import asyncio
from datetime import datetime
import pytest
from sqlalchemy.orm import Session
from models.database import AlertLog, AlertRule, Config, engine, init_db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    import models.database as db_module
    test_engine = db_module.create_engine(f"sqlite:///{tmp_path}/test.db")
    db_module.Base.metadata.create_all(test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    import notifications.alert_engine as ae
    monkeypatch.setattr(ae, "engine", test_engine)
    return test_engine


def make_metrics(cpu=10.0, ram=50.0, disk=60.0, temp=40.0, load=0.5):
    return {
        "cpu": {"percent": cpu, "load": [load, load, load]},
        "ram": {"percent": ram},
        "disk": {"percent": disk},
        "temperature_c": temp,
    }


def add_rule(engine, **kwargs):
    defaults = dict(
        nome="Test", metrica="cpu_percent", operador=">",
        threshold=80.0, duracao_minutos=0, severidade="aviso",
        canal_email=0, canal_whatsapp=0, cooldown_minutos=30, ativo=1,
    )
    defaults.update(kwargs)
    with Session(engine) as s:
        rule = AlertRule(**defaults)
        s.add(rule)
        s.commit()
        return rule.id


def count_open(engine):
    with Session(engine) as s:
        return s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).count()


def test_creates_alert_when_threshold_exceeded(fresh_db):
    rule_id = add_rule(fresh_db, threshold=80.0, metrica="cpu_percent", operador=">")
    result = asyncio.run(
        __import__("notifications.alert_engine", fromlist=["evaluate"]).evaluate(
            make_metrics(cpu=90.0), []
        )
    )
    assert len(result) == 1
    assert result[0]["metrica"] == "cpu_percent"
    assert count_open(fresh_db) == 1


def test_no_alert_when_below_threshold(fresh_db):
    add_rule(fresh_db, threshold=80.0, metrica="cpu_percent", operador=">")
    result = asyncio.run(
        __import__("notifications.alert_engine", fromlist=["evaluate"]).evaluate(
            make_metrics(cpu=70.0), []
        )
    )
    assert result == []
    assert count_open(fresh_db) == 0


def test_resolves_alert_when_condition_clears(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=80.0, metrica="cpu_percent", operador=">")
    asyncio.run(evaluate(make_metrics(cpu=90.0), []))
    assert count_open(fresh_db) == 1
    asyncio.run(evaluate(make_metrics(cpu=70.0), []))
    assert count_open(fresh_db) == 0


def test_no_duplicate_alert(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=80.0, metrica="cpu_percent", operador=">")
    asyncio.run(evaluate(make_metrics(cpu=90.0), []))
    asyncio.run(evaluate(make_metrics(cpu=90.0), []))
    assert count_open(fresh_db) == 1


def test_container_stopped_creates_alert(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1)
    containers = [{"name": "nginx", "status": "exited"}]
    result = asyncio.run(evaluate(make_metrics(), containers))
    assert any("nginx" in r["mensagem"] for r in result)


def test_container_stopped_resolves_when_running(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1)
    asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "exited"}]))
    assert count_open(fresh_db) == 1
    asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "running"}]))
    assert count_open(fresh_db) == 0


def test_container_stopped_resolves_when_removed(fresh_db):
    """Container parado e depois removido (não reiniciado) não deve ficar preso."""
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1)
    asyncio.run(evaluate(make_metrics(), [{"name": "old_nginx", "status": "exited"}]))
    assert count_open(fresh_db) == 1
    # container removido: some da lista por completo, nunca mais aparece "running"
    asyncio.run(evaluate(make_metrics(), []))
    assert count_open(fresh_db) == 0


def test_none_metric_does_not_crash(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="temperature_c", operador=">", threshold=75.0)
    metrics = make_metrics()
    metrics["temperature_c"] = None
    result = asyncio.run(evaluate(metrics, []))
    assert result == []


def test_container_stopped_alert_grava_vps_name_padrao(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1)
    asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "exited"}]))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
    assert log.vps_name == "VPS Monitor"


def test_metric_alert_grava_vps_name_configurado(fresh_db):
    from notifications.alert_engine import evaluate
    rule_id = add_rule(fresh_db, threshold=80.0, metrica="cpu_percent", operador=">")
    with Session(fresh_db) as s:
        s.add(Config(key="server_name", value="VPS-SP1"))
        s.commit()
    asyncio.run(evaluate(make_metrics(cpu=90.0), []))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.rule_id == rule_id).first()
    assert log.vps_name == "VPS-SP1"


import json


def make_containers(*, cpu=None, mem=None, net=None):
    """Helper: monta lista de containers com campos usados no contexto."""
    cpu = cpu or {}
    mem = mem or {}
    net = net or {}
    names = set(cpu) | set(mem) | set(net)
    return [
        {
            "name": n,
            "cpu_percent": cpu.get(n, 0.0),
            "mem_percent": mem.get(n, 0.0),
            "net_rx_mb": net.get(n, (0.0, 0.0))[0] if n in net else 0.0,
            "net_tx_mb": net.get(n, (0.0, 0.0))[1] if n in net else 0.0,
        }
        for n in names
    ]


def test_cpu_alert_grava_contexto_top_cpu_e_top_rede(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=80.0, metrica="cpu_percent", operador=">")
    containers = make_containers(
        cpu={"api": 90.0, "worker": 40.0, "db": 10.0},
        net={"api": (300.0, 20.0), "worker": (5.0, 1.0), "db": (1.0, 1.0)},
    )
    asyncio.run(evaluate(make_metrics(cpu=90.0), containers))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
    ctx = json.loads(log.contexto)
    assert ctx["top_cpu"][0]["nome"] == "api"
    assert ctx["top_cpu"][0]["valor"] == 90.0
    assert ctx["top_rede"][0]["nome"] == "api"


def test_ram_alert_grava_contexto_top_mem(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=80.0, metrica="ram_percent", operador=">")
    containers = make_containers(mem={"api": 88.0, "worker": 30.0})
    asyncio.run(evaluate(make_metrics(ram=90.0), containers))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
    ctx = json.loads(log.contexto)
    assert ctx["top_mem"][0]["nome"] == "api"
    assert ctx["top_mem"][0]["valor"] == 88.0


def test_load_alert_grava_contexto_top_cpu(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=6.0, metrica="load_1m", operador=">")
    containers = make_containers(cpu={"api": 95.0})
    asyncio.run(evaluate(make_metrics(load=7.5), containers))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
    ctx = json.loads(log.contexto)
    assert ctx["top_cpu"][0]["nome"] == "api"


def test_temperatura_alert_nao_grava_contexto(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=75.0, metrica="temperature_c", operador=">")
    asyncio.run(evaluate(make_metrics(temp=80.0), []))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
    assert log.contexto is None


def test_disco_alert_grava_contexto_top_disco(fresh_db):
    from notifications.alert_engine import evaluate
    from models.database import ContainerDiskUsage
    from datetime import datetime
    add_rule(fresh_db, threshold=80.0, metrica="disk_percent", operador=">")
    with Session(fresh_db) as s:
        now = datetime.utcnow()
        s.add(ContainerDiskUsage(collected_at=now, container_id="a1", container_name="logs-service", size_rw_mb=500.0, size_rootfs_mb=800.0))
        s.add(ContainerDiskUsage(collected_at=now, container_id="a2", container_name="db", size_rw_mb=50.0, size_rootfs_mb=200.0))
        s.commit()
    asyncio.run(evaluate(make_metrics(disk=90.0), []))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
    ctx = json.loads(log.contexto)
    assert ctx["top_disco"][0]["nome"] == "logs-service"
    assert ctx["top_disco"][0]["valor_mb"] == 500.0


def test_disco_alert_sem_dados_de_disco_grava_contexto_none(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=80.0, metrica="disk_percent", operador=">")
    asyncio.run(evaluate(make_metrics(disk=90.0), []))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
    assert log.contexto is None


def test_container_stopped_grava_contexto_com_motivo_real(fresh_db):
    from unittest.mock import AsyncMock
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1)

    mock_dc = AsyncMock()
    mock_dc.container_inspect = AsyncMock(return_value={
        "State": {"ExitCode": 137, "OOMKilled": True, "Error": "", "FinishedAt": "2026-07-08T21:58:03Z"}
    })
    containers = [{"name": "worker", "status": "exited", "id_full": "dead123beef456"}]
    asyncio.run(evaluate(make_metrics(), containers, mock_dc))

    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
    ctx = json.loads(log.contexto)
    assert ctx["exit_code"] == 137
    assert ctx["oom_killed"] is True
    mock_dc.container_inspect.assert_awaited_once_with("dead123beef456")


def test_container_stopped_sem_docker_client_grava_contexto_none(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1)
    containers = [{"name": "worker", "status": "exited", "id_full": "dead123beef456"}]
    asyncio.run(evaluate(make_metrics(), containers))  # sem docker_client, como os testes antigos

    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
    assert log.contexto is None


def test_container_stopped_inspect_falha_nao_impede_alerta(fresh_db):
    from unittest.mock import AsyncMock
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1)

    mock_dc = AsyncMock()
    mock_dc.container_inspect = AsyncMock(side_effect=Exception("container ja removido"))
    containers = [{"name": "worker", "status": "exited", "id_full": "dead123beef456"}]
    result = asyncio.run(evaluate(make_metrics(), containers, mock_dc))

    assert any("worker" in r["mensagem"] for r in result)
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
    assert log.contexto is None
