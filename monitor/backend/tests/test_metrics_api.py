import pytest
import importlib
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
from sqlalchemy.orm import Session


@pytest.fixture
def auth_client(test_db, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth
    importlib.reload(api.auth)
    import api.metrics
    importlib.reload(api.metrics)
    import main
    importlib.reload(main)
    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, test_db


def test_current_metrics_vazio(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/current")
    assert r.status_code == 200


def test_history_retorna_dados(auth_client):
    client, db = auth_client
    now = datetime.utcnow()
    with Session(db.engine) as session:
        for i in range(5):
            session.add(db.MetricsHistory(
                collected_at=now - timedelta(minutes=i*5),
                cpu_percent=float(10 + i),
                ram_percent=float(50 + i),
                disk_percent=30.0,
            ))
        session.commit()
    r = client.get("/api/metrics/history?metric=cpu&hours=1")
    assert r.status_code == 200
    data = r.json()
    assert data["metric"] == "cpu"
    assert len(data["data"]) == 5
    assert "value" in data["data"][0]
    assert "ts" in data["data"][0]


def test_history_hours_invalido_usa_24(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/history?metric=cpu&hours=invalid")
    assert r.status_code == 422


def test_history_metrica_invalida(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/history?metric=inexistente&hours=1")
    assert r.status_code == 200


def test_sem_autenticacao_401(test_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth
    importlib.reload(api.auth)
    import api.metrics
    importlib.reload(api.metrics)
    import main
    importlib.reload(main)
    client = TestClient(main.app)

    # Sem token no header
    response = client.get("/api/metrics/current")
    assert response.status_code == 401

    response = client.get("/api/metrics/history")
    assert response.status_code == 401


def test_container_history_hour_agrega_media_por_bucket(auth_client):
    client, db = auth_client
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    with Session(db.engine) as session:
        session.add(db.ContainerMetrics(
            collected_at=now, container_id="abc", container_name="circuitodascorridas-app",
            cpu_percent=10.0, mem_percent=40.0, net_rx_mb=1.0, net_tx_mb=0.5,
        ))
        session.add(db.ContainerMetrics(
            collected_at=now + timedelta(minutes=10), container_id="abc", container_name="circuitodascorridas-app",
            cpu_percent=20.0, mem_percent=50.0, net_rx_mb=2.0, net_tx_mb=1.5,
        ))
        session.commit()

    r = client.get("/api/metrics/container-history?container_name=circuitodascorridas-app&granularity=hour")
    assert r.status_code == 200
    data = r.json()
    assert data["granularity"] == "hour"
    last_bucket = data["data"][-1]
    assert last_bucket["cpu_percent"] == 15.0
    assert last_bucket["mem_percent"] == 45.0


def test_container_history_bucket_sem_amostra_retorna_null(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/container-history?container_name=inexistente&granularity=hour")
    data = r.json()
    assert all(p["cpu_percent"] is None for p in data["data"])


def test_container_history_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/metrics/container-history?container_name=x").status_code == 401


def test_history_de_load_inclui_serie_companheira(auth_client):
    client, db = auth_client
    with Session(db.engine) as session:
        session.add(db.MetricsHistory(collected_at=datetime.utcnow(), load_1m=7.0, load_5m=5.5))
        session.commit()
    r = client.get("/api/metrics/history?metric=load&hours=1")
    assert r.status_code == 200
    body = r.json()
    assert body["companion"] == "load_5m"
    assert body["data"][0]["value"] == 7.0
    assert body["data"][0]["value2"] == 5.5


def test_history_de_cpu_nao_tem_companheira(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/history?metric=cpu&hours=1")
    assert r.json()["companion"] is None


def test_annotations_devolve_threshold_e_alertas(auth_client):
    client, db = auth_client
    with Session(db.engine) as session:
        session.add(db.AlertRule(nome="Load Alto", metrica="load_1m", operador=">",
                        threshold=6.0, duracao_minutos=3, severidade="aviso",
                        cooldown_minutos=30, ativo=1))
        session.add(db.AlertLog(triggered_at=datetime.utcnow(), severidade="aviso",
                       metrica="load_1m", valor_no_disparo=7.3, threshold=6.0,
                       mensagem="Load Alto: 7.3 > 6.0"))
        session.commit()
    body = client.get("/api/metrics/history/annotations?metric=load&hours=24").json()
    assert body["threshold"] == 6.0
    assert body["regra"] == "Load Alto"
    assert len(body["alertas"]) == 1
    assert body["alertas"][0]["valor"] == 7.3


def test_annotations_sem_regra_devolve_null(auth_client):
    client, _ = auth_client
    body = client.get("/api/metrics/history/annotations?metric=net_rx&hours=24").json()
    assert body["threshold"] is None
    assert body["alertas"] == []


def test_annotations_escolhe_a_primeira_linha_que_o_usuario_cruza(auth_client):
    client, db = auth_client
    with Session(db.engine) as session:
        session.add(db.AlertRule(nome="CPU Critica", metrica="cpu_percent", operador=">",
                        threshold=95.0, duracao_minutos=2, severidade="critico",
                        cooldown_minutos=15, ativo=1))
        session.add(db.AlertRule(nome="CPU Alta", metrica="cpu_percent", operador=">",
                        threshold=80.0, duracao_minutos=5, severidade="aviso",
                        cooldown_minutos=30, ativo=1))
        session.commit()
    body = client.get("/api/metrics/history/annotations?metric=cpu&hours=24").json()
    assert body["threshold"] == 80.0  # a menor, para operador ">"


# net_rx/net_tx sao as unicas metricas de METRIC_MAP sem regra padrao
# semeada por init_db() (ver models.database._DEFAULT_RULES) — usadas aqui
# para testar os ramos de _regra_primeira_linha isolados, sem uma regra
# default de outro operador entrando na mistura sem querer.


def test_annotations_operador_menor_escolhe_a_maior_threshold(auth_client):
    client, db = auth_client
    with Session(db.engine) as session:
        session.add(db.AlertRule(nome="Net RX Baixo", metrica="net_rx_bytes_s", operador="<",
                        threshold=1000.0, duracao_minutos=5, severidade="aviso",
                        cooldown_minutos=30, ativo=1))
        session.add(db.AlertRule(nome="Net RX Muito Baixo", metrica="net_rx_bytes_s", operador="<",
                        threshold=2000.0, duracao_minutos=5, severidade="critico",
                        cooldown_minutos=15, ativo=1))
        session.commit()
    body = client.get("/api/metrics/history/annotations?metric=net_rx&hours=24").json()
    assert body["threshold"] == 2000.0  # a maior, para operador "<" (primeira cruzada descendo)
    assert body["regra"] == "Net RX Muito Baixo"


def test_annotations_operador_menor_empate_de_threshold_escolhe_menor_id(auth_client):
    client, db = auth_client
    with Session(db.engine) as session:
        session.add(db.AlertRule(nome="Net TX Baixo A", metrica="net_tx_bytes_s", operador="<",
                        threshold=500.0, duracao_minutos=5, severidade="aviso",
                        cooldown_minutos=30, ativo=1))
        session.add(db.AlertRule(nome="Net TX Baixo B", metrica="net_tx_bytes_s", operador="<",
                        threshold=500.0, duracao_minutos=10, severidade="critico",
                        cooldown_minutos=15, ativo=1))
        session.commit()
    body = client.get("/api/metrics/history/annotations?metric=net_tx&hours=24").json()
    assert body["threshold"] == 500.0
    assert body["regra"] == "Net TX Baixo A"  # empate de threshold -> menor id


def test_annotations_operadores_mistos_escolhe_menor_id(auth_client):
    client, db = auth_client
    with Session(db.engine) as session:
        session.add(db.AlertRule(nome="Net RX Alto", metrica="net_rx_bytes_s", operador=">",
                        threshold=8000.0, duracao_minutos=5, severidade="critico",
                        cooldown_minutos=15, ativo=1))
        session.add(db.AlertRule(nome="Net RX Baixo", metrica="net_rx_bytes_s", operador="<",
                        threshold=100.0, duracao_minutos=5, severidade="aviso",
                        cooldown_minutos=30, ativo=1))
        session.commit()
    body = client.get("/api/metrics/history/annotations?metric=net_rx&hours=24").json()
    assert body["regra"] == "Net RX Alto"  # operadores mistos -> fallback pelo menor id
    assert body["threshold"] == 8000.0


def test_annotations_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/metrics/history/annotations?metric=cpu").status_code == 401
