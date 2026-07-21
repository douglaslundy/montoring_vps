# Excluir Projeto (teardown completo) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Botão na tela `/projetos` que exclui um projeto inteiro da VPS — para e remove seus containers, apaga seus volumes Docker, remove rotas do Traefik associadas e remove regras de firewall marcadas manualmente — com confirmação forte (snapshot obrigatório + digitar o nome do projeto).

**Architecture:** Mesmo padrão já usado 4x nesta base (fail2ban, Traefik, backup/restore, firewall): o `monitor-backend` nunca executa `docker stop`/`docker rm`/`docker volume rm` diretamente — só grava um pedido (`ProjectDeleteRequest`) no seu SQLite. Um script novo no host, `scripts/project-delete-worker.sh` (cron, 1x/min), processa o pedido: para os containers, remove os arquivos Traefik marcados (o watcher de commit já existente detecta e commita sozinho), enfileira as remoções de firewall marcadas na fila `firewall_rule_request` já existente (reaproveitando o `firewall-worker.sh` já testado, com sua trava de portas protegidas), remove os volumes reais, e remove os containers.

**Tech Stack:** FastAPI + SQLAlchemy + pytest (backend, TDD), Next.js/React/TypeScript (frontend, sem suíte de testes — build limpo), bash + `docker`/`python3` (script no host, sem teste automatizado).

## Global Constraints

- **O projeto `vps-monitor` nunca pode ser alvo de exclusão, sem exceção** — tanto `GET /delete-preview` quanto `POST /delete` retornam 400 imediatamente se `projeto == "vps-monitor"`, antes de qualquer outra validação. Checagem repetida no worker (defesa em profundidade).
- Remoção de rotas Traefik só aceita arquivos que começam com `vps-monitor-` (mesma trava já usada em `api/traefik.py`) — nunca uma rota manual.
- Remoção de regras de firewall nunca aceita porta em `{22, 80, 443}` (mesma trava já usada em `api/firewall.py`) — checada de novo aqui como defesa em profundidade, tanto na API quanto no worker.
- `POST /delete` exige um `snapshot_arquivo` que já existe e pertence ao projeto (valida contra `BACKUPS_DIR/{projeto}/{arquivo}` real).
- Um novo pedido de exclusão para um projeto que já tem um `ProjectDeleteRequest` `pending`/`running` retorna 409.
- Volumes são removidos de verdade (`docker volume rm`) — ação irreversível, sem confirmação adicional além do fluxo já descrito (snapshot + digitar nome).

---

### Task 1: Modelo `ProjectDeleteRequest`

**Files:**
- Modify: `backend/models/database.py`
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Produces: `class ProjectDeleteRequest(Base)` com colunas `id, projeto, rotas_traefik_selecionadas, regras_firewall_selecionadas, snapshot_arquivo, status, criado_em, concluido_em, erro, username`. Consumido pela Task 2/3.

- [ ] **Step 1: Escrever o teste (deve falhar)**

Adicionar ao final de `backend/tests/test_database.py`:

```python
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_database.py -v -k project_delete_request`
Expected: FAIL — `AttributeError: module 'models.database' has no attribute 'ProjectDeleteRequest'`

- [ ] **Step 3: Implementar o modelo**

Em `backend/models/database.py`, logo depois da classe `FirewallRuleRequest` (antes de `AccessLog`):

```python
class ProjectDeleteRequest(Base):
    __tablename__ = "project_delete_request"
    id = Column(Integer, primary_key=True, autoincrement=True)
    projeto = Column(String, nullable=False)
    rotas_traefik_selecionadas = Column(Text, nullable=False)
    regras_firewall_selecionadas = Column(Text, nullable=False)
    snapshot_arquivo = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)
    concluido_em = Column(DateTime, nullable=True)
    erro = Column(Text, nullable=True)
    username = Column(String, nullable=False)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_database.py -v -k project_delete_request`
Expected: PASS

- [ ] **Step 5: Rodar toda a suíte de `test_database.py`**

Run: `cd backend && py -m pytest tests/test_database.py -v`
Expected: todos os testes existentes continuam passando.

- [ ] **Step 6: Commit**

```bash
git add backend/models/database.py backend/tests/test_database.py
git commit -m "feat: adiciona modelo ProjectDeleteRequest"
```

---

### Task 2: `GET /api/projects/{projeto}/delete-preview`

**Files:**
- Modify: `backend/api/projects.py`
- Test: `backend/tests/test_projects_api.py`

**Interfaces:**
- Consumes: `docker_client.container_inspect(id_full)` de `collector.scheduler` (já importado em `api/containers.py` como `from collector.scheduler import docker_client, get_last_metrics`); `FIREWALL_STATE_FILE`/`PORTAS_PROTEGIDAS` de `api/firewall.py`; `agrupar_por_projeto`, `_resolver_dominio`, `TRAEFIK_DYNAMIC_DIR`, `_HOST_RE` já existentes em `api/projects.py`.
- Produces: `GET /api/projects/{projeto}/delete-preview` retornando `{"containers": [...], "volumes": [...], "rotas_candidatas": [...], "regras_firewall_candidatas": [...]}`. Consumido pela Task 5 (frontend).

**Nota sobre `Mounts`/`Ports` no retorno de `container_inspect`:** é a API padrão do Docker Engine (`GET /containers/{id}/json`), estável e documentada publicamente — `Mounts` é uma lista de `{"Type": "volume"|"bind"|..., "Name": "...", ...}` (só entradas com `Type == "volume"` têm um `Name` de volume real), e `NetworkSettings.Ports` é um dict `{"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}` (valor `null` se a porta é exposta mas não publicada). Isso já é usado neste projeto por `scripts/backup-worker.sh` (via `docker inspect --format`, mesma API, caminho CLI) para descobrir volumes — aqui usamos a mesma informação via a API REST (`container_inspect`), já que o objetivo é montar o preview de forma síncrona no backend, sem esperar o worker no host.

