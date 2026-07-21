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
    assert count == 13

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

def test_insert_metrics_history_com_swap(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        record = test_db.MetricsHistory(
            collected_at=datetime.utcnow(),
            cpu_percent=10.0, ram_percent=50.0, disk_percent=30.0,
            swap_used_mb=2048.0, swap_percent=50.0,
        )
        session.add(record)
        session.commit()
        fetched = session.query(test_db.MetricsHistory).first()
    assert fetched.swap_percent == 50.0
    assert fetched.swap_used_mb == 2048.0


def test_alert_log_tem_coluna_vps_name(test_db):
    from sqlalchemy import inspect
    cols = {c["name"] for c in inspect(test_db.engine).get_columns("alert_log")}
    assert "vps_name" in cols


def test_backfill_preenche_vps_name_com_server_name_padrao(test_db):
    from datetime import datetime
    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        session.add(test_db.AlertLog(
            rule_id=1, triggered_at=datetime.utcnow(), severidade="critico",
            metrica="cpu_percent", mensagem="alerta antigo sem vps_name",
        ))
        session.commit()

    # Simula reinício do backend, que roda a migração/backfill de novo
    test_db.init_db()

    with Session(test_db.engine) as session:
        fetched = session.query(test_db.AlertLog).filter_by(mensagem="alerta antigo sem vps_name").first()
    assert fetched.vps_name == "VPS Monitor"


def test_backfill_usa_server_name_customizado(test_db):
    from datetime import datetime
    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        cfg = session.get(test_db.Config, "server_name")
        cfg.value = "VPS-SP1"
        session.add(test_db.AlertLog(
            rule_id=1, triggered_at=datetime.utcnow(), severidade="critico",
            metrica="cpu_percent", mensagem="outro alerta antigo",
        ))
        session.commit()

    test_db.init_db()

    with Session(test_db.engine) as session:
        fetched = session.query(test_db.AlertLog).filter_by(mensagem="outro alerta antigo").first()
    assert fetched.vps_name == "VPS-SP1"


def test_alert_log_tem_coluna_contexto(test_db):
    from sqlalchemy import inspect
    cols = {c["name"] for c in inspect(test_db.engine).get_columns("alert_log")}
    assert "contexto" in cols


def test_tabela_container_disk_usage_criada(test_db):
    with test_db.engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result}
    assert "container_disk_usage" in tables


def test_insert_container_disk_usage(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        record = test_db.ContainerDiskUsage(
            collected_at=datetime.utcnow(),
            container_id="abc123",
            container_name="meu-container",
            size_rw_mb=12.5,
            size_rootfs_mb=340.0,
        )
        session.add(record)
        session.commit()
        fetched = session.query(test_db.ContainerDiskUsage).first()
    assert fetched.size_rw_mb == 12.5


def test_tabela_container_action_log_criada(test_db):
    with test_db.engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result}
    assert "container_action_log" in tables


