import os
import json
import pytest
import importlib
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _garante_jwt_secret(monkeypatch):
    # api.projects importa (transitivamente, via collector.scheduler -> ws.stream)
    # api.auth, que levanta RuntimeError no import se JWT_SECRET nao estiver
    # setado. Quando este arquivo roda dentro da suite completa, algum teste
    # anterior ja deixou api.auth importado com JWT_SECRET setado (modulo fica
    # cacheado em sys.modules) e isso passa despercebido. Rodando este arquivo
    # isolado, os testes que importam api.projects diretamente (sem passar
    # pela fixture auth_client) sao os primeiros a disparar essa cadeia de
    # import e quebram. Este fixture autouse garante JWT_SECRET setado antes
    # do corpo de qualquer teste deste arquivo rodar, independente de ordem.
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")


@pytest.fixture
def auth_client(test_db, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")

    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth as auth_mod
    importlib.reload(auth_mod)
    import main
    importlib.reload(main)

    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ---------------------------------------------------------------------------
# _dominio_por_labels
# ---------------------------------------------------------------------------

def test_dominio_por_labels_encontra_host():
    from api.projects import _dominio_por_labels

    containers = [{
        "labels": {"traefik.http.routers.portainer.rule": "Host(`portainer.dlsistemas.com.br`)"},
    }]
    assert _dominio_por_labels(containers) == "portainer.dlsistemas.com.br"


def test_dominio_por_labels_nenhum_container_com_label():
    from api.projects import _dominio_por_labels

    containers = [{"labels": {"outra.label": "valor"}}, {"labels": {}}]
    assert _dominio_por_labels(containers) is None


# ---------------------------------------------------------------------------
# _dominio_por_arquivo_dinamico
# ---------------------------------------------------------------------------

def test_dominio_por_arquivo_dinamico_encontra_hostregexp(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAEFIK_DYNAMIC_DIR", str(tmp_path))
    import api.projects as projects_mod
    importlib.reload(projects_mod)

    (tmp_path / "mecanicapro.yml").write_text(
        'rule: "HostRegexp(`{subdomain:[a-z0-9-]+}.dlsistemas.com.br`)"',
        encoding="utf-8",
    )

    assert projects_mod._dominio_por_arquivo_dinamico("mecanicapro") == "{subdomain:[a-z0-9-]+}.dlsistemas.com.br"


def test_dominio_por_arquivo_dinamico_arquivo_inexistente(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAEFIK_DYNAMIC_DIR", str(tmp_path))
    import api.projects as projects_mod
    importlib.reload(projects_mod)

    assert projects_mod._dominio_por_arquivo_dinamico("projeto-sem-arquivo") is None


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------

def _metrics_stub():
    return {
        "ram": {"total_mb": 8000.0},
        "containers": [
            {
                "name": "mecanicapro-backend-1", "status": "running",
                "cpu_percent": 5.0, "mem_usage_mb": 100.0,
                "labels": {"com.docker.compose.project": "mecanicapro"},
            },
            {
                "name": "mecanicapro-frontend-1", "status": "running",
                "cpu_percent": 2.0, "mem_usage_mb": 50.0,
                "labels": {"com.docker.compose.project": "mecanicapro"},
            },
            {
                "name": "portainer", "status": "running",
                "cpu_percent": 1.0, "mem_usage_mb": 20.0,
                "labels": {
                    "com.docker.compose.project": "traefik",
                    "traefik.http.routers.portainer.rule": "Host(`portainer.dlsistemas.com.br`)",
                },
            },
            {
                "name": "container-orfao", "status": "running",
                "cpu_percent": 0.5, "mem_usage_mb": 10.0,
                "labels": {},
            },
        ],
    }


def test_agrupa_por_projeto_sem_misturar(auth_client, tmp_path, monkeypatch):
    monkeypatch.setenv("TRAEFIK_DYNAMIC_DIR", str(tmp_path))
    import api.projects as projects_mod
    importlib.reload(projects_mod)
    import main
    importlib.reload(main)
    client = TestClient(main.app)
    client.headers.update(auth_client.headers)

    with patch("collector.scheduler._last_metrics", _metrics_stub()):
        r = client.get("/api/projects")

    assert r.status_code == 200
    projetos = {p["nome"]: p for p in r.json()["projects"]}
    assert set(projetos.keys()) == {"mecanicapro", "traefik", "(sem projeto)"}
    assert projetos["mecanicapro"]["container_count"] == 2
    assert projetos["mecanicapro"]["cpu_percent"] == pytest.approx(7.0)
    assert projetos["mecanicapro"]["mem_usage_mb"] == pytest.approx(150.0)
    assert projetos["mecanicapro"]["mem_percent_do_host"] == pytest.approx(150.0 / 8000.0 * 100, abs=0.01)
    assert projetos["traefik"]["dominio"] == "portainer.dlsistemas.com.br"
    assert projetos["mecanicapro"]["dominio"] is None
    assert projetos["(sem projeto)"]["container_count"] == 1


def test_dominio_via_arquivo_dinamico_quando_sem_label(auth_client, tmp_path, monkeypatch):
    monkeypatch.setenv("TRAEFIK_DYNAMIC_DIR", str(tmp_path))
    (tmp_path / "mecanicapro.yml").write_text(
        'rule: "HostRegexp(`{subdomain:[a-z0-9-]+}.dlsistemas.com.br`)"',
        encoding="utf-8",
    )
    import api.projects as projects_mod
    importlib.reload(projects_mod)
    import main
    importlib.reload(main)
    client = TestClient(main.app)
    client.headers.update(auth_client.headers)

    with patch("collector.scheduler._last_metrics", _metrics_stub()):
        r = client.get("/api/projects")

    projetos = {p["nome"]: p for p in r.json()["projects"]}
    assert projetos["mecanicapro"]["dominio"] == "{subdomain:[a-z0-9-]+}.dlsistemas.com.br"


def test_sem_autenticacao_401():
    import main
    client = TestClient(main.app)
    r = client.get("/api/projects")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/projects/{projeto}/delete-preview
# ---------------------------------------------------------------------------

def _metrics_stub_com_id():
    return {
        "ram": {"total_mb": 8000.0},
        "containers": [
            {
                "id": "abc111", "id_full": "abc111full",
                "name": "mecanicapro-backend-1", "status": "running",
                "cpu_percent": 5.0, "mem_usage_mb": 100.0,
                "labels": {"com.docker.compose.project": "mecanicapro"},
            },
            {
                "id": "abc222", "id_full": "abc222full",
                "name": "mecanicapro-frontend-1", "status": "running",
                "cpu_percent": 2.0, "mem_usage_mb": 50.0,
                "labels": {"com.docker.compose.project": "mecanicapro"},
            },
        ],
    }


def test_delete_preview_bloqueia_vps_monitor(auth_client):
    r = auth_client.get("/api/projects/vps-monitor/delete-preview")
    assert r.status_code == 400


def test_delete_preview_404_projeto_inexistente(auth_client):
    with patch("collector.scheduler._last_metrics", _metrics_stub_com_id()):
        r = auth_client.get("/api/projects/projeto-fantasma/delete-preview")
    assert r.status_code == 404


def test_delete_preview_monta_containers_volumes_e_candidatas(auth_client, tmp_path, monkeypatch):
    monkeypatch.setenv("TRAEFIK_DYNAMIC_DIR", str(tmp_path))
    monkeypatch.setenv("FIREWALL_STATE_FILE", str(tmp_path / "firewall-state.json"))
    import api.firewall as firewall_mod
    importlib.reload(firewall_mod)
    import api.projects as projects_mod
    importlib.reload(projects_mod)
    import main
    importlib.reload(main)
    client = TestClient(main.app)
    client.headers.update(auth_client.headers)

    (tmp_path / "vps-monitor-mecanicapro.yml").write_text(
        'rule: "Host(`mecanicapro.dlsistemas.com.br`)"', encoding="utf-8",
    )
    (tmp_path / "firewall-state.json").write_text(json.dumps({"regras": [
        {"porta": 22, "protocolo": "tcp", "permitir": True, "origem_ip": None, "protegida": True},
        {"porta": 3000, "protocolo": "tcp", "permitir": True, "origem_ip": None, "protegida": False},
        {"porta": 9999, "protocolo": "tcp", "permitir": True, "origem_ip": None, "protegida": False},
    ]}), encoding="utf-8")

    async def _fake_inspect(container_id):
        base = {
            "abc111full": {
                "Mounts": [{"Type": "volume", "Name": "mecanicapro_dados"}],
                "NetworkSettings": {"Ports": {"3000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3000"}]}},
            },
            "abc222full": {
                "Mounts": [{"Type": "bind", "Source": "/host/x", "Destination": "/x"}],
                "NetworkSettings": {"Ports": {"80/tcp": None}},
            },
        }
        return base[container_id]

    with patch("collector.scheduler._last_metrics", _metrics_stub_com_id()), \
         patch.object(projects_mod.docker_client, "container_inspect", side_effect=_fake_inspect):
        r = client.get("/api/projects/mecanicapro/delete-preview")

    assert r.status_code == 200
    body = r.json()
    assert {c["name"] for c in body["containers"]} == {"mecanicapro-backend-1", "mecanicapro-frontend-1"}
    assert body["volumes"] == ["mecanicapro_dados"]
    assert body["rotas_candidatas"] == ["vps-monitor-mecanicapro.yml"]
    assert body["regras_firewall_candidatas"] == [
        {"porta": 3000, "protocolo": "tcp", "permitir": True, "origem_ip": None}
    ]


def test_delete_preview_sem_autenticacao_401():
    import main
    client = TestClient(main.app)
    r = client.get("/api/projects/mecanicapro/delete-preview")
    assert r.status_code == 401
