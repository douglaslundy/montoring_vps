# Backup/Restore de Projetos da VPS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Interface no monitor pra criar/agendar/restaurar/baixar snapshots (volumes Docker + diretório de trabalho) de qualquer projeto docker-compose da VPS, sem o container do monitor precisar acessar diretamente dados de outros projetos/clientes.

**Architecture:** Helper de agrupamento por projeto extraído de `api/projects.py` pra `api/_project_grouping.py` (reaproveitado). Dois modelos novos (`BackupSchedule`, `BackupJob`) — o backend só enfileira jobs, nunca toca em volumes/diretórios de outros projetos. Novo endpoint `api/backups.py` faz CRUD da fila de jobs e serve leitura/download de snapshots (diretório montado read-only). A execução real (parar containers, copiar volumes + diretório de trabalho, subir de novo) roda num script novo no host, `scripts/backup-worker.sh` (cron, mesmo padrão do fail2ban e do Traefik), que lê/escreve o SQLite do monitor direto no path do volume no host.

**Tech Stack:** FastAPI + SQLAlchemy + pytest (backend, TDD), Next.js/React/TypeScript (frontend, sem suíte de testes — build + verificação manual), bash + `docker`/`docker compose`/`tar`/`rsync`/`sqlite3` (script no host, CLI, sem teste automatizado).

## Global Constraints

- O container `monitor-backend` nunca acessa diretamente volumes ou diretórios de outros projetos — só grava/lê linhas em `backup_job`/`backup_schedule` no seu próprio SQLite. Toda execução real (tar, restore, docker compose stop/up) acontece no script do host.
- `projeto` e `arquivo` (nome de snapshot) são sempre validados por regex (`^[a-zA-Z0-9_-]+$` e `^[a-zA-Z0-9_-]+\.tar\.gz$`, respectivamente) antes de montar qualquer path — nunca aceitar `/`, `..` ou caminho absoluto.
- Só é permitido um `BackupJob` com `status` em `pending`/`running` por projeto ao mesmo tempo — uma nova tentativa (manual ou agendada) retorna 409 enquanto isso não mudar.
- Snapshot e restore **sempre param os containers do projeto** antes de mexer nos dados, e **sempre tentam subir de novo** (`docker compose up -d`) mesmo se a cópia/restore falhar no meio — nunca deixar um projeto de cliente parado indefinidamente por causa de uma falha do script.
- Retenção (manter os 5 snapshots mais recentes por projeto) só roda depois de um snapshot novo ter sucesso — nunca apaga o último snapshot bom se o novo falhou.
- `BACKUPS_DIR` (env var, default `/opt/vps-monitor-backups`) é montado **read-only** no `monitor-backend` — o container nunca escreve/apaga arquivos de snapshot diretamente, só o script no host.

---

### Task 1: Extrair `agrupar_por_projeto` compartilhado

**Files:**
- Create: `backend/api/_project_grouping.py`
- Modify: `backend/api/projects.py`
- Test: `backend/tests/test_project_grouping.py`

**Interfaces:**
- Produces: `def agrupar_por_projeto(containers: list[dict]) -> dict[str, list[dict]]` (agrupa pelo label `com.docker.compose.project`, default `"(sem projeto)"`). Consumido pela Task 3 (`api/backups.py`) e por `api/projects.py` (refatorado nesta task).

- [ ] **Step 1: Escrever os testes (devem falhar)**

Criar `backend/tests/test_project_grouping.py`:

```python
from api._project_grouping import agrupar_por_projeto


def test_agrupa_containers_pelo_label_do_projeto():
    containers = [
        {"name": "a", "labels": {"com.docker.compose.project": "mecanicapro"}},
        {"name": "b", "labels": {"com.docker.compose.project": "mecanicapro"}},
        {"name": "c", "labels": {"com.docker.compose.project": "corridas"}},
    ]
    grupos = agrupar_por_projeto(containers)
    assert set(grupos.keys()) == {"mecanicapro", "corridas"}
    assert [c["name"] for c in grupos["mecanicapro"]] == ["a", "b"]
    assert [c["name"] for c in grupos["corridas"]] == ["c"]


def test_container_sem_label_vai_pra_sem_projeto():
    containers = [{"name": "orfao", "labels": {}}]
    grupos = agrupar_por_projeto(containers)
    assert set(grupos.keys()) == {"(sem projeto)"}


def test_container_sem_chave_labels():
    containers = [{"name": "sem-labels"}]
    grupos = agrupar_por_projeto(containers)
    assert set(grupos.keys()) == {"(sem projeto)"}
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_project_grouping.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api._project_grouping'`

- [ ] **Step 3: Implementar o módulo**

Criar `backend/api/_project_grouping.py`:

```python
def agrupar_por_projeto(containers: list[dict]) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = {}
    for c in containers:
        projeto = (c.get("labels") or {}).get("com.docker.compose.project", "(sem projeto)")
        grupos.setdefault(projeto, []).append(c)
    return grupos
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_project_grouping.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Refatorar `api/projects.py` pra usar o helper compartilhado**

Em `backend/api/projects.py`, adicionar o import no topo (junto aos outros):

```python
from api._project_grouping import agrupar_por_projeto
```

Substituir, dentro de `list_projects()`:

```python
    grupos: dict[str, list[dict]] = {}
    for c in containers:
        projeto = (c.get("labels") or {}).get("com.docker.compose.project", "(sem projeto)")
        grupos.setdefault(projeto, []).append(c)
