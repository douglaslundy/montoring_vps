import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

def test_tabelas_criadas(test_db):
    with test_db.engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result}
    assert "metrics_history" in tables
    assert "container_metrics" in tables
    assert "alert_rules" in tables
    assert "alert_log" in tables
    assert "config" in tables

def test_wal_mode_ativo(test_db):
    with test_db.engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode")).fetchone()
    assert result[0] == "wal"

def test_regras_padrao_inseridas(test_db):
    with Session(test_db.engine) as session:
        count = session.query(test_db.AlertRule).count()
    assert count == 9

def test_insert_metrics_history(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        record = test_db.MetricsHistory(
            collected_at=datetime.utcnow(),
            cpu_percent=45.2,
            ram_percent=60.1,
            disk_percent=30.0,
        )
        session.add(record)
        session.commit()
        fetched = session.query(test_db.MetricsHistory).first()
    assert fetched.cpu_percent == 45.2