def test_insert_container_action_log(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        record = test_db.ContainerActionLog(
            performed_at=datetime.utcnow(),
            username="admin",
            container_id="abc123",
            container_name="meu-container",
            acao="restart",
            sucesso=1,
        )
        session.add(record)
        session.commit()
        fetched = session.query(test_db.ContainerActionLog).first()
    assert fetched.acao == "restart"
    assert fetched.sucesso == 1


def test_insert_fail2ban_action_log(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        record = test_db.Fail2banActionLog(
            performed_at=datetime.utcnow(),
            username="admin",
            jail_nome="vps-monitor-teste",
            acao="create",
            sucesso=1,
        )
        session.add(record)
        session.commit()
        fetched = session.query(test_db.Fail2banActionLog).first()
    assert fetched.jail_nome == "vps-monitor-teste"
    assert fetched.acao == "create"
    assert fetched.sucesso == 1


def test_tabela_access_log_criada(test_db):
    with test_db.engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result}
    assert "access_log" in tables
    assert "access_log_daily" in tables
    assert "ip_geo_cache" in tables


def test_insert_access_log(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        session.add(test_db.AccessLog(
            accessed_at=datetime.utcnow(),
            ip="203.0.113.10",
            sistema="app2.dlsistemas.com.br",
            path="/api/pedidos",
            method="GET",
            status_code=200,
            user_agent="Mozilla/5.0",
        ))
        session.commit()
        fetched = session.query(test_db.AccessLog).first()
    assert fetched.ip == "203.0.113.10"
    assert fetched.sistema == "app2.dlsistemas.com.br"


def test_insert_access_log_daily(test_db):
    with Session(test_db.engine) as session:
        session.add(test_db.AccessLogDaily(
            day="2026-07-12", ip="203.0.113.10", sistema="app2.dlsistemas.com.br", count=5,
        ))
        session.commit()
        fetched = session.query(test_db.AccessLogDaily).first()
    assert fetched.count == 5


def test_insert_ip_geo_cache(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        session.add(test_db.IpGeoCache(
            ip="203.0.113.10", country="Brazil", city="São Paulo",
            is_private=0, looked_up_at=datetime.utcnow(),
        ))
        session.commit()
        fetched = session.get(test_db.IpGeoCache, "203.0.113.10")
    assert fetched.country == "Brazil"


def test_tabela_access_log_hourly_criada(test_db):
    with test_db.engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result}
    assert "access_log_hourly" in tables


def test_insert_access_log_hourly(test_db):
    with Session(test_db.engine) as session:
        session.add(test_db.AccessLogHourly(
            hour="2026-07-12 14", sistema="app2.dlsistemas.com.br", count=5,
        ))
        session.commit()
        fetched = session.query(test_db.AccessLogHourly).first()
    assert fetched.count == 5
    assert fetched.hour == "2026-07-12 14"


def test_tabela_alert_notification_criada(test_db):
    with test_db.engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result}
    assert "alert_notification" in tables


def test_insert_alert_notification(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        log = test_db.AlertLog(
            rule_id=1, triggered_at=datetime.utcnow(), severidade="critico",
            metrica="disk_percent", mensagem="teste",
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        log_id = log.id
        session.add(test_db.AlertNotification(
            alert_log_id=log_id, canal="whatsapp", tipo="disparo",
            status="enviado", tentativa_em=datetime.utcnow(),
        ))
        session.commit()
        fetched = session.query(test_db.AlertNotification).first()
    assert fetched.canal == "whatsapp"
    assert fetched.status == "enviado"
    assert fetched.alert_log_id == log_id


def test_init_db_backfill_regra_espaco_reaproveitavel_banco_existente(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import importlib
    import models.database as db_module
    importlib.reload(db_module)
    db_module.init_db()

    from sqlalchemy.orm import Session
    with Session(db_module.engine) as session:
        rule = session.query(db_module.AlertRule).filter_by(nome="Espaço em Disco Reaproveitável").first()
        assert rule is not None
        session.delete(rule)
        session.commit()

    db_module.init_db()  # roda de novo, como se fosse um redeploy

    with Session(db_module.engine) as session:
        rules = session.query(db_module.AlertRule).filter_by(nome="Espaço em Disco Reaproveitável").all()
    assert len(rules) == 1
    assert rules[0].metrica == "docker_reclaimable_mb"
    assert rules[0].threshold == 500


def test_insert_traefik_action_log(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        record = test_db.TraefikActionLog(
            performed_at=datetime.utcnow(),
            username="admin",
            filename="vps-monitor-teste.yml",
            acao="create",
            sucesso=1,
        )
        session.add(record)
        session.commit()
        fetched = session.query(test_db.TraefikActionLog).first()
    assert fetched.filename == "vps-monitor-teste.yml"
    assert fetched.acao == "create"
    assert fetched.sucesso == 1


def test_insert_backup_schedule(test_db):
    with Session(test_db.engine) as session:
        session.add(test_db.BackupSchedule(projeto="mecanicapro", frequencia="daily", hora=3))
        session.commit()
        fetched = session.get(test_db.BackupSchedule, "mecanicapro")
    assert fetched.frequencia == "daily"
    assert fetched.hora == 3


def test_insert_backup_job(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        job = test_db.BackupJob(
            projeto="mecanicapro", tipo="snapshot", status="pending",
            criado_em=datetime.utcnow(), username="admin",
        )
        session.add(job)
        session.commit()
        fetched = session.query(test_db.BackupJob).first()
    assert fetched.projeto == "mecanicapro"
    assert fetched.tipo == "snapshot"
    assert fetched.status == "pending"
    assert fetched.arquivo is None


def test_insert_firewall_rule_request(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        req = test_db.FirewallRuleRequest(
            acao="add", permitir=1, porta=8081, protocolo="tcp",
            origem_ip=None, status="pending", criado_em=datetime.utcnow(),
            username="admin",
        )
        session.add(req)
        session.commit()
        fetched = session.query(test_db.FirewallRuleRequest).first()
    assert fetched.acao == "add"
    assert fetched.porta == 8081
    assert fetched.status == "pending"
    assert fetched.origem_ip is None


def test_insert_project_delete_request(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        req = test_db.ProjectDeleteRequest(
            projeto="mecanicapro",
            rotas_traefik_selecionadas='["vps-monitor-mecanicapro.yml"]',
            regras_firewall_selecionadas='[{"porta": 8081, "protocolo": "tcp", "permitir": true, "origem_ip": null}]',
            snapshot_arquivo="20260721T140000Z.tar.gz",
            status="pending", criado_em=datetime.utcnow(), username="admin",
        )
        session.add(req)
        session.commit()
        fetched = session.query(test_db.ProjectDeleteRequest).first()
    assert fetched.projeto == "mecanicapro"
    assert fetched.snapshot_arquivo == "20260721T140000Z.tar.gz"
    assert fetched.status == "pending"
    assert "vps-monitor-mecanicapro.yml" in fetched.rotas_traefik_selecionadas