- [ ] **Step 1: Escrever os testes (devem falhar)**

Adicionar ao final de `backend/tests/test_projects_api.py`:

```python
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
```

Adicionar também no topo do arquivo (junto aos outros imports já existentes):

```python
import json
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_projects_api.py -v -k delete_preview`
Expected: FAIL — `404 Not Found` (rota ainda não existe) em todos os testes.

- [ ] **Step 3: Implementar o endpoint**

Em `backend/api/projects.py`, adicionar aos imports do topo:

```python
import json
from fastapi import APIRouter, HTTPException
from collector.scheduler import docker_client, get_last_metrics
from api.firewall import FIREWALL_STATE_FILE, PORTAS_PROTEGIDAS
```

(A linha `from fastapi import APIRouter` já existe — trocar por `from fastapi import APIRouter, HTTPException`. A linha `from collector.scheduler import get_last_metrics` já existe — trocar por `from collector.scheduler import docker_client, get_last_metrics`.)

Adicionar ao final do arquivo:

```python
PROJETO_PROTEGIDO = "vps-monitor"


def _projeto_ou_404(projeto: str) -> list[dict]:
    metrics = get_last_metrics()
    containers = metrics.get("containers", [])
    grupos = agrupar_por_projeto(containers)
    if projeto not in grupos:
        raise HTTPException(status_code=404, detail=f"Projeto '{projeto}' não encontrado.")
    return grupos[projeto]


def _portas_publicadas(inspect: dict) -> set[int]:
    portas: set[int] = set()
    ports = (inspect.get("NetworkSettings") or {}).get("Ports") or {}
    for bindings in ports.values():
        if not bindings:
            continue
        for b in bindings:
            host_port = b.get("HostPort")
            if host_port:
                try:
                    portas.add(int(host_port))
                except ValueError:
                    pass
    return portas


def _rotas_candidatas(dominio_projeto: str | None) -> list[str]:
    if not dominio_projeto or not os.path.isdir(TRAEFIK_DYNAMIC_DIR):
        return []
    candidatas = []
    for filename in sorted(os.listdir(TRAEFIK_DYNAMIC_DIR)):
        if not filename.startswith("vps-monitor-") or not filename.endswith(".yml"):
            continue
        path = os.path.join(TRAEFIK_DYNAMIC_DIR, filename)
        try:
            with open(path, encoding="utf-8") as f:
                conteudo = f.read()
        except OSError:
            continue
        if dominio_projeto in _HOST_RE.findall(conteudo):
            candidatas.append(filename)
    return candidatas


def _regras_firewall_candidatas(portas_projeto: set[int]) -> list[dict]:
    if not portas_projeto or not os.path.isfile(FIREWALL_STATE_FILE):
        return []
    try:
        with open(FIREWALL_STATE_FILE, encoding="utf-8") as f:
            estado = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    candidatas = []
    for regra in estado.get("regras", []):
        if regra.get("porta") in PORTAS_PROTEGIDAS:
            continue
        if regra.get("porta") in portas_projeto:
            candidatas.append({
                "porta": regra["porta"], "protocolo": regra["protocolo"],
                "permitir": regra["permitir"], "origem_ip": regra.get("origem_ip"),
            })
    return candidatas


@router.get("/projects/{projeto}/delete-preview")
async def delete_preview(projeto: str):
    if projeto == PROJETO_PROTEGIDO:
        raise HTTPException(status_code=400, detail=f"O projeto '{PROJETO_PROTEGIDO}' não pode ser excluído.")
    membros = _projeto_ou_404(projeto)

    volumes: set[str] = set()
    portas_publicadas: set[int] = set()
    for m in membros:
        id_full = m.get("id_full")
        if not id_full:
            continue
        inspect = await docker_client.container_inspect(id_full)
        for mount in inspect.get("Mounts", []):
            if mount.get("Type") == "volume" and mount.get("Name"):
                volumes.add(mount["Name"])
        portas_publicadas |= _portas_publicadas(inspect)

    dominio_projeto = _resolver_dominio(projeto, membros)

    return {
        "containers": [{"name": m.get("name"), "status": m.get("status")} for m in membros],
        "volumes": sorted(volumes),
        "rotas_candidatas": _rotas_candidatas(dominio_projeto),
        "regras_firewall_candidatas": _regras_firewall_candidatas(portas_publicadas),
    }
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_projects_api.py -v -k delete_preview`
Expected: PASS (5 testes)

- [ ] **Step 5: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: todos os testes passando, sem `FAILED`.

- [ ] **Step 6: Commit**

```bash
git add backend/api/projects.py backend/tests/test_projects_api.py
git commit -m "feat: adiciona preview de exclusao de projeto (GET /delete-preview)"
```

---

### Task 3: `POST /api/projects/{projeto}/delete`

**Files:**
- Modify: `backend/api/projects.py`
- Test: `backend/tests/test_projects_api.py`

**Interfaces:**
- Consumes: `ProjectDeleteRequest` (Task 1), `_projeto_ou_404`/`PROJETO_PROTEGIDO` (Task 2), `BACKUPS_DIR`/`_ARQUIVO_VALIDO_RE` de `api/backups.py`, `PORTAS_PROTEGIDAS` de `api/firewall.py`, `get_token_data` de `api/auth.py`.
- Produces: `POST /api/projects/{projeto}/delete` retornando 202 `{request_id}`. Consumido pela Task 5 (frontend) e Task 4 (worker, via leitura direta da tabela).

