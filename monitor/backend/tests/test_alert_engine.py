import asyncio
import re
from datetime import datetime
import pytest
from sqlalchemy.orm import Session
from models.database import AlertLog, AlertNotification, AlertRule, Config, engine, init_db


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


def test_swap_alto_cria_alerta(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=70.0, metrica="swap_percent", operador=">")
    metrics = make_metrics()
    metrics["swap"] = {"percent": 85.0}
    result = asyncio.run(evaluate(metrics, []))
    assert len(result) == 1
    assert result[0]["metrica"] == "swap_percent"


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


def _add_container_metrics(engine, container_id, restart_counts, minutos_atras_inicial=9):
    """Insere uma série de ContainerMetrics simulando o histórico de restart_count
    nos últimos `minutos_atras_inicial` minutos (1 ponto por minuto, mais recente por último)."""
    from datetime import timedelta
    from models.database import ContainerMetrics
    now = datetime.utcnow()
    with Session(engine) as s:
        for i, rc in enumerate(restart_counts):
            minutos_atras = minutos_atras_inicial - i
            s.add(ContainerMetrics(
                collected_at=now - timedelta(minutes=minutos_atras),
                container_id=container_id, container_name="worker",
                restart_count=rc, status="running",
            ))
        s.commit()


def test_restart_loop_dispara_com_3_aumentos_em_10min(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_restart_loop", operador=">=", threshold=3, duracao_minutos=10, cooldown_minutos=30)
    _add_container_metrics(fresh_db, "abc123", [0, 1, 1, 2, 2, 3, 3, 3, 3, 3])
    containers = [{"id": "abc123", "id_full": "abc123fullid", "name": "worker", "status": "running"}]
    result = asyncio.run(evaluate(make_metrics(), containers))
    assert any("worker" in r["mensagem"] and "restart loop" in r["mensagem"] for r in result)


def test_restart_loop_nao_dispara_abaixo_do_threshold(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_restart_loop", operador=">=", threshold=3, duracao_minutos=10, cooldown_minutos=30)
    _add_container_metrics(fresh_db, "abc123", [0, 0, 0, 1, 1, 1, 1, 1, 1, 1])
    containers = [{"id": "abc123", "id_full": "abc123fullid", "name": "worker", "status": "running"}]
    result = asyncio.run(evaluate(make_metrics(), containers))
    assert result == []


def test_restart_loop_contexto_sinaliza_oom(fresh_db):
    from unittest.mock import AsyncMock
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_restart_loop", operador=">=", threshold=3, duracao_minutos=10, cooldown_minutos=30)
    _add_container_metrics(fresh_db, "abc123", [0, 1, 1, 2, 2, 3, 3, 3, 3, 3])
    containers = [{"id": "abc123", "id_full": "abc123fullid", "name": "worker", "status": "running"}]

    mock_dc = AsyncMock()
    mock_dc.container_inspect = AsyncMock(return_value={"State": {"OOMKilled": True}})
    asyncio.run(evaluate(make_metrics(), containers, mock_dc))

    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.metrica == "container_restart_loop").first()
    ctx = json.loads(log.contexto)
    assert ctx["oom_killed"] is True
    mock_dc.container_inspect.assert_awaited_once_with("abc123fullid")


def test_restart_loop_resolve_quando_para_de_reiniciar(fresh_db):
    from notifications.alert_engine import evaluate
    from models.database import ContainerMetrics
    add_rule(fresh_db, metrica="container_restart_loop", operador=">=", threshold=3, duracao_minutos=10, cooldown_minutos=30)
    _add_container_metrics(fresh_db, "abc123", [0, 1, 1, 2, 2, 3, 3, 3, 3, 3])
    containers = [{"id": "abc123", "id_full": "abc123fullid", "name": "worker", "status": "running"}]
    asyncio.run(evaluate(make_metrics(), containers))
    assert count_open(fresh_db) == 1

    # Simula a janela de 10min avançando (equivalente ao que aconteceria de
    # verdade com o tempo passando entre execuções do scheduler): remove os
    # pontos antigos (que tinham os aumentos) e insere só histórico estável.
    # Sem isso, os pontos antigos continuariam dentro da janela de 10min
    # (o teste roda em milissegundos, não passa tempo real de verdade) e o
    # alerta nunca resolveria.
    with Session(fresh_db) as s:
        s.query(ContainerMetrics).filter(ContainerMetrics.container_id == "abc123").delete()
        s.commit()
    _add_container_metrics(fresh_db, "abc123", [3] * 10)
    asyncio.run(evaluate(make_metrics(), containers))
    assert count_open(fresh_db) == 0


def test_restart_loop_nao_duplica_alerta_entre_ciclos_com_aumentos_diferente(fresh_db):
    """Contêiner continua em loop entre execuções do evaluate() (aumentos muda
    de valor a cada ciclo) — não pode gerar um segundo AlertLog nem ignorar
    o cooldown configurado."""
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_restart_loop", operador=">=", threshold=3, duracao_minutos=10, cooldown_minutos=30)
    _add_container_metrics(fresh_db, "abc123", [0, 1, 1, 2, 2, 3, 3, 3, 3, 3])
    containers = [{"id": "abc123", "id_full": "abc123fullid", "name": "worker", "status": "running"}]
    asyncio.run(evaluate(make_metrics(), containers))
    assert count_open(fresh_db) == 1

    with Session(fresh_db) as s:
        primeiro_log_id = s.query(AlertLog).filter(AlertLog.metrica == "container_restart_loop").first().id

    # Container continua reiniciando no ciclo seguinte — mais pontos no
    # histórico, "aumentos" recalculado tende a ser diferente do ciclo
    # anterior (não faz sentido em produção o valor ficar estático enquanto
    # o container continua reiniciando de verdade).
    _add_container_metrics(fresh_db, "abc123", [3, 4, 4, 5, 5, 5, 5, 5, 5, 5])
    asyncio.run(evaluate(make_metrics(), containers))

    assert count_open(fresh_db) == 1
    with Session(fresh_db) as s:
        segundo_log = s.query(AlertLog).filter(AlertLog.metrica == "container_restart_loop").first()
    assert segundo_log.id == primeiro_log_id  # mesmo registro, não um novo


def test_restart_loop_nome_com_underscore_nao_casa_com_outro_container(fresh_db):
    """Container com "_" no nome (valido no Docker) nao pode casar, via LIKE,
    com o alerta aberto de um container DIFERENTE cujo nome so difere nessa
    posicao (ex: "web_1" vs "webX1") — "_" e curinga de 1 caractere no LIKE
    e precisa ser escapado."""
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_restart_loop", operador=">=", threshold=3, duracao_minutos=10, cooldown_minutos=30)

    _add_container_metrics(fresh_db, "aaa111", [0, 1, 1, 2, 2, 3, 3, 3, 3, 3])
    _add_container_metrics(fresh_db, "bbb222", [0, 1, 1, 2, 2, 3, 3, 3, 3, 3])
    containers = [
        {"id": "aaa111", "id_full": "aaa111full", "name": "webX1", "status": "running"},
        {"id": "bbb222", "id_full": "bbb222full", "name": "web_1", "status": "running"},
    ]
    asyncio.run(evaluate(make_metrics(), containers))

    with Session(fresh_db) as s:
        logs = s.query(AlertLog).filter(AlertLog.metrica == "container_restart_loop").all()
    assert len(logs) == 2  # dois containers distintos, dois alertas distintos
    nomes_nos_logs = {re.search(r"Container '(.+)' em restart loop", l.mensagem).group(1) for l in logs}
    assert nomes_nos_logs == {"webX1", "web_1"}


from unittest.mock import patch


def enable_channels(engine):
    with Session(engine) as s:
        s.merge(Config(key="smtp_enabled", value="1"))
        s.merge(Config(key="evolution_enabled", value="1"))
        s.commit()


def get_notifications(engine, alert_log_id=None):
    with Session(engine) as s:
        q = s.query(AlertNotification)
        if alert_log_id is not None:
            q = q.filter(AlertNotification.alert_log_id == alert_log_id)
        return q.all()


def test_alerta_flapping_ainda_notifica_antes_de_resolver(fresh_db):
    """Bug original: duracao_minutos=0 só notificava a partir do 2º ciclo
    do alerta aberto; se ele resolvesse no ciclo seguinte, nunca notificava."""
    from notifications.alert_engine import evaluate
    enable_channels(fresh_db)
    add_rule(fresh_db, threshold=80.0, metrica="disk_percent", operador=">",
             duracao_minutos=0, cooldown_minutos=120, canal_whatsapp=1, canal_email=0)
    with patch("notifications.whatsapp_service.send_alert") as mock_send:
        asyncio.run(evaluate(make_metrics(disk=90.0), []))
        with Session(fresh_db) as s:
            log = s.query(AlertLog).first()
        asyncio.run(evaluate(make_metrics(disk=70.0), []))  # resolve no ciclo seguinte
    disparos = [n for n in get_notifications(fresh_db, log.id) if n.tipo == "disparo"]
    assert len(disparos) == 1
    assert disparos[0].status == "enviado"
    assert disparos[0].canal == "whatsapp"
    mock_send.assert_called_once()


def test_canal_marcado_mas_desabilitado_globalmente_grava_status_desabilitado(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=80.0, metrica="disk_percent", operador=">",
             duracao_minutos=0, canal_whatsapp=1, canal_email=0)
    asyncio.run(evaluate(make_metrics(disk=90.0), []))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).first()
    notifs = get_notifications(fresh_db, log.id)
    assert len(notifs) == 1
    assert notifs[0].canal == "whatsapp"
    assert notifs[0].status == "desabilitado"
    assert notifs[0].erro is None


