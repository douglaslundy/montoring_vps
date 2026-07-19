import os
import pytest
import importlib
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _garante_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")


@pytest.fixture
def auth_client(test_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    monkeypatch.setenv("BACKUPS_DIR", str(tmp_path / "backups"))
    (tmp_path / "backups").mkdir()

    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth as auth_mod
    importlib.reload(auth_mod)
    import api.backups as backups_mod
    importlib.reload(backups_mod)
    import main
    importlib.reload(main)

    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _metrics_stub():
    return {
        "containers": [
            {"name": "mecanicapro-backend-1", "labels": {"com.docker.compose.project": "mecanicapro"}},
            {"name": "corridas-app", "labels": {"com.docker.compose.project": "corridas"}},
            {"name": "orfao", "labels": {}},
        ],
    }


def _criar_snapshot_arquivo(tmp_path, projeto, nome_arquivo, conteudo=b"dados"):
    destino = tmp_path / "backups" / projeto
    destino.mkdir(parents=True, exist_ok=True)
    (destino / nome_arquivo).write_bytes(conteudo)


def test_validar_nome_rejeita_caracteres_invalidos():
    from api.backups import _validar_nome
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _validar_nome("../etc", "Nome do projeto")
    assert exc.value.status_code == 400


def test_validar_arquivo_rejeita_nome_fora_do_padrao():
    from api.backups import _validar_arquivo
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _validar_arquivo("../../etc/passwd")
    assert exc.value.status_code == 400


def test_listar_projetos_agrupa_e_inclui_schedule_e_snapshots(auth_client, tmp_path, test_db):
    _criar_snapshot_arquivo(tmp_path, "mecanicapro", "20260101T000000Z.tar.gz")

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        session.add(test_db.BackupSchedule(projeto="mecanicapro", frequencia="daily", hora=4))
        session.commit()

    with patch("collector.scheduler._last_metrics", _metrics_stub()):
        r = auth_client.get("/api/backups/projects")

    assert r.status_code == 200
    projetos = {p["nome"]: p for p in r.json()["projects"]}
    assert set(projetos.keys()) == {"mecanicapro", "corridas"}
    assert projetos["mecanicapro"]["frequencia"] == "daily"
    assert projetos["mecanicapro"]["hora"] == 4
    assert len(projetos["mecanicapro"]["snapshots"]) == 1
    assert projetos["mecanicapro"]["snapshots"][0]["arquivo"] == "20260101T000000Z.tar.gz"
    assert projetos["corridas"]["frequencia"] == "off"
    assert projetos["corridas"]["snapshots"] == []
    assert projetos["mecanicapro"]["job_ativo"] is None


def test_listar_projetos_nome_fora_do_padrao_nao_quebra_listagem(auth_client):
    metrics_com_nome_estranho = {
        "containers": [
            {"name": "algo", "labels": {"com.docker.compose.project": "projeto com espaco"}},
        ],
    }
    with patch("collector.scheduler._last_metrics", metrics_com_nome_estranho):
        r = auth_client.get("/api/backups/projects")

    assert r.status_code == 200
    projetos = {p["nome"]: p for p in r.json()["projects"]}
    assert projetos["projeto com espaco"]["snapshots"] == []


def test_listar_projetos_inclui_job_ativo(auth_client, test_db):
    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        session.add(test_db.BackupJob(projeto="mecanicapro", tipo="snapshot", status="running", username="admin"))
        session.commit()

    with patch("collector.scheduler._last_metrics", _metrics_stub()):
        r = auth_client.get("/api/backups/projects")

    projetos = {p["nome"]: p for p in r.json()["projects"]}
    assert projetos["mecanicapro"]["job_ativo"]["tipo"] == "snapshot"
    assert projetos["mecanicapro"]["job_ativo"]["status"] == "running"


def test_definir_schedule_cria_novo(auth_client, test_db):
    r = auth_client.put("/api/backups/projects/mecanicapro/schedule", json={"frequencia": "daily", "hora": 5})
    assert r.status_code == 200

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        schedule = session.get(test_db.BackupSchedule, "mecanicapro")
    assert schedule.frequencia == "daily"
    assert schedule.hora == 5


def test_definir_schedule_atualiza_existente(auth_client, test_db):
    auth_client.put("/api/backups/projects/mecanicapro/schedule", json={"frequencia": "daily", "hora": 5})
    r = auth_client.put("/api/backups/projects/mecanicapro/schedule", json={"frequencia": "weekly", "hora": 2})
    assert r.status_code == 200

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        schedules = session.query(test_db.BackupSchedule).filter_by(projeto="mecanicapro").all()
    assert len(schedules) == 1
    assert schedules[0].frequencia == "weekly"
    assert schedules[0].hora == 2


def test_definir_schedule_frequencia_invalida(auth_client):
    r = auth_client.put("/api/backups/projects/mecanicapro/schedule", json={"frequencia": "mensal", "hora": 3})
    assert r.status_code == 400


def test_definir_schedule_hora_invalida(auth_client):
    r = auth_client.put("/api/backups/projects/mecanicapro/schedule", json={"frequencia": "daily", "hora": 25})
    assert r.status_code == 400


def test_criar_snapshot_sucesso(auth_client, test_db):
    r = auth_client.post("/api/backups/projects/mecanicapro/snapshot")
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        job = session.get(test_db.BackupJob, job_id)
    assert job.projeto == "mecanicapro"
    assert job.tipo == "snapshot"
    assert job.status == "pending"


def test_criar_snapshot_bloqueia_se_ja_existe_job_ativo(auth_client):
    auth_client.post("/api/backups/projects/mecanicapro/snapshot")
    r = auth_client.post("/api/backups/projects/mecanicapro/snapshot")
    assert r.status_code == 409


def test_restore_sucesso(auth_client, tmp_path, test_db):
    _criar_snapshot_arquivo(tmp_path, "mecanicapro", "20260101T000000Z.tar.gz")
    r = auth_client.post("/api/backups/projects/mecanicapro/snapshots/20260101T000000Z.tar.gz/restore")
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        job = session.get(test_db.BackupJob, job_id)
    assert job.tipo == "restore"
    assert job.arquivo == "20260101T000000Z.tar.gz"


def test_restore_arquivo_nao_encontrado(auth_client):
    r = auth_client.post("/api/backups/projects/mecanicapro/snapshots/nao-existe.tar.gz/restore")
    assert r.status_code == 404


def test_restore_bloqueia_se_ja_existe_job_ativo(auth_client, tmp_path):
    _criar_snapshot_arquivo(tmp_path, "mecanicapro", "20260101T000000Z.tar.gz")
    auth_client.post("/api/backups/projects/mecanicapro/snapshot")
    r = auth_client.post("/api/backups/projects/mecanicapro/snapshots/20260101T000000Z.tar.gz/restore")
    assert r.status_code == 409


def test_download_snapshot_sucesso(auth_client, tmp_path):
    _criar_snapshot_arquivo(tmp_path, "mecanicapro", "20260101T000000Z.tar.gz", conteudo=b"conteudo-teste")
    r = auth_client.get("/api/backups/projects/mecanicapro/snapshots/20260101T000000Z.tar.gz/download")
    assert r.status_code == 200
    assert r.content == b"conteudo-teste"


def test_download_snapshot_nao_encontrado(auth_client):
    r = auth_client.get("/api/backups/projects/mecanicapro/snapshots/nao-existe.tar.gz/download")
    assert r.status_code == 404


def test_excluir_snapshot_sucesso(auth_client, tmp_path, test_db):
    _criar_snapshot_arquivo(tmp_path, "mecanicapro", "20260101T000000Z.tar.gz")
    r = auth_client.delete("/api/backups/projects/mecanicapro/snapshots/20260101T000000Z.tar.gz")
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        job = session.get(test_db.BackupJob, job_id)
    assert job.tipo == "delete"
    assert job.arquivo == "20260101T000000Z.tar.gz"


def test_excluir_snapshot_nao_encontrado(auth_client):
    r = auth_client.delete("/api/backups/projects/mecanicapro/snapshots/nao-existe.tar.gz")
    assert r.status_code == 404


def test_backups_endpoints_sem_autenticacao_401():
    import main
    client = TestClient(main.app)
    assert client.get("/api/backups/projects").status_code == 401
    assert client.put("/api/backups/projects/mecanicapro/schedule", json={"frequencia": "off", "hora": 3}).status_code == 401
    assert client.post("/api/backups/projects/mecanicapro/snapshot").status_code == 401
    assert client.post("/api/backups/projects/mecanicapro/snapshots/x.tar.gz/restore").status_code == 401
    assert client.get("/api/backups/projects/mecanicapro/snapshots/x.tar.gz/download").status_code == 401
    assert client.delete("/api/backups/projects/mecanicapro/snapshots/x.tar.gz").status_code == 401