- [ ] **Step 1: Escrever os testes (devem falhar)**

Adicionar ao final de `backend/tests/test_projects_api.py`:

```python
# ---------------------------------------------------------------------------
# POST /api/projects/{projeto}/delete
# ---------------------------------------------------------------------------

def _preparar_snapshot(tmp_path, monkeypatch, projeto="mecanicapro", arquivo="20260721T140000Z.tar.gz"):
    backups_dir = tmp_path / "backups"
    (backups_dir / projeto).mkdir(parents=True)
    (backups_dir / projeto / arquivo).write_bytes(b"conteudo-fake")
    monkeypatch.setenv("BACKUPS_DIR", str(backups_dir))
    import api.backups as backups_mod
    importlib.reload(backups_mod)
    import api.projects as projects_mod
    importlib.reload(projects_mod)
    import main
    importlib.reload(main)
    client = TestClient(main.app)
    return client, arquivo


def test_post_delete_bloqueia_vps_monitor(auth_client):
    r = auth_client.post("/api/projects/vps-monitor/delete", json={
        "snapshot_arquivo": "qualquer.tar.gz", "rotas_selecionadas": [], "regras_selecionadas": [],
    })
    assert r.status_code == 400


def test_post_delete_404_projeto_inexistente(auth_client, tmp_path, monkeypatch):
    client, arquivo = _preparar_snapshot(tmp_path, monkeypatch, projeto="projeto-fantasma")
    client.headers.update(auth_client.headers)
    with patch("collector.scheduler._last_metrics", _metrics_stub_com_id()):
        r = client.post("/api/projects/projeto-fantasma/delete", json={
            "snapshot_arquivo": arquivo, "rotas_selecionadas": [], "regras_selecionadas": [],
        })
    assert r.status_code == 404


def test_post_delete_400_snapshot_inexistente(auth_client, tmp_path, monkeypatch):
    client, _ = _preparar_snapshot(tmp_path, monkeypatch)
    client.headers.update(auth_client.headers)
    with patch("collector.scheduler._last_metrics", _metrics_stub_com_id()):
        r = client.post("/api/projects/mecanicapro/delete", json={
            "snapshot_arquivo": "nao-existe.tar.gz", "rotas_selecionadas": [], "regras_selecionadas": [],
        })
    assert r.status_code == 400


def test_post_delete_400_rota_nao_gerenciada(auth_client, tmp_path, monkeypatch):
    client, arquivo = _preparar_snapshot(tmp_path, monkeypatch)
    client.headers.update(auth_client.headers)
    with patch("collector.scheduler._last_metrics", _metrics_stub_com_id()):
        r = client.post("/api/projects/mecanicapro/delete", json={
            "snapshot_arquivo": arquivo, "rotas_selecionadas": ["mecanicapro-manual.yml"], "regras_selecionadas": [],
        })
    assert r.status_code == 400


def test_post_delete_400_regra_porta_protegida(auth_client, tmp_path, monkeypatch):
    client, arquivo = _preparar_snapshot(tmp_path, monkeypatch)
    client.headers.update(auth_client.headers)
    with patch("collector.scheduler._last_metrics", _metrics_stub_com_id()):
        r = client.post("/api/projects/mecanicapro/delete", json={
            "snapshot_arquivo": arquivo, "rotas_selecionadas": [],
            "regras_selecionadas": [{"porta": 22, "protocolo": "tcp", "permitir": True, "origem_ip": None}],
        })
    assert r.status_code == 400


def test_post_delete_sucesso(auth_client, tmp_path, monkeypatch, test_db):
    client, arquivo = _preparar_snapshot(tmp_path, monkeypatch)
    client.headers.update(auth_client.headers)
    with patch("collector.scheduler._last_metrics", _metrics_stub_com_id()):
        r = client.post("/api/projects/mecanicapro/delete", json={
            "snapshot_arquivo": arquivo,
            "rotas_selecionadas": ["vps-monitor-mecanicapro.yml"],
            "regras_selecionadas": [{"porta": 3000, "protocolo": "tcp", "permitir": True, "origem_ip": None}],
        })
    assert r.status_code == 202
    request_id = r.json()["request_id"]

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        req = session.get(test_db.ProjectDeleteRequest, request_id)
    assert req.projeto == "mecanicapro"
    assert req.snapshot_arquivo == arquivo
    assert json.loads(req.rotas_traefik_selecionadas) == ["vps-monitor-mecanicapro.yml"]
    assert json.loads(req.regras_firewall_selecionadas)[0]["porta"] == 3000
    assert req.status == "pending"


def test_post_delete_409_pedido_ja_pendente(auth_client, tmp_path, monkeypatch):
    client, arquivo = _preparar_snapshot(tmp_path, monkeypatch)
    client.headers.update(auth_client.headers)
    body = {"snapshot_arquivo": arquivo, "rotas_selecionadas": [], "regras_selecionadas": []}
    with patch("collector.scheduler._last_metrics", _metrics_stub_com_id()):
        client.post("/api/projects/mecanicapro/delete", json=body)
        r = client.post("/api/projects/mecanicapro/delete", json=body)
    assert r.status_code == 409


def test_post_delete_sem_autenticacao_401():
    import main
    client = TestClient(main.app)
    r = client.post("/api/projects/mecanicapro/delete", json={
        "snapshot_arquivo": "x.tar.gz", "rotas_selecionadas": [], "regras_selecionadas": [],
    })
    assert r.status_code == 401
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_projects_api.py -v -k post_delete`
Expected: FAIL — `404 Not Found` (rota ainda não existe) em todos os testes.