def test_erro_no_envio_grava_status_falhou_com_mensagem(fresh_db):
    from notifications.alert_engine import evaluate
    enable_channels(fresh_db)
    add_rule(fresh_db, threshold=80.0, metrica="disk_percent", operador=">",
             duracao_minutos=0, canal_whatsapp=1, canal_email=0)
    with patch("notifications.whatsapp_service.send_alert", side_effect=Exception("evolution indisponivel")):
        asyncio.run(evaluate(make_metrics(disk=90.0), []))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).first()
    notifs = get_notifications(fresh_db, log.id)
    assert notifs[0].status == "falhou"
    assert "evolution indisponivel" in notifs[0].erro


def test_canal_nao_marcado_na_regra_nao_gera_notificacao(fresh_db):
    from notifications.alert_engine import evaluate
    enable_channels(fresh_db)
    add_rule(fresh_db, threshold=80.0, metrica="disk_percent", operador=">",
             duracao_minutos=0, canal_whatsapp=0, canal_email=0)
    asyncio.run(evaluate(make_metrics(disk=90.0), []))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).first()
    assert get_notifications(fresh_db, log.id) == []


def test_resolucao_grava_notificacao_enviada(fresh_db):
    from notifications.alert_engine import evaluate
    enable_channels(fresh_db)
    add_rule(fresh_db, threshold=80.0, metrica="disk_percent", operador=">",
             duracao_minutos=0, cooldown_minutos=0, canal_whatsapp=1, canal_email=0)
    with patch("notifications.whatsapp_service.send_alert"):
        asyncio.run(evaluate(make_metrics(disk=90.0), []))
        with Session(fresh_db) as s:
            log = s.query(AlertLog).first()
        with patch("notifications.whatsapp_service.send_resolution") as mock_res:
            asyncio.run(evaluate(make_metrics(disk=70.0), []))
    resolucoes = [n for n in get_notifications(fresh_db, log.id) if n.tipo == "resolucao"]
    assert len(resolucoes) == 1
    assert resolucoes[0].status == "enviado"
    mock_res.assert_called_once()


