import os
import pytest
import importlib
from unittest.mock import patch
from fastapi.testclient import TestClient


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