- [ ] **Step 3: Implementar o endpoint**

Em `backend/api/projects.py`, adicionar aos imports do topo:

```python
from typing import Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models.database as db_module
from api.auth import get_token_data
from api.backups import BACKUPS_DIR, _ARQUIVO_VALIDO_RE
from models.database import ProjectDeleteRequest
```

Adicionar ao final do arquivo:

```python
_STATUS_ATIVOS_DELETE = ["pending", "running"]


class RegraSelecionada(BaseModel):
    porta: int
    protocolo: str
    permitir: bool
    origem_ip: Optional[str] = None


class ProjectDeleteIn(BaseModel):
    snapshot_arquivo: str
    rotas_selecionadas: list[str] = []
    regras_selecionadas: list[RegraSelecionada] = []


def _job_delete_pendente_existe(session: Session, projeto: str) -> bool:
    return session.query(ProjectDeleteRequest).filter(
        ProjectDeleteRequest.projeto == projeto,
        ProjectDeleteRequest.status.in_(_STATUS_ATIVOS_DELETE),
    ).count() > 0


@router.post("/projects/{projeto}/delete", status_code=202)
def delete_project(projeto: str, body: ProjectDeleteIn, token_data: dict = Depends(get_token_data)):
    if projeto == PROJETO_PROTEGIDO:
        raise HTTPException(status_code=400, detail=f"O projeto '{PROJETO_PROTEGIDO}' não pode ser excluído.")
    _projeto_ou_404(projeto)

    if not _ARQUIVO_VALIDO_RE.match(body.snapshot_arquivo):
        raise HTTPException(status_code=400, detail="Nome de arquivo de snapshot inválido.")
    caminho_snapshot = os.path.join(BACKUPS_DIR, projeto, body.snapshot_arquivo)
    if not os.path.isfile(caminho_snapshot):
        raise HTTPException(status_code=400, detail="Snapshot informado não existe para este projeto.")

    for filename in body.rotas_selecionadas:
        if not filename.startswith("vps-monitor-"):
            raise HTTPException(status_code=400, detail=f"Rota '{filename}' não é gerenciada pelo monitor.")

    for regra in body.regras_selecionadas:
        if regra.porta in PORTAS_PROTEGIDAS:
            raise HTTPException(status_code=400, detail=f"Porta {regra.porta} é protegida e não pode ser removida.")

    username = token_data.get("sub", "desconhecido")

    with Session(db_module.engine) as session:
        if _job_delete_pendente_existe(session, projeto):
            raise HTTPException(status_code=409, detail=f"Já existe uma exclusão em andamento para '{projeto}'.")
        req = ProjectDeleteRequest(
            projeto=projeto,
            rotas_traefik_selecionadas=json.dumps(body.rotas_selecionadas),
            regras_firewall_selecionadas=json.dumps([r.dict() for r in body.regras_selecionadas]),
            snapshot_arquivo=body.snapshot_arquivo,
            status="pending",
            username=username,
        )
        session.add(req)
        session.commit()
        return {"request_id": req.id}
```

Também adicionar ao import do topo (junto ao `from fastapi import APIRouter, HTTPException`):

```python
from fastapi import APIRouter, Depends, HTTPException
```

(troca a linha já modificada na Task 2, agora incluindo `Depends`.)

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_projects_api.py -v -k post_delete`
Expected: PASS (8 testes)

- [ ] **Step 5: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: todos os testes passando, sem `FAILED`.

- [ ] **Step 6: Commit**

```bash
git add backend/api/projects.py backend/tests/test_projects_api.py
git commit -m "feat: adiciona endpoint de exclusao de projeto (POST /delete)"
```

---

### Task 4: Script no host — `scripts/project-delete-worker.sh`

**Files:**
- Create: `scripts/project-delete-worker.sh`

**Interfaces:** nenhuma (shell script, roda via cron no host, fora de qualquer container). Lê `project_delete_request` e escreve em `firewall_rule_request` (mesmo SQLite do monitor).

- [ ] **Step 1: Criar o script**

Criar `scripts/project-delete-worker.sh`:

```bash
#!/bin/bash
# Roda no HOST via cron (nao dentro de um container).
#
# monitor-backend so grava "intencoes" (linhas em project_delete_request, no
# SQLite do proprio monitor) — nunca roda `docker stop/rm`, `docker volume rm`
# diretamente, o que exigiria acesso total ao Docker do host (bem alem do que
# o socket-proxy do container libera hoje: so CONTAINERS/POST/DELETE/IMAGES,
# sem VOLUMES). Este script executa a exclusao de verdade a partir do host.
#
# Reaproveita duas filas/watchers ja existentes em vez de duplicar logica:
# - Remocao de rotas Traefik: so apaga o arquivo .yml marcado. O
#   scripts/traefik-dynamic-commit-watcher.sh ja existente detecta a mudanca
#   no proximo ciclo e comita sozinho — nenhuma mudanca necessaria nele.
# - Remocao de regras de firewall: insere linhas em firewall_rule_request
#   (acao=remove) em vez de rodar `ufw` direto — o scripts/firewall-worker.sh
#   ja existente processa essas linhas, com sua propria trava de portas
#   protegidas ja testada (nao duplicamos essa logica aqui).
#
# Nao usa "set -e": precisa continuar apos falha pra marcar o job como
# failed, tratamento de erro explicito em cada etapa. Sem rollback
# automatico — a maioria das acoes (rm de volume, rm de arquivo) nao e
# reversivel de qualquer forma; o erro fica registrado pra investigacao
# manual.
#
# Instalacao do cron (uma vez, fora deste repo):
#   crontab -e
#   * * * * * /opt/vps-monitor/monitor/scripts/project-delete-worker.sh >> /var/log/project-delete-worker.log 2>&1
set -uo pipefail

