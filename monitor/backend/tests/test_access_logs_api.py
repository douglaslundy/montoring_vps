import importlib
import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import AsyncMock, patch


@pytest.fixture
def auth_client(test_db, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth
    importlib.reload(api.auth)
    import api.access_logs
    importlib.reload(api.access_logs)
    import main
    importlib.reload(main)
    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, test_db


def _seed_daily(db, day, ip, sistema, count):
    with Session(db.engine) as session:
        session.add(db.AccessLogDaily(day=day, ip=ip, sistema=sistema, count=count))
        session.commit()


def test_summary_agrega_por_ip(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 5)
    _seed_daily(db, today, "203.0.113.10", "monitor.dlsistemas.com.br", 2)
    _seed_daily(db, today, "198.51.100.20", "app2.dlsistemas.com.br", 1)

    r = client.get("/api/access-logs/summary?days=7")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    top = data[0]
    assert top["ip"] == "203.0.113.10"
    assert top["total_acessos"] == 7
    assert {s["sistema"] for s in top["sistemas"]} == {"app2.dlsistemas.com.br", "monitor.dlsistemas.com.br"}


def test_summary_filtra_por_sistema(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 5)
    _seed_daily(db, today, "203.0.113.10", "monitor.dlsistemas.com.br", 2)

    r = client.get("/api/access-logs/summary?days=7&sistema=monitor.dlsistemas.com.br")
    data = r.json()
    assert len(data) == 1
    assert data[0]["total_acessos"] == 2


def test_summary_filtra_por_ip_prefixo(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 5)
    _seed_daily(db, today, "198.51.100.20", "app2.dlsistemas.com.br", 1)

    r = client.get("/api/access-logs/summary?days=7&ip=203.0.113")
    data = r.json()
    assert len(data) == 1
    assert data[0]["ip"] == "203.0.113.10"


def test_summary_ignora_dias_fora_da_janela(auth_client):
    client, db = auth_client
    old_day = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d")
    _seed_daily(db, old_day, "203.0.113.10", "app2.dlsistemas.com.br", 5)

    r = client.get("/api/access-logs/summary?days=7")
    assert r.json() == []


def test_sistemas_retorna_lista_distinta(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 5)
    _seed_daily(db, today, "198.51.100.20", "monitor.dlsistemas.com.br", 1)

    r = client.get("/api/access-logs/sistemas")
    assert r.status_code == 200
    assert set(r.json()) == {"app2.dlsistemas.com.br", "monitor.dlsistemas.com.br"}


def test_ip_detail_retorna_geo_e_recentes(auth_client):
    client, db = auth_client
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 3)
    with Session(db.engine) as session:
        session.add(db.AccessLog(
            accessed_at=now, ip="203.0.113.10", sistema="app2.dlsistemas.com.br",
            path="/api/pedidos", method="GET", status_code=200,
        ))
        session.commit()

    fake_geo = {"is_private": False, "country": "Brazil", "region": "SP", "city": "São Paulo", "isp": "X", "org": "Y", "lat": -23.5, "lon": -46.6}
    with patch("api.access_logs.lookup_ip", AsyncMock(return_value=fake_geo)):
        r = client.get("/api/access-logs/ip/203.0.113.10?days=7")

    assert r.status_code == 200
    data = r.json()
    assert data["ip"] == "203.0.113.10"
    assert data["geo"]["country"] == "Brazil"
    assert data["total_acessos"] == 3
    assert len(data["acessos_recentes"]) == 1
    assert data["acessos_recentes"][0]["path"] == "/api/pedidos"


def test_endpoints_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/access-logs/summary").status_code == 401
    assert client.get("/api/access-logs/sistemas").status_code == 401
    assert client.get("/api/access-logs/ip/203.0.113.10").status_code == 401


def test_summary_por_sistema_agrega_por_sistema_e_ip(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "circuitodascorridas.dlsistemas.com.br", 5)
    _seed_daily(db, today, "198.51.100.20", "circuitodascorridas.dlsistemas.com.br", 3)
    _seed_daily(db, today, "203.0.113.10", "monitor.dlsistemas.com.br", 2)

    r = client.get("/api/access-logs/summary-por-sistema?days=7")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    top = data[0]
    assert top["sistema"] == "circuitodascorridas.dlsistemas.com.br"
    assert top["total_acessos"] == 8
    assert top["ips"][0]["ip"] == "203.0.113.10"
    assert top["ips"][0]["count"] == 5


def test_summary_por_sistema_filtra_por_ip_prefixo(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "circuitodascorridas.dlsistemas.com.br", 5)
    _seed_daily(db, today, "198.51.100.20", "circuitodascorridas.dlsistemas.com.br", 3)

    r = client.get("/api/access-logs/summary-por-sistema?days=7&ip=203.0.113")
    data = r.json()
    assert len(data) == 1
    assert data[0]["total_acessos"] == 5
    assert len(data[0]["ips"]) == 1
    assert data[0]["ips"][0]["ip"] == "203.0.113.10"


def test_summary_por_sistema_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/access-logs/summary-por-sistema").status_code == 401