def test_container_parado_notifica_ao_criar_alerta(fresh_db):
    from notifications.alert_engine import evaluate
    enable_channels(fresh_db)
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1,
             canal_whatsapp=1, canal_email=0)
    with patch("notifications.whatsapp_service.send_alert") as mock_send:
        asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "exited"}]))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.metrica == "container_stopped").first()
    notifs = get_notifications(fresh_db, log.id)
    assert len(notifs) == 1
    assert notifs[0].status == "enviado"
    assert notifs[0].tipo == "disparo"
    mock_send.assert_called_once()


def test_container_parado_notifica_resolucao(fresh_db):
    from notifications.alert_engine import evaluate
    enable_channels(fresh_db)
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1,
             canal_whatsapp=1, canal_email=0)
    with patch("notifications.whatsapp_service.send_alert"):
        asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "exited"}]))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.metrica == "container_stopped").first()
    with patch("notifications.whatsapp_service.send_resolution") as mock_res:
        asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "running"}]))
    resolucoes = [n for n in get_notifications(fresh_db, log.id) if n.tipo == "resolucao"]
    assert len(resolucoes) == 1
    assert resolucoes[0].status == "enviado"
    mock_res.assert_called_once()


def test_evaluate_rule_usa_extra_context_quando_fornecido(fresh_db):
    from notifications.alert_engine import _evaluate_rule
    from sqlalchemy.orm import Session
    import json

    rule_id = add_rule(fresh_db, threshold=500.0, metrica="docker_reclaimable_mb", operador=">")

    with Session(fresh_db) as s:
        rule = s.get(AlertRule, rule_id)
        _evaluate_rule(
            s, rule, 800.0, "teste", datetime.utcnow(), "VPS Teste", [],
            extra_context={"imagens_orfas": [{"repo_tag": "old:latest", "tamanho_mb": 800.0}]},
        )
        s.commit()

    with Session(fresh_db) as s:
        log = s.query(AlertLog).first()
    assert log is not None
    contexto = json.loads(log.contexto)
    assert contexto["imagens_orfas"][0]["repo_tag"] == "old:latest"