DB_PATH="/var/lib/docker/volumes/vps-monitor_vps_monitor_data/_data/monitor.db"
TRAEFIK_DYNAMIC_DIR="/opt/traefik/dynamic"
PROJETO_PROTEGIDO="vps-monitor"
LOCK_FILE="/var/lock/project-delete-worker.lock"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date -Iseconds) outra execucao do project-delete-worker.sh ja esta em andamento, saindo." >&2
  exit 0
fi

sqlite3_exec() {
  # ".timeout" (dot-command) nao emite linha de saida, diferente de
  # "PRAGMA busy_timeout=...", que contaminaria a captura via $(...) — erro
  # ja cometido e corrigido no backup-worker.sh, nao repetir aqui.
  sqlite3 -cmd ".timeout 5000" "$DB_PATH" "$1"
}

remover_rotas_traefik() {
  local rotas_json="$1"
  echo "$rotas_json" | python3 -c '
import json, sys, os

DYNAMIC_DIR = "'"$TRAEFIK_DYNAMIC_DIR"'"
arquivos = json.load(sys.stdin)
for nome in arquivos:
    if not nome.startswith("vps-monitor-"):
        print(f"Recusado: {nome} nao comeca com vps-monitor-, nunca removido pelo worker.")
        continue
    caminho = os.path.join(DYNAMIC_DIR, nome)
    if os.path.isfile(caminho):
        os.remove(caminho)
        print(f"Rota removida: {caminho}")
'
}

enfileirar_remocoes_firewall() {
  local regras_json="$1"
  echo "$regras_json" | python3 -c '
import json, sys

PORTAS_PROTEGIDAS = {22, 80, 443}
regras = json.load(sys.stdin)
for r in regras:
    porta = r["porta"]
    if porta in PORTAS_PROTEGIDAS:
        continue
    protocolo = r["protocolo"]
    permitir = 1 if r["permitir"] else 0
    origem = r.get("origem_ip")
    if origem:
        origem_escapado = origem.replace("\x27", "\x27\x27")
        origem_sql = "\x27" + origem_escapado + "\x27"
    else:
        origem_sql = "NULL"
    print(
        "INSERT INTO firewall_rule_request "
        "(acao, permitir, porta, protocolo, origem_ip, status, criado_em, username) VALUES "
        "(\x27remove\x27, " + str(permitir) + ", " + str(porta) + ", \x27" + protocolo + "\x27, "
        + origem_sql + ", \x27pending\x27, datetime(\x27now\x27), \x27project-delete-worker\x27);"
    )
' | while IFS= read -r stmt; do
    sqlite3_exec "$stmt"
  done
}

fazer_delete_projeto() {
  local projeto="$1"
  local rotas_json="$2"
  local regras_json="$3"

  if [ "$projeto" = "$PROJETO_PROTEGIDO" ]; then
    echo "Recusado: projeto '$PROJETO_PROTEGIDO' e protegido, nunca excluido via worker." >&2
    return 1
  fi

  local containers_raw
  containers_raw=$(docker ps -a --filter "label=com.docker.compose.project=$projeto" --format '{{.Names}}')
  if [ -z "$containers_raw" ]; then
    echo "Nenhum container encontrado para o projeto '$projeto'" >&2
    return 1
  fi
  local containers_array=()
  mapfile -t containers_array <<< "$containers_raw"

  if ! docker stop "${containers_array[@]}"; then
    echo "Falha ao parar containers de '$projeto'" >&2
    return 1
  fi

  remover_rotas_traefik "$rotas_json"
  enfileirar_remocoes_firewall "$regras_json"

  local volumes
  volumes=$(for c in "${containers_array[@]}"; do
    docker inspect "$c" --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{"\n"}}{{end}}{{end}}'
  done | sort -u)

  if [ -n "$volumes" ]; then
    while IFS= read -r vol; do
      [ -z "$vol" ] && continue
      if ! docker volume rm "$vol"; then
        echo "AVISO: falha ao remover volume '$vol'" >&2
      fi
    done <<< "$volumes"
  fi

  if ! docker rm "${containers_array[@]}"; then
    echo "AVISO: falha ao remover containers de '$projeto'" >&2
    return 1
  fi

  echo "Projeto '$projeto' excluido: containers e volumes removidos, rotas/regras marcadas processadas."
}

# ---------- 0. Libera jobs presos (worker interrompido no meio de uma execucao) ----------
# 30 minutos: mais generoso que o do firewall (aplicar regra e quase
# instantaneo) mas bem menor que o do backup (2h) — remocao de volumes
# grandes pode demorar, mas nao deveria levar tanto quanto uma copia completa
# de backup.
sqlite3_exec "UPDATE project_delete_request SET status='failed', concluido_em=datetime('now'), erro='Job travado em running por mais de 30min - worker provavelmente interrompido.' WHERE status='running' AND criado_em < datetime('now', '-30 minutes');"

# ---------- 1. Processa no maximo um pedido pendente por execucao ----------
job_linha=$(sqlite3_exec "SELECT id, projeto, rotas_traefik_selecionadas, regras_firewall_selecionadas FROM project_delete_request WHERE status='pending' ORDER BY criado_em LIMIT 1;")