```

por:

```python
    grupos = agrupar_por_projeto(containers)
```

Não alterar mais nada nesse arquivo (o resto de `list_projects()`, `_dominio_por_labels`, `_dominio_por_arquivo_dinamico`, `_resolver_dominio` continuam iguais).

- [ ] **Step 6: Rodar a suíte de `test_projects_api.py` pra confirmar que nada quebrou**

Run: `cd backend && py -m pytest tests/test_projects_api.py -v`
Expected: PASS (todos os testes existentes, sem nenhuma mudança de comportamento)

- [ ] **Step 7: Commit**

```bash
git add backend/api/_project_grouping.py backend/api/projects.py backend/tests/test_project_grouping.py
git commit -m "refactor: extrai agrupar_por_projeto compartilhado pra api/_project_grouping.py"
```

---

### Task 2: Modelos `BackupSchedule` e `BackupJob`

**Files:**
- Modify: `backend/models/database.py`
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Produces: `class BackupSchedule(Base)` (colunas `projeto (PK), frequencia, hora`) e `class BackupJob(Base)` (colunas `id, projeto, tipo, arquivo, status, criado_em, concluido_em, erro, username`). Consumidos pela Task 3.

- [ ] **Step 1: Escrever os testes (devem falhar)**

Adicionar ao final de `backend/tests/test_database.py`:

```python
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_database.py -v -k "backup_schedule or backup_job"`
Expected: FAIL — `AttributeError: module 'models.database' has no attribute 'BackupSchedule'`

- [ ] **Step 3: Implementar os modelos**

Em `backend/models/database.py`, logo depois da classe `TraefikActionLog`:

```python
class BackupSchedule(Base):
    __tablename__ = "backup_schedule"
    projeto = Column(String, primary_key=True)
    frequencia = Column(String, nullable=False, default="off")
    hora = Column(Integer, nullable=False, default=3)


class BackupJob(Base):
    __tablename__ = "backup_job"
    id = Column(Integer, primary_key=True, autoincrement=True)
    projeto = Column(String, nullable=False)
    tipo = Column(String, nullable=False)
    arquivo = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)
    concluido_em = Column(DateTime, nullable=True)
    erro = Column(Text, nullable=True)
    username = Column(String, nullable=False)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_database.py -v -k "backup_schedule or backup_job"`
Expected: PASS (2 testes)

- [ ] **Step 5: Rodar toda a suíte de `test_database.py`**

Run: `cd backend && py -m pytest tests/test_database.py -v`
Expected: todos os testes existentes continuam passando.

- [ ] **Step 6: Commit**

```bash
git add backend/models/database.py backend/tests/test_database.py
git commit -m "feat: adiciona modelos BackupSchedule e BackupJob"
```

---

### Task 3: `api/backups.py` — fila de jobs + leitura/download de snapshots

**Files:**
- Create: `backend/api/backups.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_backups_api.py`

**Interfaces:**
- Consumes: `agrupar_por_projeto` (Task 1), `BackupSchedule`/`BackupJob` (Task 2), `get_last_metrics` (`collector/scheduler.py`, já existente).
- Produces: `router = APIRouter(prefix="/api/backups", ...)` com `GET /projects`, `PUT /projects/{projeto}/schedule`, `POST /projects/{projeto}/snapshot`, `POST /projects/{projeto}/snapshots/{arquivo}/restore`, `GET /projects/{projeto}/snapshots/{arquivo}/download`, `DELETE /projects/{projeto}/snapshots/{arquivo}`. Consumido pela Task 6 (frontend).
- Usa a env var `BACKUPS_DIR` (default `/opt/vps-monitor-backups`) — permite apontar pra um diretório temporário nos testes.

- [ ] **Step 1: Escrever os testes (devem falhar)**

Criar `backend/tests/test_backups_api.py`:

```python
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_backups_api.py -v`
Expected: FAIL em todos os testes — `ModuleNotFoundError: No module named 'api.backups'`.

- [ ] **Step 3: Implementar o módulo**

Criar `backend/api/backups.py`:

```python
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models.database as db_module
from api._project_grouping import agrupar_por_projeto
from api.auth import get_token_data, verify_token_header
from collector.scheduler import get_last_metrics
from models.database import BackupJob, BackupSchedule

BACKUPS_DIR = os.environ.get("BACKUPS_DIR", "/opt/vps-monitor-backups")
_NOME_VALIDO_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_ARQUIVO_VALIDO_RE = re.compile(r"^[a-zA-Z0-9_-]+\.tar\.gz$")
_FREQUENCIAS_VALIDAS = {"off", "daily", "weekly"}
_STATUS_ATIVOS = ["pending", "running"]

router = APIRouter(prefix="/api/backups", dependencies=[Depends(verify_token_header)])


def _validar_nome(valor: str, campo: str) -> None:
    if not _NOME_VALIDO_RE.match(valor):
        raise HTTPException(status_code=400, detail=f"{campo} inválido: '{valor}'.")


def _validar_arquivo(valor: str) -> None:
    if not _ARQUIVO_VALIDO_RE.match(valor):
        raise HTTPException(status_code=400, detail=f"Nome de arquivo inválido: '{valor}'.")


def _job_pendente_existe(session: Session, projeto: str) -> bool:
    return session.query(BackupJob).filter(
        BackupJob.projeto == projeto, BackupJob.status.in_(_STATUS_ATIVOS)
    ).count() > 0