def test_container_para_sistema_acha_pelo_host_label(auth_client):
    client, _ = auth_client
    fake_containers = [
        {
            "Id": "abc123def456",
            "Names": ["/circuitodascorridas-app"],
            "Labels": {
                "traefik.enable": "true",
                "traefik.http.routers.circuitodascorridas.rule": "Host(`circuitodascorridas.dlsistemas.com.br`)",
            },
        },
        {"Id": "def456abc123", "Names": ["/outro"], "Labels": {}},
    ]
    with patch("api.access_logs.docker_client") as mock_dc:
        mock_dc.list_containers = AsyncMock(return_value=fake_containers)
        r = client.get("/api/access-logs/container-para-sistema?sistema=circuitodascorridas.dlsistemas.com.br")
    assert r.status_code == 200
    assert r.json() == {"container_name": "circuitodascorridas-app"}


def test_container_para_sistema_multiplos_hosts_na_mesma_regra(auth_client):
    client, _ = auth_client
    fake_containers = [
        {
            "Id": "abc123def456",
            "Names": ["/app-multi"],
            "Labels": {
                "traefik.http.routers.multi.rule": "Host(`a.dlsistemas.com.br`) || Host(`b.dlsistemas.com.br`)",
            },
        },
    ]
    with patch("api.access_logs.docker_client") as mock_dc:
        mock_dc.list_containers = AsyncMock(return_value=fake_containers)
        r = client.get("/api/access-logs/container-para-sistema?sistema=b.dlsistemas.com.br")
    assert r.json() == {"container_name": "app-multi"}


def test_container_para_sistema_nao_encontrado_retorna_null(auth_client):
    client, _ = auth_client
    with patch("api.access_logs.docker_client") as mock_dc:
        mock_dc.list_containers = AsyncMock(return_value=[])
        r = client.get("/api/access-logs/container-para-sistema?sistema=inexistente.com.br")
    assert r.json() == {"container_name": None}


def test_container_para_sistema_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/access-logs/container-para-sistema?sistema=x.com").status_code == 401


def _seed_hourly(db, hour, sistema, count):
    with Session(db.engine) as session:
        session.add(db.AccessLogHourly(hour=hour, sistema=sistema, count=count))
        session.commit()


def test_timeseries_hour_ultimas_12h(auth_client):
    client, db = auth_client
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    _seed_hourly(db, now.strftime("%Y-%m-%d %H"), "app2.dlsistemas.com.br", 7)
    _seed_hourly(db, (now - timedelta(hours=1)).strftime("%Y-%m-%d %H"), "app2.dlsistemas.com.br", 3)

    r = client.get("/api/access-logs/timeseries?sistema=app2.dlsistemas.com.br&granularity=hour")
    assert r.status_code == 200
    data = r.json()
    assert data["granularity"] == "hour"
    assert len(data["data"]) == 12
    assert data["data"][-1]["value"] == 7
    assert data["data"][-2]["value"] == 3
    assert data["data"][0]["value"] == 0


def test_timeseries_hour_dia_especifico(auth_client):
    client, db = auth_client
    day_dt = datetime.utcnow() - timedelta(days=2)
    day = day_dt.strftime("%Y-%m-%d")
    _seed_hourly(db, f"{day} 09", "app2.dlsistemas.com.br", 4)
    _seed_hourly(db, f"{day} 15", "app2.dlsistemas.com.br", 6)

    r = client.get(f"/api/access-logs/timeseries?sistema=app2.dlsistemas.com.br&granularity=hour&day={day}")
    data = r.json()
    assert len(data["data"]) == 24
    assert data["data"][9]["value"] == 4
    assert data["data"][15]["value"] == 6
    assert data["data"][0]["value"] == 0


def test_timeseries_day_mes_passado_completo(auth_client):
    client, db = auth_client
    now = datetime.utcnow()
    ultimo_dia_mes_anterior = now.replace(day=1) - timedelta(days=1)
    mes_anterior = ultimo_dia_mes_anterior.strftime("%Y-%m")
    dias_no_mes = ultimo_dia_mes_anterior.day

    _seed_daily(db, f"{mes_anterior}-01", "203.0.113.10", "app2.dlsistemas.com.br", 10)
    _seed_daily(db, ultimo_dia_mes_anterior.strftime("%Y-%m-%d"), "203.0.113.10", "app2.dlsistemas.com.br", 20)

    r = client.get(f"/api/access-logs/timeseries?sistema=app2.dlsistemas.com.br&granularity=day&month={mes_anterior}")
    data = r.json()
    assert data["granularity"] == "day"
    assert len(data["data"]) == dias_no_mes
    primeiro = next(d for d in data["data"] if d["ts"] == f"{mes_anterior}-01")
    assert primeiro["value"] == 10
    ultimo = next(d for d in data["data"] if d["ts"] == ultimo_dia_mes_anterior.strftime("%Y-%m-%d"))
    assert ultimo["value"] == 20


def test_timeseries_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/access-logs/timeseries?sistema=x.com").status_code == 401