if [ -n "$job_linha" ]; then
  IFS='|' read -r job_id job_projeto job_rotas job_regras <<< "$job_linha"

  sqlite3_exec "UPDATE project_delete_request SET status='running' WHERE id=$job_id;"

  if saida=$(fazer_delete_projeto "$job_projeto" "$job_rotas" "$job_regras" 2>&1); then
    sqlite3_exec "UPDATE project_delete_request SET status='done', concluido_em=datetime('now') WHERE id=$job_id;"
    echo "$saida"
  else
    erro_escapado=$(echo "$saida" | sed "s/'/''/g" | tr '\n' ' ')
    sqlite3_exec "UPDATE project_delete_request SET status='failed', concluido_em=datetime('now'), erro='$erro_escapado' WHERE id=$job_id;"
    echo "$saida" >&2
  fi
fi
```

**Nota:** a query SQL do Step 1 lê `rotas_traefik_selecionadas`/`regras_firewall_selecionadas` (colunas `Text` com JSON) e passa direto como `IFS='|' read`. Como esses campos são JSON (podem conter `|` dentro de strings, embora improvável em nomes de arquivo/porta/protocolo/IP), isso é aceitável para o padrão já estabelecido nos outros workers (que já assumem campos sem `|` na prática); não há necessidade de um parsing mais robusto aqui, já que o conteúdo é gerado pelo próprio backend (nunca digitado livremente pelo usuário nesses campos JSON — vem de nomes de arquivo/porta/protocolo/IP já validados).

- [ ] **Step 2: Checar sintaxe do script**

Run: `bash -n scripts/project-delete-worker.sh`
Expected: sem saída (sintaxe válida).

- [ ] **Step 3: Dar permissão de execução**

Run: `chmod +x scripts/project-delete-worker.sh`

- [ ] **Step 4: Commit**

```bash
git add scripts/project-delete-worker.sh
git commit -m "feat: adiciona worker no host pra executar exclusao completa de projeto"
```

(A instalação do cron acontece na Task 6, deploy.)

---

### Task 5: Frontend — modal de exclusão em `frontend/app/projetos/page.tsx`

**Files:**
- Modify: `frontend/app/projetos/page.tsx`

**Interfaces:**
- Consumes: `GET /api/projects/{projeto}/delete-preview` (Task 2), `POST /api/projects/{projeto}/delete` (Task 3), `POST /api/backups/projects/{projeto}/snapshot` e `GET /api/backups/projects` (já existentes, `frontend/app/backups/page.tsx` como referência de padrão).

- [ ] **Step 1: Reescrever a página**

Substituir o conteúdo de `frontend/app/projetos/page.tsx` por:

```tsx
'use client';
import { useState, useEffect, useCallback, useRef } from 'react';
import api from '../../lib/api';
import Toast from '../../components/Toast';

interface ProjectContainer {
  name: string;
  status: string;
}

interface Project {
  nome: string;
  dominio: string | null;
  container_count: number;
  cpu_percent: number;
  mem_usage_mb: number;
  mem_percent_do_host: number;
  containers: ProjectContainer[];
}

interface RegraCandidata {
  porta: number;
  protocolo: string;
  permitir: boolean;
  origem_ip: string | null;
}

interface DeletePreview {
  containers: ProjectContainer[];
  volumes: string[];
  rotas_candidatas: string[];
  regras_firewall_candidatas: RegraCandidata[];
}

type DeleteEtapa = 'preview' | 'criando-snapshot' | 'confirmacao';

const card: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8, cursor: 'pointer',
};

const input: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
};

const modalOverlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000,
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};

const modalBox: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12,
  width: '90%', maxWidth: 520, padding: 24, maxHeight: '85vh', overflowY: 'auto',
};