def _listar_snapshots(projeto: str) -> list[dict]:
    destino_dir = os.path.join(BACKUPS_DIR, projeto)
    if not os.path.isdir(destino_dir):
        return []
    snapshots = []
    for arquivo in sorted(os.listdir(destino_dir), reverse=True):
        if not arquivo.endswith(".tar.gz"):
            continue
        caminho = os.path.join(destino_dir, arquivo)
        snapshots.append({
            "arquivo": arquivo,
            "tamanho_mb": round(os.path.getsize(caminho) / 1024 / 1024, 2),
        })
    return snapshots


class ScheduleIn(BaseModel):
    frequencia: str
    hora: int = 3


@router.get("/projects")
def list_projects():
    metrics = get_last_metrics()
    containers = metrics.get("containers", [])
    grupos = agrupar_por_projeto(containers)

    with Session(db_module.engine) as session:
        schedules = {s.projeto: s for s in session.query(BackupSchedule).all()}
        jobs_ativos: dict[str, BackupJob] = {}
        for job in session.query(BackupJob).filter(BackupJob.status.in_(_STATUS_ATIVOS)).all():
            jobs_ativos.setdefault(job.projeto, job)

        resultado = []
        for nome in sorted(grupos.keys()):
            if nome == "(sem projeto)":
                continue
            schedule = schedules.get(nome)
            job = jobs_ativos.get(nome)
            resultado.append({
                "nome": nome,
                "frequencia": schedule.frequencia if schedule else "off",
                "hora": schedule.hora if schedule else 3,
                "snapshots": _listar_snapshots(nome),
                "job_ativo": {"id": job.id, "tipo": job.tipo, "status": job.status} if job else None,
            })
    return {"projects": resultado}


@router.put("/projects/{projeto}/schedule")
def set_schedule(projeto: str, body: ScheduleIn):
    _validar_nome(projeto, "Nome do projeto")
    if body.frequencia not in _FREQUENCIAS_VALIDAS:
        raise HTTPException(status_code=400, detail=f"Frequência inválida: '{body.frequencia}'.")
    if not (0 <= body.hora <= 23):
        raise HTTPException(status_code=400, detail="Hora deve estar entre 0 e 23.")

    with Session(db_module.engine) as session:
        existente = session.get(BackupSchedule, projeto)
        if existente:
            existente.frequencia = body.frequencia
            existente.hora = body.hora
        else:
            session.add(BackupSchedule(projeto=projeto, frequencia=body.frequencia, hora=body.hora))
        session.commit()
    return {"ok": True}


@router.post("/projects/{projeto}/snapshot", status_code=202)
def create_snapshot(projeto: str, token_data: dict = Depends(get_token_data)):
    _validar_nome(projeto, "Nome do projeto")
    username = token_data.get("sub", "desconhecido")

    with Session(db_module.engine) as session:
        if _job_pendente_existe(session, projeto):
            raise HTTPException(status_code=409, detail=f"Já existe uma operação em andamento para '{projeto}'.")
        job = BackupJob(projeto=projeto, tipo="snapshot", status="pending", username=username)
        session.add(job)
        session.commit()
        return {"job_id": job.id}


@router.post("/projects/{projeto}/snapshots/{arquivo}/restore", status_code=202)
def restore_snapshot(projeto: str, arquivo: str, token_data: dict = Depends(get_token_data)):
    _validar_nome(projeto, "Nome do projeto")
    _validar_arquivo(arquivo)
    username = token_data.get("sub", "desconhecido")

    caminho = os.path.join(BACKUPS_DIR, projeto, arquivo)
    if not os.path.isfile(caminho):
        raise HTTPException(status_code=404, detail="Snapshot não encontrado.")

    with Session(db_module.engine) as session:
        if _job_pendente_existe(session, projeto):
            raise HTTPException(status_code=409, detail=f"Já existe uma operação em andamento para '{projeto}'.")
        job = BackupJob(projeto=projeto, tipo="restore", arquivo=arquivo, status="pending", username=username)
        session.add(job)
        session.commit()
        return {"job_id": job.id}


