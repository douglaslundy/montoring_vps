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