export default function ProjetosPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  const [deleteAlvo, setDeleteAlvo] = useState<string | null>(null);
  const [deleteEtapa, setDeleteEtapa] = useState<DeleteEtapa>('preview');
  const [preview, setPreview] = useState<DeletePreview | null>(null);
  const [rotasMarcadas, setRotasMarcadas] = useState<Set<string>>(new Set());
  const [regrasMarcadas, setRegrasMarcadas] = useState<Set<number>>(new Set());
  const [snapshotArquivo, setSnapshotArquivo] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState('');
  const snapshotPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadProjects = useCallback(async () => {
    try { setProjects((await api.get('/projects')).data.projects); } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { setLoading(true); loadProjects(); }, [loadProjects]);

  useEffect(() => {
    const id = setInterval(loadProjects, 30000);
    return () => clearInterval(id);
  }, [loadProjects]);

  useEffect(() => {
    return () => { if (snapshotPollRef.current) clearInterval(snapshotPollRef.current); };
  }, []);

  async function abrirExclusao(nome: string, e: React.MouseEvent) {
    e.stopPropagation();
    setDeleteAlvo(nome);
    setDeleteEtapa('preview');
    setPreview(null);
    setSnapshotArquivo(null);
    setConfirmText('');
    try {
      const r = await api.get(`/projects/${nome}/delete-preview`);
      setPreview(r.data);
      setRotasMarcadas(new Set<string>(r.data.rotas_candidatas));
      setRegrasMarcadas(new Set());
    } catch (e: any) {
      setToast({ msg: e?.response?.data?.detail || 'Erro ao carregar preview', type: 'error' });
      setDeleteAlvo(null);
    }
  }

  function toggleRota(filename: string) {
    setRotasMarcadas((prev) => {
      const next = new Set(prev);
      if (next.has(filename)) next.delete(filename); else next.add(filename);
      return next;
    });
  }

  function toggleRegra(index: number) {
    setRegrasMarcadas((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index); else next.add(index);
      return next;
    });
  }

  async function criarSnapshotEContinuar() {
    if (!deleteAlvo) return;
    setDeleteEtapa('criando-snapshot');
    let contagemAntes = 0;
    try {
      const antes = await api.get('/backups/projects');
      const projetoAntes = antes.data.projects.find((p: any) => p.nome === deleteAlvo);
      contagemAntes = projetoAntes ? projetoAntes.snapshots.length : 0;
      await api.post(`/backups/projects/${deleteAlvo}/snapshot`);
    } catch (e: any) {
      setToast({ msg: e?.response?.data?.detail || 'Erro ao criar snapshot', type: 'error' });
      setDeleteEtapa('preview');
      return;
    }

    let tentativas = 0;
    snapshotPollRef.current = setInterval(async () => {
      tentativas += 1;
      try {
        const r = await api.get('/backups/projects');
        const p = r.data.projects.find((pr: any) => pr.nome === deleteAlvo);
        if (p && p.snapshots.length > contagemAntes) {
          if (snapshotPollRef.current) clearInterval(snapshotPollRef.current);
          setSnapshotArquivo(p.snapshots[0].arquivo);
          setDeleteEtapa('confirmacao');
          return;
        }
        if (p && !p.job_ativo && p.snapshots.length <= contagemAntes) {
          if (snapshotPollRef.current) clearInterval(snapshotPollRef.current);
          setToast({ msg: 'Falha ao criar snapshot, tente novamente', type: 'error' });
          setDeleteEtapa('preview');
        }
      } catch { /* ignore, tenta de novo no proximo ciclo */ }
      if (tentativas > 40) {
        if (snapshotPollRef.current) clearInterval(snapshotPollRef.current);
        setToast({ msg: 'Snapshot demorou demais, tente novamente', type: 'error' });
        setDeleteEtapa('preview');
      }
    }, 3000);
  }

  async function confirmarExclusao() {
    if (!deleteAlvo || !snapshotArquivo || confirmText !== deleteAlvo) return;
    try {
      await api.post(`/projects/${deleteAlvo}/delete`, {
        snapshot_arquivo: snapshotArquivo,
        rotas_selecionadas: Array.from(rotasMarcadas),
        regras_selecionadas: (preview?.regras_firewall_candidatas || []).filter((_, i) => regrasMarcadas.has(i)),
      });
      setToast({ msg: `Exclusão de '${deleteAlvo}' enfileirada`, type: 'success' });
      loadProjects();
    } catch (e: any) {
      setToast({ msg: e?.response?.data?.detail || 'Erro ao excluir projeto', type: 'error' });
    }
    setDeleteAlvo(null);
  }

  function fecharModal() {
    if (snapshotPollRef.current) clearInterval(snapshotPollRef.current);
    setDeleteAlvo(null);
  }

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      {toast && <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />}
      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Projetos</h1>

      {loading && projects.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Carregando...</p>
      )}

      {!loading && projects.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Nenhum projeto encontrado.</p>
      )}

      {projects.map((p) => (
        <div key={p.nome} style={card} onClick={() => setExpanded(expanded === p.nome ? null : p.nome)}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--text)', fontWeight: 600 }}>{p.nome}</span>
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>{p.dominio ?? '—'}</span>
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>{p.container_count} container(s)</span>
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>CPU: {p.cpu_percent.toFixed(1)}%</span>
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>
              RAM: {p.mem_usage_mb.toFixed(0)} MB ({p.mem_percent_do_host.toFixed(1)}% do host)
            </span>
            {p.nome !== 'vps-monitor' && (
              <button
                onClick={(e) => abrirExclusao(p.nome, e)}
                style={{ marginLeft: 'auto', padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 6, color: 'var(--danger)', cursor: 'pointer', fontSize: 12 }}
              >
                Excluir projeto
              </button>
            )}
          </div>

          {expanded === p.nome && (
            <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {p.containers.map((c) => (
                <div key={c.name} style={{
                  padding: '4px 8px', background: 'var(--surface)',
                  border: '1px solid var(--border)', borderRadius: 6, fontSize: 12,
                }}>
                  <span style={{ fontFamily: 'monospace' }}>{c.name}</span>
                  <span style={{ color: 'var(--muted)', marginLeft: 6 }}>{c.status}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {deleteAlvo && (
        <div style={modalOverlay} onClick={fecharModal}>
          <div style={modalBox} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>Excluir projeto &quot;{deleteAlvo}&quot;</h3>

            {!preview && (
              <p style={{ color: 'var(--muted)', fontSize: 14 }}>Carregando preview...</p>
            )}

            {preview && deleteEtapa === 'preview' && (
              <>
                <p style={{ color: 'var(--danger)', fontWeight: 600, marginBottom: 12, fontSize: 14 }}>
                  Isso vai parar e remover permanentemente todos os containers e volumes deste projeto. Essa ação não pode ser desfeita.
                </p>

                <p style={{ color: 'var(--text)', fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Containers ({preview.containers.length})</p>
                <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 12 }}>
                  {preview.containers.map((c) => c.name).join(', ') || 'nenhum'}
                </p>

                <p style={{ color: 'var(--text)', fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Volumes ({preview.volumes.length})</p>
                <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 12 }}>
                  {preview.volumes.join(', ') || 'nenhum'}
                </p>

                <p style={{ color: 'var(--text)', fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Rotas do Traefik a remover</p>
                {preview.rotas_candidatas.length === 0 ? (
                  <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 12 }}>Nenhuma rota candidata encontrada.</p>
                ) : (
                  <div style={{ marginBottom: 12 }}>
                    {preview.rotas_candidatas.map((r) => (
                      <label key={r} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text)', marginBottom: 4 }}>
                        <input type="checkbox" checked={rotasMarcadas.has(r)} onChange={() => toggleRota(r)} />
                        {r}
                      </label>
                    ))}
                  </div>
                )}

                <p style={{ color: 'var(--text)', fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Regras de firewall (sugestões — marque manualmente)</p>
                {preview.regras_firewall_candidatas.length === 0 ? (
                  <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 16 }}>Nenhuma regra candidata encontrada.</p>
                ) : (
                  <div style={{ marginBottom: 16 }}>
                    {preview.regras_firewall_candidatas.map((r, i) => (
                      <label key={`${r.porta}-${r.protocolo}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text)', marginBottom: 4 }}>
                        <input type="checkbox" checked={regrasMarcadas.has(i)} onChange={() => toggleRegra(i)} />
                        {r.porta}/{r.protocolo} — {r.permitir ? 'Permitir' : 'Negar'} — Origem: {r.origem_ip ?? 'Qualquer'}
                      </label>
                    ))}
                  </div>
                )}

                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                  <button onClick={fecharModal} style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}>
                    Cancelar
                  </button>
                  <button onClick={criarSnapshotEContinuar} style={{ padding: '8px 20px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}>
                    Criar snapshot e continuar
                  </button>
                </div>
              </>
            )}

            {deleteEtapa === 'criando-snapshot' && (
              <p style={{ color: 'var(--accent)', fontSize: 14 }}>Criando snapshot de segurança, aguarde...</p>
            )}

            {deleteEtapa === 'confirmacao' && (
              <>
                <p style={{ color: 'var(--muted)', marginBottom: 12, fontSize: 14 }}>
                  Snapshot &quot;{snapshotArquivo}&quot; criado com sucesso. Digite <strong>{deleteAlvo}</strong> pra confirmar a exclusão definitiva:
                </p>
                <input
                  style={{ ...input, marginBottom: 16 }}
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  autoFocus
                />
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                  <button onClick={fecharModal} style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}>
                    Cancelar
                  </button>
                  <button
                    onClick={confirmarExclusao}
                    disabled={confirmText !== deleteAlvo}
                    style={{
                      padding: '8px 20px', border: 'none', borderRadius: 6, fontWeight: 700,
                      background: confirmText === deleteAlvo ? 'var(--danger)' : 'var(--surface)',
                      color: confirmText === deleteAlvo ? '#fff' : 'var(--muted)',
                      cursor: confirmText === deleteAlvo ? 'pointer' : 'not-allowed',
                    }}
                  >
                    Excluir definitivamente
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Build**

Run: `cd frontend && npm run build`
Expected: build limpo, sem erros de tipo.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/projetos/page.tsx
git commit -m "feat: adiciona modal de exclusao completa de projeto em /projetos"
```

(Verificação manual no navegador fica por conta do usuário, combinado nesta sessão.)

---

### Task 6: Deploy para produção

**Files:** nenhum (ação operacional, sem mudança de código)

**Atenção:** esta task só deve ser executada após confirmação explícita do usuário — dá ao script no host permissão de parar/remover containers e apagar volumes reais na VPS de produção. É a ação mais destrutiva de todas as features já deployadas neste projeto. O teste manual desta task deve usar um projeto de teste descartável, nunca o `vps-monitor` nem um projeto de cliente real.

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
ssh root@144.91.92.70 "docker ps --filter name=monitor --format '{{.Names}}\t{{.Status}}'"
```

Expected: todos `Up`, sem `Restarting`.

- [ ] **Step 4: Garantir permissão de execução do script novo**

```bash
ssh root@144.91.92.70 "chmod +x /opt/vps-monitor/monitor/scripts/project-delete-worker.sh && ls -l /opt/vps-monitor/monitor/scripts/project-delete-worker.sh"
```

(Atenção ao padrão já conhecido: scripts commitados como `100644` — se o `git pull` reclamar de mudança local de modo em algum `scripts/*.sh` por causa de um `chmod +x` de um deploy anterior, reverter só o modo com `git checkout -- monitor/scripts/*.sh` antes do pull, e reaplicar `chmod +x` depois, mesmo procedimento já usado no deploy da feature de firewall.)

- [ ] **Step 5: Instalar o cron**

```bash
ssh root@144.91.92.70 "(crontab -l 2>/dev/null; echo '* * * * * /opt/vps-monitor/monitor/scripts/project-delete-worker.sh >> /var/log/project-delete-worker.log 2>&1') | crontab -"
ssh root@144.91.92.70 "crontab -l | grep project-delete-worker"
```

- [ ] **Step 6: Teste manual end-to-end usando um projeto de teste descartável**

1. Subir na VPS um projeto docker-compose mínimo e descartável (ex: um único container `hello-world` ou `nginx` com uma label `com.docker.compose.project=teste-exclusao` e um volume nomeado), só para este teste.
2. Pela UI (`/projetos`), clicar "Excluir projeto" no card `teste-exclusao`, revisar o preview (containers, volumes, candidatas), criar o snapshot, aguardar a confirmação ficar disponível, digitar o nome e confirmar.
3. Aguardar até 1 minuto, confirmar que o container sumiu (`docker ps -a --filter label=com.docker.compose.project=teste-exclusao` vazio) e o volume foi removido (`docker volume ls | grep teste-exclusao` vazio).
4. Confirmar que o projeto sumiu da listagem `/projetos`.
5. **Confirmar que tentar excluir o projeto `vps-monitor` direto via API retorna 400**, nunca chegando a enfileirar nada:
   ```bash
   ssh root@144.91.92.70 "curl -sk -X POST https://monitor.dlsistemas.com.br/api/projects/vps-monitor/delete -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '{\"snapshot_arquivo\":\"x.tar.gz\",\"rotas_selecionadas\":[],\"regras_selecionadas\":[]}'"
   ```
   (Ou mais simples: confirmar pela UI que o card do `vps-monitor` não mostra o botão "Excluir projeto".)