@router.get("/projects/{projeto}/snapshots/{arquivo}/download")
def download_snapshot(projeto: str, arquivo: str):
    _validar_nome(projeto, "Nome do projeto")
    _validar_arquivo(arquivo)

    caminho = os.path.join(BACKUPS_DIR, projeto, arquivo)
    if not os.path.isfile(caminho):
        raise HTTPException(status_code=404, detail="Snapshot não encontrado.")

    def _stream():
        with open(caminho, "rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk

    return StreamingResponse(_stream(), media_type="application/gzip", headers={
        "Content-Disposition": f'attachment; filename="{arquivo}"'
    })


@router.delete("/projects/{projeto}/snapshots/{arquivo}", status_code=202)
def delete_snapshot(projeto: str, arquivo: str, token_data: dict = Depends(get_token_data)):
    _validar_nome(projeto, "Nome do projeto")
    _validar_arquivo(arquivo)
    username = token_data.get("sub", "desconhecido")

    caminho = os.path.join(BACKUPS_DIR, projeto, arquivo)
    if not os.path.isfile(caminho):
        raise HTTPException(status_code=404, detail="Snapshot não encontrado.")

    with Session(db_module.engine) as session:
        if _job_pendente_existe(session, projeto):
            raise HTTPException(status_code=409, detail=f"Já existe uma operação em andamento para '{projeto}'.")
        job = BackupJob(projeto=projeto, tipo="delete", arquivo=arquivo, status="pending", username=username)
        session.add(job)
        session.commit()
        return {"job_id": job.id}
```

- [ ] **Step 4: Registrar o router temporariamente pra rodar os testes desta task**

Esta task depende do router estar registrado em `main.py` (o fixture `auth_client` faz `import main`). Em `backend/main.py`, adicionar o import:

```python
from api.backups import router as backups_router
```

E adicionar, junto aos outros `app.include_router`:

```python
app.include_router(backups_router)
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_backups_api.py -v`
Expected: PASS (18 testes)

- [ ] **Step 6: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: todos os testes passando, sem `FAILED`.

- [ ] **Step 7: Commit**

```bash
git add backend/api/backups.py backend/tests/test_backups_api.py backend/main.py
git commit -m "feat: adiciona fila de jobs e leitura/download de snapshots via API"
```

---

### Task 4: Docker — mount read-only de `BACKUPS_DIR`

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:** nenhuma (mudança de infraestrutura, sem código Python).

- [ ] **Step 1: Adicionar o mount no `docker-compose.yml`**

No serviço `monitor-backend`, dentro do bloco `volumes:`, adicionar (mantendo os mounts existentes):

```yaml
      - /opt/vps-monitor-backups:/opt/vps-monitor-backups:ro
```

(Sem adicionar `BACKUPS_DIR=` em `environment:` — o default do código já bate com esse path, mesmo padrão já usado com `TRAEFIK_DYNAMIC_DIR`.)

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: monta BACKUPS_DIR read-only no monitor-backend"
```

---

### Task 5: Script no host — `scripts/backup-worker.sh`

**Files:**
- Create: `scripts/backup-worker.sh`

**Interfaces:** nenhuma (shell script, roda via cron no host, fora de qualquer container).

- [ ] **Step 1: Criar o script**

Criar `scripts/backup-worker.sh`:

```bash
#!/bin/bash
# Roda no HOST via cron (nao dentro de um container).
#
# monitor-backend so grava "intencoes" (linhas em backup_job/backup_schedule,
# no SQLite do proprio monitor) — nunca acessa diretamente volumes ou
# diretorios de outros projetos, o que exigiria montar /opt e
# /var/lib/docker/volumes inteiros no container (expondo segredos de todos
# os ~7 clientes da VPS de uma vez). Este script executa o trabalho de
# verdade a partir do host, onde tem acesso total: para os containers do
# projeto-alvo, copia volumes + diretorio de trabalho, sobe de novo.
#
# Le/escreve o SQLite do monitor direto no path do volume Docker no host
# (nao precisa de mount novo, e so um arquivo real no disco). O monitor ja
# roda em modo WAL (PRAGMA journal_mode=WAL, ver models/database.py),
# seguro para acesso concorrente de multiplos processos no mesmo host.
#
# Nao usa "set -e": o script precisa continuar apos uma falha (pra marcar o
# job como failed e tentar subir os containers de novo), entao os erros sao
# tratados explicitamente em cada funcao, nunca abortando o script inteiro.
#
# Pre-requisito (uma vez, fora deste repo): apt-get install -y sqlite3
#
# Instalacao do cron (uma vez, fora deste repo):
#   crontab -e
#   * * * * * /opt/vps-monitor/monitor/scripts/backup-worker.sh >> /var/log/backup-worker.log 2>&1
set -uo pipefail

DB_PATH="/var/lib/docker/volumes/vps-monitor_vps_monitor_data/_data/monitor.db"
BACKUPS_DIR="/opt/vps-monitor-backups"
RETENCAO_PADRAO=5

mkdir -p "$BACKUPS_DIR"

# $projeto so chega aqui depois de validado por _validar_nome() na API
# (regex ^[a-zA-Z0-9_-]+$), entao a interpolacao direta nas queries abaixo
# nunca contem aspas nem caracteres especiais de SQL.
sqlite3_exec() {
  sqlite3 "$DB_PATH" "$1"
}

fazer_snapshot() {
  local projeto="$1"
  local containers
  containers=$(docker ps -a --filter "label=com.docker.compose.project=$projeto" --format '{{.Names}}')
  if [ -z "$containers" ]; then
    echo "Nenhum container encontrado para o projeto '$projeto'" >&2
    return 1
  fi

  local primeiro working_dir
  primeiro=$(echo "$containers" | head -1)
  working_dir=$(docker inspect "$primeiro" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}')
  if [ -z "$working_dir" ] || [ ! -d "$working_dir" ]; then
    echo "working_dir invalido para o projeto '$projeto': '$working_dir'" >&2
    return 1
  fi

  local volumes
  volumes=$(for c in $containers; do
    docker inspect "$c" --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{"\n"}}{{end}}{{end}}'
  done | sort -u)

  local staging
  staging=$(mktemp -d)
  mkdir -p "$staging/volumes"

  local falhou=0
  if ! (cd "$working_dir" && docker compose stop); then
    echo "Falha ao parar containers de '$projeto'" >&2
    falhou=1
  fi

  if [ "$falhou" -eq 0 ]; then
    if ! cp -a "$working_dir" "$staging/workdir"; then
      falhou=1
    fi

    if [ "$falhou" -eq 0 ] && [ -n "$volumes" ]; then
      while IFS= read -r vol; do
        [ -z "$vol" ] && continue
        local vol_path="/var/lib/docker/volumes/$vol/_data"
        if [ -d "$vol_path" ]; then
          mkdir -p "$staging/volumes/$vol"
          if ! cp -a "$vol_path/." "$staging/volumes/$vol/"; then
            falhou=1
          fi
        fi
      done <<< "$volumes"
    fi
  fi

  # Sempre tenta subir os containers de novo, mesmo que o stop ou a copia
  # tenham falhado — nunca deixar um projeto de cliente parado por causa de
  # uma falha do script (mesma garantia que fazer_restore ja tinha).
  if ! (cd "$working_dir" && docker compose up -d); then
    echo "AVISO: falha ao subir containers de '$projeto' apos snapshot" >&2
    falhou=1
  fi

  if [ "$falhou" -ne 0 ]; then
    rm -rf "$staging"
    echo "Falha ao gerar snapshot do projeto '$projeto'" >&2
    return 1
  fi

  local destino_dir="$BACKUPS_DIR/$projeto"
  mkdir -p "$destino_dir"
  local timestamp arquivo_final
  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
  arquivo_final="$destino_dir/${timestamp}.tar.gz"

  if ! tar -czf "$arquivo_final" -C "$staging" .; then
    rm -rf "$staging" "$arquivo_final"
    echo "Falha ao compactar snapshot do projeto '$projeto'" >&2
    return 1
  fi

  rm -rf "$staging"
  echo "Snapshot criado: $arquivo_final"
}

fazer_restore() {
  local projeto="$1"
  local arquivo="$2"
  local origem="$BACKUPS_DIR/$projeto/$arquivo"

  if [ ! -f "$origem" ]; then
    echo "Snapshot '$arquivo' nao encontrado para o projeto '$projeto'" >&2
    return 1
  fi

  local containers
  containers=$(docker ps -a --filter "label=com.docker.compose.project=$projeto" --format '{{.Names}}')
  if [ -z "$containers" ]; then
    echo "Nenhum container encontrado para o projeto '$projeto'" >&2
    return 1
  fi
  local primeiro working_dir
  primeiro=$(echo "$containers" | head -1)
  working_dir=$(docker inspect "$primeiro" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}')
  if [ -z "$working_dir" ] || [ ! -d "$working_dir" ]; then
    echo "working_dir invalido para o projeto '$projeto': '$working_dir'" >&2
    return 1
  fi

  local staging
  staging=$(mktemp -d)
  if ! tar -xzf "$origem" -C "$staging"; then
    rm -rf "$staging"
    echo "Falha ao extrair snapshot '$arquivo'" >&2
    return 1
  fi

  local falhou=0
  if ! (cd "$working_dir" && docker compose stop); then
    echo "Falha ao parar containers de '$projeto' antes do restore" >&2
    falhou=1
  fi

  if [ "$falhou" -eq 0 ] && [ -d "$staging/workdir" ]; then
    if ! rsync -a --delete "$staging/workdir/" "$working_dir/"; then
      falhou=1
    fi
  fi

  if [ "$falhou" -eq 0 ] && [ -d "$staging/volumes" ]; then
    for vol_dir in "$staging/volumes"/*/; do
      [ -d "$vol_dir" ] || continue
      local vol_nome vol_path
      vol_nome=$(basename "$vol_dir")
      vol_path="/var/lib/docker/volumes/$vol_nome/_data"
      if [ -d "$vol_path" ]; then
        if ! rsync -a --delete "$vol_dir" "$vol_path/"; then
          falhou=1
        fi
      fi
    done
  fi

  rm -rf "$staging"

  if ! (cd "$working_dir" && docker compose up -d); then
    echo "AVISO: falha ao subir containers de '$projeto' apos restore" >&2
    falhou=1
  fi

  if [ "$falhou" -ne 0 ]; then
    echo "Restore do projeto '$projeto' teve falhas (ver mensagens acima)" >&2
    return 1
  fi

  echo "Restore concluido para '$projeto' a partir de '$arquivo'"
}

fazer_delete() {
  local projeto="$1"
  local arquivo="$2"
  local caminho="$BACKUPS_DIR/$projeto/$arquivo"
  if [ ! -f "$caminho" ]; then
    echo "Snapshot '$arquivo' nao encontrado para o projeto '$projeto'" >&2
    return 1
  fi
  rm -f "$caminho"
  echo "Snapshot removido: $caminho"
}

aplicar_retencao() {
  local projeto="$1"
  local destino_dir="$BACKUPS_DIR/$projeto"
  local total
  total=$(ls -1 "$destino_dir"/*.tar.gz 2>/dev/null | wc -l)
  if [ "$total" -gt "$RETENCAO_PADRAO" ]; then
    ls -1t "$destino_dir"/*.tar.gz | tail -n "+$((RETENCAO_PADRAO + 1))" | while IFS= read -r antigo; do
      rm -f "$antigo"
      echo "Retencao: removido snapshot antigo $antigo"
    done
  fi
}

# ---------- 1. Processa no maximo um job pendente por execucao ----------
job_linha=$(sqlite3_exec "SELECT id, projeto, tipo, IFNULL(arquivo, '') FROM backup_job WHERE status='pending' ORDER BY criado_em LIMIT 1;")

if [ -n "$job_linha" ]; then
  IFS='|' read -r job_id job_projeto job_tipo job_arquivo <<< "$job_linha"

  sqlite3_exec "UPDATE backup_job SET status='running' WHERE id=$job_id;"

  saida=""
  sucesso=1
  case "$job_tipo" in
    snapshot)
      if ! saida=$(fazer_snapshot "$job_projeto" 2>&1); then sucesso=0; fi
      ;;
    restore)
      if ! saida=$(fazer_restore "$job_projeto" "$job_arquivo" 2>&1); then sucesso=0; fi
      ;;
    delete)
      if ! saida=$(fazer_delete "$job_projeto" "$job_arquivo" 2>&1); then sucesso=0; fi
      ;;
    *)
      saida="Tipo de job desconhecido: $job_tipo"
      sucesso=0
      ;;
  esac

  echo "$saida"

  if [ "$sucesso" -eq 1 ]; then
    sqlite3_exec "UPDATE backup_job SET status='done', concluido_em=datetime('now') WHERE id=$job_id;"
    if [ "$job_tipo" = "snapshot" ]; then
      aplicar_retencao "$job_projeto"
    fi
  else
    erro_escapado=$(echo "$saida" | sed "s/'/''/g" | tr '\n' ' ')
    sqlite3_exec "UPDATE backup_job SET status='failed', concluido_em=datetime('now'), erro='$erro_escapado' WHERE id=$job_id;"
  fi
fi

# ---------- 2. Verifica agendamentos ----------
sqlite3_exec "SELECT projeto, frequencia, hora FROM backup_schedule WHERE frequencia != 'off';" | while IFS='|' read -r projeto frequencia hora; do
  [ -z "$projeto" ] && continue

  pendente=$(sqlite3_exec "SELECT COUNT(*) FROM backup_job WHERE projeto='$projeto' AND status IN ('pending','running');")
  if [ "$pendente" -gt 0 ]; then
    continue
  fi

  ultimo_snapshot_epoch=0
  destino_dir="$BACKUPS_DIR/$projeto"
  if [ -d "$destino_dir" ]; then
    ultimo_arquivo=$(ls -1t "$destino_dir"/*.tar.gz 2>/dev/null | head -1)
    if [ -n "$ultimo_arquivo" ]; then
      ultimo_snapshot_epoch=$(date -r "$ultimo_arquivo" +%s)
    fi
  fi

  agora_epoch=$(date +%s)
  agora_hora=$(date +%H)
  intervalo_segundos=86400
  if [ "$frequencia" = "weekly" ]; then
    intervalo_segundos=604800
  fi

  if [ "$((10#$agora_hora))" -eq "$hora" ] && [ $((agora_epoch - ultimo_snapshot_epoch)) -ge "$intervalo_segundos" ]; then
    sqlite3_exec "INSERT INTO backup_job (projeto, tipo, status, criado_em, username) VALUES ('$projeto', 'snapshot', 'pending', datetime('now'), 'agendado');"
  fi
done
```

- [ ] **Step 2: Checar sintaxe do script**

Run: `bash -n scripts/backup-worker.sh`
Expected: sem saída (sintaxe válida).

- [ ] **Step 3: Dar permissão de execução**

Run: `chmod +x scripts/backup-worker.sh`

- [ ] **Step 4: Commit**

```bash
git add scripts/backup-worker.sh
git commit -m "feat: adiciona worker no host pra processar jobs de backup/restore"
```

(A instalação do cron job e do pacote `sqlite3` acontece na Task 7, deploy, já que exige acesso direto ao host.)

---

### Task 6: Frontend — página `/backups`

**Files:**
- Create: `frontend/app/backups/page.tsx`
- Modify: `frontend/app/layout.tsx`

**Interfaces:**
- Consumes: `GET/PUT/POST/DELETE /api/backups/*` (Task 3).

- [ ] **Step 1: Criar a página**

Criar `frontend/app/backups/page.tsx`:

```tsx
'use client';
import { useState, useEffect, useCallback, useRef } from 'react';
import api from '../../lib/api';
import Toast from '../../components/Toast';

interface Snapshot {
  arquivo: string;
  tamanho_mb: number;
}

interface JobAtivo {
  id: number;
  tipo: string;
  status: string;
}

interface BackupProject {
  nome: string;
  frequencia: 'off' | 'daily' | 'weekly';
  hora: number;
  snapshots: Snapshot[];
  job_ativo: JobAtivo | null;
}

const card: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8,
};

const selectStyle: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 13,
};

const input: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
};

const JOB_LABEL: Record<string, string> = {
  snapshot: 'Criando snapshot', restore: 'Restaurando', delete: 'Excluindo',
};

export default function BackupsPage() {
  const [projects, setProjects] = useState<BackupProject[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [restoreAlvo, setRestoreAlvo] = useState<{ projeto: string; arquivo: string } | null>(null);
  const [restoreConfirmText, setRestoreConfirmText] = useState('');
  const [deleteAlvo, setDeleteAlvo] = useState<{ projeto: string; arquivo: string } | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadProjects = useCallback(async () => {
    try { setProjects((await api.get('/backups/projects')).data.projects); } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { setLoading(true); loadProjects(); }, [loadProjects]);

  useEffect(() => {
    const temJobAtivo = projects.some((p) => p.job_ativo !== null);
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(loadProjects, temJobAtivo ? 5000 : 30000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [projects, loadProjects]);

  async function handleScheduleChange(nome: string, frequencia: string, hora: number) {
    try {
      await api.put(`/backups/projects/${nome}/schedule`, { frequencia, hora });
      loadProjects();
    } catch {
      setToast({ msg: 'Erro ao salvar agendamento', type: 'error' });
    }
  }

  async function handleSnapshot(nome: string) {
    try {
      await api.post(`/backups/projects/${nome}/snapshot`);
      setToast({ msg: `Snapshot de '${nome}' enfileirado`, type: 'success' });
      loadProjects();
    } catch (e: any) {
      setToast({ msg: e?.response?.data?.detail || 'Erro ao criar snapshot', type: 'error' });
    }
  }

  async function handleDownload(projeto: string, arquivo: string) {
    try {
      const resp = await api.get(`/backups/projects/${projeto}/snapshots/${arquivo}/download`, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([resp.data]));
      const link = document.createElement('a');
      link.href = url;
      link.download = arquivo;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      setToast({ msg: 'Erro ao baixar snapshot', type: 'error' });
    }
  }

  async function confirmRestore() {
    if (!restoreAlvo || restoreConfirmText !== restoreAlvo.projeto) return;
    try {
      await api.post(`/backups/projects/${restoreAlvo.projeto}/snapshots/${restoreAlvo.arquivo}/restore`);
      setToast({ msg: `Restore de '${restoreAlvo.projeto}' enfileirado`, type: 'success' });
      loadProjects();
    } catch (e: any) {
      setToast({ msg: e?.response?.data?.detail || 'Erro ao iniciar restore', type: 'error' });
    }
    setRestoreAlvo(null);
    setRestoreConfirmText('');
  }

  async function confirmDelete() {
    if (!deleteAlvo) return;
    try {
      await api.delete(`/backups/projects/${deleteAlvo.projeto}/snapshots/${deleteAlvo.arquivo}`);
      setToast({ msg: 'Snapshot excluído', type: 'success' });
      loadProjects();
    } catch {
      setToast({ msg: 'Erro ao excluir snapshot', type: 'error' });
    }
    setDeleteAlvo(null);
  }

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      {toast && <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />}
      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Backups</h1>

      {loading && projects.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Carregando...</p>
      )}

      {!loading && projects.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Nenhum projeto encontrado.</p>
      )}

      {projects.map((p) => (
        <div key={p.nome} style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--text)', fontWeight: 600 }}>{p.nome}</span>

            <select
              style={selectStyle}
              value={p.frequencia}
              onChange={(e) => handleScheduleChange(p.nome, e.target.value, p.hora)}
            >
              <option value="off">Desligado</option>
              <option value="daily">Diário</option>
              <option value="weekly">Semanal</option>
            </select>

            {p.frequencia !== 'off' && (
              <select
                style={selectStyle}
                value={p.hora}
                onChange={(e) => handleScheduleChange(p.nome, p.frequencia, Number(e.target.value))}
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>
                ))}
              </select>
            )}

            <div style={{ marginLeft: 'auto' }}>
              {p.job_ativo ? (
                <span style={{ color: 'var(--accent)', fontSize: 13 }}>
                  {JOB_LABEL[p.job_ativo.tipo] ?? p.job_ativo.tipo} ({p.job_ativo.status})...
                </span>
              ) : (
                <button
                  onClick={() => handleSnapshot(p.nome)}
                  style={{ padding: '6px 14px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700, fontSize: 13 }}
                >
                  Criar snapshot agora
                </button>
              )}
            </div>
          </div>

          {p.snapshots.length === 0 ? (
            <p style={{ color: 'var(--muted)', fontSize: 13 }}>Nenhum snapshot ainda.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {p.snapshots.map((s) => (
                <div key={s.arquivo} style={{
                  display: 'flex', alignItems: 'center', gap: 12, padding: '6px 10px',
                  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6,
                }}>
                  <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{s.arquivo}</span>
                  <span style={{ color: 'var(--muted)', fontSize: 12 }}>{s.tamanho_mb.toFixed(1)} MB</span>
                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                    <button
                      onClick={() => handleDownload(p.nome, s.arquivo)}
                      style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', cursor: 'pointer', fontSize: 12 }}
                    >
                      Baixar
                    </button>
                    <button
                      onClick={() => { setRestoreAlvo({ projeto: p.nome, arquivo: s.arquivo }); setRestoreConfirmText(''); }}
                      style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', cursor: 'pointer', fontSize: 12 }}
                    >
                      Restaurar
                    </button>
                    <button
                      onClick={() => setDeleteAlvo({ projeto: p.nome, arquivo: s.arquivo })}
                      style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 6, color: 'var(--danger)', cursor: 'pointer', fontSize: 12 }}
                    >
                      Excluir
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {restoreAlvo && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setRestoreAlvo(null)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, maxWidth: 460 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>Restaurar snapshot</h3>
            <p style={{ color: 'var(--muted)', marginBottom: 12, fontSize: 14 }}>
              Isso vai parar os containers de &quot;{restoreAlvo.projeto}&quot;, substituir todos os dados atuais pelos do snapshot &quot;{restoreAlvo.arquivo}&quot;, e subir de novo. <strong>Essa ação não pode ser desfeita.</strong>
            </p>
            <p style={{ color: 'var(--muted)', marginBottom: 8, fontSize: 13 }}>
              Digite <strong>{restoreAlvo.projeto}</strong> pra confirmar:
            </p>
            <input
              style={{ ...input, marginBottom: 16 }}
              value={restoreConfirmText}
              onChange={(e) => setRestoreConfirmText(e.target.value)}
              autoFocus
            />
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setRestoreAlvo(null)}
                style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
              >
                Cancelar
              </button>
              <button
                onClick={confirmRestore}
                disabled={restoreConfirmText !== restoreAlvo.projeto}
                style={{
                  padding: '8px 20px', border: 'none', borderRadius: 6, fontWeight: 700,
                  background: restoreConfirmText === restoreAlvo.projeto ? 'var(--danger)' : 'var(--surface)',
                  color: restoreConfirmText === restoreAlvo.projeto ? '#fff' : 'var(--muted)',
                  cursor: restoreConfirmText === restoreAlvo.projeto ? 'pointer' : 'not-allowed',
                }}
              >
                Confirmar restore
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteAlvo && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setDeleteAlvo(null)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, maxWidth: 420 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>Excluir snapshot</h3>
            <p style={{ color: 'var(--muted)', marginBottom: 20, fontSize: 14 }}>
              Tem certeza que deseja excluir o snapshot &quot;{deleteAlvo.arquivo}&quot; de &quot;{deleteAlvo.projeto}&quot;? Essa ação não pode ser desfeita.
            </p>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setDeleteAlvo(null)}
                style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
              >
                Cancelar
              </button>
              <button
                onClick={confirmDelete}
                style={{ padding: '8px 20px', background: 'var(--danger)', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
              >
                Confirmar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Adicionar o link no menu**

Em `frontend/app/layout.tsx`, no array `NAV`, adicionar uma entrada logo depois de `/traefik`:

```tsx
  { href: '/traefik', label: 'Traefik', icon: '🔀' },
  { href: '/backups', label: 'Backups', icon: '💾' },
  { href: '/configuracoes', label: 'Configurações', icon: '⚙️' },
```

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build limpo, sem erros de tipo, rota `/backups` aparece na lista de rotas geradas.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/backups/page.tsx frontend/app/layout.tsx
git commit -m "feat: adiciona pagina de backup/restore de projetos (/backups)"
```

(Verificação manual no navegador fica por conta do usuário, combinado nesta sessão.)

---

### Task 7: Deploy para produção

**Files:** nenhum (ação operacional, sem mudança de código)

**Atenção:** esta task só deve ser executada após confirmação explícita do usuário — dá ao container `monitor-backend` acesso de leitura a um diretório de backups que vai conter cópias de dados de todos os projetos/clientes da VPS, e o script no host ganha permissão pra parar/subir qualquer stack docker-compose da VPS (via `docker compose stop`/`up -d`), incluindo as de clientes reais.

- [ ] **Step 1: Push para o remoto**

```bash
git push origin main
```

- [ ] **Step 2: Deploy na VPS**

```bash
ssh root@144.91.92.70 "cd /opt/vps-monitor && git pull --ff-only && bash monitor/deploy.sh"
```

- [ ] **Step 3: Confirmar containers saudáveis**

```bash
ssh root@144.91.92.70 "docker ps --filter name=monitor --filter name=vps-monitor --format '{{.Names}}\t{{.Status}}'"
```

Expected: todos `Up`, sem `Restarting`.

- [ ] **Step 4: Instalar o `sqlite3` no host (pré-requisito do worker)**

```bash
ssh root@144.91.92.70 "which sqlite3 || apt-get update && apt-get install -y sqlite3"
```

Expected: `sqlite3` instalado (ou já presente).

- [ ] **Step 5: Criar o diretório de backups e garantir permissão de execução do script**

```bash
ssh root@144.91.92.70 "mkdir -p /opt/vps-monitor-backups && chmod +x /opt/vps-monitor/monitor/scripts/backup-worker.sh && ls -l /opt/vps-monitor/monitor/scripts/backup-worker.sh"
```

Expected: diretório criado, script com permissão `-rwxr-xr-x` (mesma correção manual já necessária pros outros 2 scripts desta base de código, que não têm o bit executável rastreado no git).

- [ ] **Step 6: Instalar o cron job**

```bash
ssh root@144.91.92.70 "(crontab -l 2>/dev/null; echo '* * * * * /opt/vps-monitor/monitor/scripts/backup-worker.sh >> /var/log/backup-worker.log 2>&1') | crontab -"
```

Confirmar:

```bash
ssh root@144.91.92.70 "crontab -l | grep backup-worker"
```

- [ ] **Step 7: Teste manual end-to-end usando o próprio projeto `vps-monitor`**

Usar o projeto **`vps-monitor`** (o próprio monitor, não um projeto de cliente) como alvo do primeiro teste — é o único projeto da VPS que não afeta um cliente externo se ficar fora do ar por alguns segundos durante o teste.

1. Pela UI (`/backups`), clicar em "Criar snapshot agora" no projeto `vps-monitor`.
2. Acompanhar o log do worker: `ssh root@144.91.92.70 "tail -f /var/log/backup-worker.log"` — confirmar que o job aparece, os containers do monitor reiniciam brevemente, e um arquivo `.tar.gz` aparece em `/opt/vps-monitor-backups/vps-monitor/`.
3. Confirmar na UI que o snapshot aparece na lista, com o tamanho correto.
4. Baixar o snapshot pela UI, confirmar que o arquivo `.tar.gz` abre corretamente.
5. Restaurar esse mesmo snapshot pela UI (digitando `vps-monitor` pra confirmar) — confirmar que os containers reiniciam e o monitor volta a responder normalmente.
6. Excluir o snapshot de teste pela UI, confirmar que some da lista e do disco.
7. Só depois desse ciclo completo validado no próprio `vps-monitor`, considerar liberar o uso em projetos de clientes reais (mecanicapro, corridas, etc.) — combinar com o usuário antes de rodar o primeiro snapshot real de um projeto de cliente.
