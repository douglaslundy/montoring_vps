import os
import pytest
import importlib
from fastapi.testclient import TestClient


@pytest.fixture
def auth_client(test_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    monkeypatch.setenv("TRAEFIK_DYNAMIC_DIR", str(tmp_path / "dynamic"))
    (tmp_path / "dynamic").mkdir()

    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth as auth_mod
    importlib.reload(auth_mod)
    import api.traefik as traefik_mod
    importlib.reload(traefik_mod)
    import main
    importlib.reload(main)

    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


VALID_YAML = (
    "http:\n"
    "  routers:\n"
    "    exemplo:\n"
    "      rule: \"Host(`exemplo.dlsistemas.com.br`)\"\n"
    "      service: exemplo\n"
    "  services:\n"
    "    exemplo:\n"
    "      loadBalancer:\n"
    "        servers:\n"
    "          - url: \"http://172.17.0.1:9000\"\n"
)

INVALID_YAML = "http:\n  routers:\n    exemplo rule: [\n"


def test_listar_rotas(auth_client):
    dynamic_dir = os.environ["TRAEFIK_DYNAMIC_DIR"]
    with open(os.path.join(dynamic_dir, "mecanicapro.yml"), "w") as f:
        f.write(VALID_YAML)
    with open(os.path.join(dynamic_dir, "vps-monitor-teste.yml"), "w") as f:
        f.write(VALID_YAML)

    r = auth_client.get("/api/traefik/routes")
    assert r.status_code == 200
    rotas = {item["filename"]: item for item in r.json()}
    assert rotas["mecanicapro.yml"]["managed"] is False
    assert rotas["vps-monitor-teste.yml"]["managed"] is True
    assert rotas["mecanicapro.yml"]["content"] == VALID_YAML


def test_criar_rota_sucesso(auth_client, test_db):
    r = auth_client.post("/api/traefik/routes", json={
        "nome_exibicao": "Teste de Rota",
        "yaml_content": VALID_YAML,
    })
    assert r.status_code == 201
    assert r.json()["filename"] == "vps-monitor-teste-de-rota.yml"

    dynamic_dir = os.environ["TRAEFIK_DYNAMIC_DIR"]
    path = os.path.join(dynamic_dir, "vps-monitor-teste-de-rota.yml")
    assert os.path.exists(path)
    with open(path) as f:
        assert f.read() == VALID_YAML

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        log = session.query(test_db.TraefikActionLog).first()
    assert log.acao == "create"
    assert log.sucesso == 1


def test_criar_rota_yaml_invalido(auth_client):
    r = auth_client.post("/api/traefik/routes", json={
        "nome_exibicao": "Rota Invalida",
        "yaml_content": INVALID_YAML,
    })
    assert r.status_code == 400

    dynamic_dir = os.environ["TRAEFIK_DYNAMIC_DIR"]
    assert not os.path.exists(os.path.join(dynamic_dir, "vps-monitor-rota-invalida.yml"))


def test_criar_rota_duplicada(auth_client):
    auth_client.post("/api/traefik/routes", json={"nome_exibicao": "Duplicada", "yaml_content": VALID_YAML})
    r = auth_client.post("/api/traefik/routes", json={"nome_exibicao": "Duplicada", "yaml_content": VALID_YAML})
    assert r.status_code == 409


def test_editar_rota_bloqueia_sem_prefixo(auth_client):
    r = auth_client.put("/api/traefik/routes/mecanicapro.yml", json={"yaml_content": VALID_YAML})
    assert r.status_code == 403


def test_editar_rota_nao_encontrada(auth_client):
    r = auth_client.put("/api/traefik/routes/vps-monitor-nao-existe.yml", json={"yaml_content": VALID_YAML})
    assert r.status_code == 404


def test_editar_rota_yaml_invalido_nao_altera_arquivo(auth_client):
    auth_client.post("/api/traefik/routes", json={"nome_exibicao": "Original", "yaml_content": VALID_YAML})
    r = auth_client.put("/api/traefik/routes/vps-monitor-original.yml", json={"yaml_content": INVALID_YAML})
    assert r.status_code == 400

    dynamic_dir = os.environ["TRAEFIK_DYNAMIC_DIR"]
    with open(os.path.join(dynamic_dir, "vps-monitor-original.yml")) as f:
        assert f.read() == VALID_YAML


def test_editar_rota_sucesso(auth_client, test_db):
    auth_client.post("/api/traefik/routes", json={"nome_exibicao": "Editar Mim", "yaml_content": VALID_YAML})
    novo_conteudo = VALID_YAML.replace("exemplo.dlsistemas.com.br", "novo.dlsistemas.com.br")
    r = auth_client.put("/api/traefik/routes/vps-monitor-editar-mim.yml", json={"yaml_content": novo_conteudo})
    assert r.status_code == 200

    dynamic_dir = os.environ["TRAEFIK_DYNAMIC_DIR"]
    with open(os.path.join(dynamic_dir, "vps-monitor-editar-mim.yml")) as f:
        assert f.read() == novo_conteudo

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        log = session.query(test_db.TraefikActionLog).filter_by(acao="edit").first()
    assert log is not None
    assert log.filename == "vps-monitor-editar-mim.yml"


def test_excluir_rota_bloqueia_sem_prefixo(auth_client):
    r = auth_client.delete("/api/traefik/routes/mecanicapro.yml")
    assert r.status_code == 403


def test_excluir_rota_nao_encontrada(auth_client):
    r = auth_client.delete("/api/traefik/routes/vps-monitor-nao-existe.yml")
    assert r.status_code == 404


def test_excluir_rota_sucesso(auth_client, test_db):
    auth_client.post("/api/traefik/routes", json={"nome_exibicao": "Excluir Mim", "yaml_content": VALID_YAML})
    r = auth_client.delete("/api/traefik/routes/vps-monitor-excluir-mim.yml")
    assert r.status_code == 200

    dynamic_dir = os.environ["TRAEFIK_DYNAMIC_DIR"]
    assert not os.path.exists(os.path.join(dynamic_dir, "vps-monitor-excluir-mim.yml"))

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        log = session.query(test_db.TraefikActionLog).filter_by(acao="delete").first()
    assert log is not None


def test_traefik_endpoints_sem_autenticacao_401():
    import main
    client = TestClient(main.app)
    assert client.get("/api/traefik/routes").status_code == 401
    assert client.post("/api/traefik/routes", json={}).status_code == 401
    assert client.put("/api/traefik/routes/vps-monitor-x.yml", json={}).status_code == 401
    assert client.delete("/api/traefik/routes/vps-monitor-x.yml").status_code == 401
