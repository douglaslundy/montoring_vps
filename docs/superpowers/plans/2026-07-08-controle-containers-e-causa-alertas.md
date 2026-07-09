# Controle de Containers e Causa Provável dos Alertas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adiciona botões de iniciar/parar/reiniciar em cada container na página Containers, e faz cada alerta gravar um snapshot de "causa provável" (top containers por CPU/RAM/rede/disco, ou motivo real de saída para containers parados) para permitir planejar melhorias de infraestrutura com base em dados reais.

**Architecture:** `DockerClient` ganha métodos de controle (start/stop/restart) e leitura de tamanho em disco, expostos por novos endpoints protegidos por JWT em `containers.py`, com log de auditoria em `ContainerActionLog`. O motor de alertas (`alert_engine.py`) monta um dict `contexto` (serializado como JSON em `AlertLog.contexto`) no momento em que cria cada alerta novo, usando os dados de containers já coletados a cada 30s e uma nova coleta de disco por container a cada 10 min (`ContainerDiskUsage`). A API expõe `contexto` em `/api/alerts/history` e `/api/alerts/active`; o frontend exibe tudo isso em UI expansível.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite), httpx (Docker UDS), APScheduler, pytest/pytest-asyncio, Next.js/React (TypeScript).

## Global Constraints

- Sem bloqueio automático de start/stop/restart nos containers do próprio monitor (`monitor-backend`, `monitor-frontend`, `monitor-nginx`) — apenas aviso reforçado na confirmação do frontend.
- Sem contagem real de conexões/acessos simultâneos — tráfego de rede (RX/TX) é o proxy disponível.
- Alertas de Temperatura não recebem `contexto` de container (limitação de hardware).
- Sem backfill de `contexto` para alertas antigos — ficam com `contexto = None`.
- `evaluate()` deve continuar aceitando chamadas com apenas `(metrics, containers)` — `docker_client` é um terceiro parâmetro opcional, para não quebrar os testes existentes em `test_alert_engine.py`.
- Toda rota nova em `containers.py` herda a proteção JWT já aplicada ao router (`_protected` em `main.py`) — nenhuma mudança de auth necessária.

---

### Task 1: Schema — `AlertLog.contexto`, `ContainerDiskUsage`, `ContainerActionLog`

**Files:**
- Modify: `backend/models/database.py`
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Produces: `AlertLog.contexto` (Text, nullable, JSON string) — usado pelas Tasks 5, 6, 8. `ContainerDiskUsage` (id, collected_at, container_id, container_name, size_rw_mb, size_rootfs_mb) — usado pelas Tasks 5, 7. `ContainerActionLog` (id, performed_at, username, container_id, container_name, acao, sucesso, erro) — usado pela Task 3.

- [ ] **Step 1: Escrever os testes que falham**

Adicione ao final de `backend/tests/test_database.py`:

```python
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
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && py -m pytest tests/test_database.py -k "contexto or disk_usage or action_log" -v`
Expected: FAIL — `AttributeError: module has no attribute 'ContainerDiskUsage'` (ou coluna/tabela inexistente).

- [ ] **Step 3: Adicionar a coluna `contexto` ao model `AlertLog`**

Em `backend/models/database.py`, a classe `AlertLog` atual é:

```python
class AlertLog(Base):
    __tablename__ = "alert_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, ForeignKey("alert_rules.id"))
    triggered_at = Column(DateTime, nullable=False)
    resolved_at = Column(DateTime)
    severidade = Column(String)
    metrica = Column(String)
    valor_no_disparo = Column(Float)
    threshold = Column(Float)
    mensagem = Column(Text)
    vps_name = Column(String, nullable=True)
    notificado_email = Column(Integer, default=0)
    notificado_whatsapp = Column(Integer, default=0)
    erro_email = Column(Text)
    erro_whatsapp = Column(Text)
    last_notified_at = Column(DateTime, nullable=True)
```

Substitua por:

```python
class AlertLog(Base):
    __tablename__ = "alert_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, ForeignKey("alert_rules.id"))
    triggered_at = Column(DateTime, nullable=False)
    resolved_at = Column(DateTime)
    severidade = Column(String)
    metrica = Column(String)
    valor_no_disparo = Column(Float)
    threshold = Column(Float)
    mensagem = Column(Text)
    vps_name = Column(String, nullable=True)
    contexto = Column(Text, nullable=True)
    notificado_email = Column(Integer, default=0)
    notificado_whatsapp = Column(Integer, default=0)
    erro_email = Column(Text)
    erro_whatsapp = Column(Text)
    last_notified_at = Column(DateTime, nullable=True)
```

- [ ] **Step 4: Adicionar as classes `ContainerDiskUsage` e `ContainerActionLog`**

Na mesma arquivo, logo após a classe `ContainerMetrics` (antes de `class AlertRule(Base):`), adicione:

```python
class ContainerDiskUsage(Base):
    __tablename__ = "container_disk_usage"
    id = Column(Integer, primary_key=True, autoincrement=True)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    container_id = Column(String, nullable=False)
    container_name = Column(String, nullable=False)
    size_rw_mb = Column(Float)
    size_rootfs_mb = Column(Float)
```

E logo após a classe `AlertLog` (antes de `class Config(Base):`), adicione:

```python
class ContainerActionLog(Base):
    __tablename__ = "container_action_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    performed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    username = Column(String, nullable=False)
    container_id = Column(String, nullable=False)
    container_name = Column(String, nullable=False)
    acao = Column(String, nullable=False)
    sucesso = Column(Integer, default=1)
    erro = Column(Text, nullable=True)
```

- [ ] **Step 5: Adicionar migração da coluna `contexto` em `init_db()`**

Em `backend/models/database.py`, dentro de `init_db()`, o bloco de migrações leves atual é:

```python
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE alert_log ADD COLUMN last_notified_at DATETIME"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
        try:
            conn.execute(text("ALTER TABLE alert_log ADD COLUMN vps_name VARCHAR"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
```

Substitua por:

```python
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE alert_log ADD COLUMN last_notified_at DATETIME"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
        try:
            conn.execute(text("ALTER TABLE alert_log ADD COLUMN vps_name VARCHAR"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
        try:
            conn.execute(text("ALTER TABLE alert_log ADD COLUMN contexto TEXT"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
```

`Base.metadata.create_all(engine)` (já presente no início de `init_db()`) cria `container_disk_usage` e `container_action_log` automaticamente — sem migração manual necessária para tabelas novas.

- [ ] **Step 6: Rodar toda a suíte de `test_database.py` e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_database.py -v`
Expected: todos os testes passam, incluindo os 5 novos.

- [ ] **Step 7: Commit**

```bash
git add backend/models/database.py backend/tests/test_database.py
git commit -m "feat: adiciona AlertLog.contexto, ContainerDiskUsage e ContainerActionLog"
```

---

### Task 2: `DockerClient` — controle de containers e leitura de tamanho em disco

**Files:**
- Modify: `backend/collector/docker_client.py`
- Test: `backend/tests/test_docker_client.py`

**Interfaces:**
- Produces: `DockerClient.start_container(id)`, `.stop_container(id, timeout=10)`, `.restart_container(id, timeout=10)` (todos `async`, retornam `None`, levantam `httpx.HTTPStatusError` em falha exceto HTTP 304) — usados pela Task 3. `DockerClient.list_containers_with_size() -> list[dict]` (async, cada dict com `Id`, `Names`, `SizeRw`, `SizeRootFs`) — usado pela Task 7.

- [ ] **Step 1: Escrever os testes que falham**

Adicione ao final de `backend/tests/test_docker_client.py`:

```python
@pytest.mark.asyncio
async def test_start_container_chama_endpoint_correto():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        await client.start_container("abc123")

    mock_http.post.assert_called_once_with("/containers/abc123/start", params={})


@pytest.mark.asyncio
async def test_stop_container_passa_timeout():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        await client.stop_container("abc123", timeout=5)

    mock_http.post.assert_called_once_with("/containers/abc123/stop", params={"t": 5})


@pytest.mark.asyncio
async def test_restart_container_trata_304_como_sucesso():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 304
    mock_response.raise_for_status = MagicMock(side_effect=AssertionError("não deveria chamar raise_for_status em 304"))
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        await client.restart_container("abc123")  # não deve levantar exceção


@pytest.mark.asyncio
async def test_start_container_propaga_erro_404():
    import httpx
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("not found", request=MagicMock(), response=mock_response)
    )
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        with pytest.raises(httpx.HTTPStatusError):
            await client.start_container("inexistente")


@pytest.mark.asyncio
async def test_list_containers_with_size():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_data = [
        {"Id": "abc123def456", "Names": ["/meu-container"], "SizeRw": 13107200, "SizeRootFs": 356515840},
    ]
    mock_http = _make_mock_http_client(mock_data)
    with patch.object(client, "_client", return_value=mock_http):
        result = await client.list_containers_with_size()

    assert result == mock_data
    mock_http.get.assert_called_once_with("/containers/json", params={"all": True, "size": True})
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && py -m pytest tests/test_docker_client.py -k "start_container or stop_container or restart_container or list_containers_with_size" -v`
Expected: FAIL — `AttributeError: 'DockerClient' object has no attribute 'start_container'`.

- [ ] **Step 3: Implementar os métodos de controle e leitura de tamanho**

Em `backend/collector/docker_client.py`, o método `container_inspect` atual é:

```python
    async def container_inspect(self, container_id: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"/containers/{container_id}/json")
            r.raise_for_status()
            return r.json()
```

Logo após esse método (antes de `get_logs`), adicione:

```python
    async def _post_action(self, container_id: str, action: str, params: Optional[dict] = None) -> None:
        async with self._client() as c:
            r = await c.post(f"/containers/{container_id}/{action}", params=params or {})
            if r.status_code == 304:
                return
            r.raise_for_status()

    async def start_container(self, container_id: str) -> None:
        await self._post_action(container_id, "start")

    async def stop_container(self, container_id: str, timeout: int = 10) -> None:
        await self._post_action(container_id, "stop", {"t": timeout})

    async def restart_container(self, container_id: str, timeout: int = 10) -> None:
        await self._post_action(container_id, "restart", {"t": timeout})

    async def list_containers_with_size(self) -> list[dict]:
        async with self._client() as c:
            r = await c.get("/containers/json", params={"all": True, "size": True})
            r.raise_for_status()
            return r.json()
```

- [ ] **Step 4: Rodar toda a suíte de `test_docker_client.py` e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_docker_client.py -v`
Expected: todos os testes passam, incluindo os 5 novos.

- [ ] **Step 5: Commit**

```bash
git add backend/collector/docker_client.py backend/tests/test_docker_client.py
git commit -m "feat: DockerClient ganha start/stop/restart e list_containers_with_size"
```

---

### Task 3: API — endpoints de start/stop/restart com log de auditoria

**Files:**
- Modify: `backend/api/containers.py`
- Test: `backend/tests/test_containers_api.py`

**Interfaces:**
- Consumes: `DockerClient.start_container/stop_container/restart_container` (Task 2); `ContainerActionLog` (Task 1); `get_token_data` de `backend/api/auth.py` (já existe, retorna dict com chave `"sub"`).
- Produces: `POST /api/containers/{id}/start|stop|restart` — usados pela Task 9.

- [ ] **Step 1: Escrever os testes que falham**

Adicione ao final de `backend/tests/test_containers_api.py`:

```python
def test_start_container_sucesso(auth_client):
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.start_container = AsyncMock(return_value=None)
        r = auth_client.post("/api/containers/abc123/start")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    mock_dc.start_container.assert_awaited_once_with("abc123")


def test_stop_container_sucesso(auth_client):
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.stop_container = AsyncMock(return_value=None)
        r = auth_client.post("/api/containers/abc123/stop")
    assert r.status_code == 200
    mock_dc.stop_container.assert_awaited_once_with("abc123")


def test_restart_container_sucesso(auth_client):
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.restart_container = AsyncMock(return_value=None)
        r = auth_client.post("/api/containers/abc123/restart")
    assert r.status_code == 200
    mock_dc.restart_container.assert_awaited_once_with("abc123")


def test_start_container_registra_log_de_sucesso(auth_client, test_db):
    from sqlalchemy.orm import Session
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.start_container = AsyncMock(return_value=None)
        auth_client.post("/api/containers/abc123/start")

    with Session(test_db.engine) as session:
        log = session.query(test_db.ContainerActionLog).first()
    assert log is not None
    assert log.acao == "start"
    assert log.container_name == "web"
    assert log.sucesso == 1
    assert log.username == "admin"


def test_stop_container_erro_registra_log_de_falha(auth_client, test_db):
    import httpx
    from sqlalchemy.orm import Session
    mock_response = MagicMock()
    mock_response.status_code = 500
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.stop_container = AsyncMock(
            side_effect=httpx.HTTPStatusError("erro", request=MagicMock(), response=mock_response)
        )
        r = auth_client.post("/api/containers/abc123/stop")

    assert r.status_code == 502
    with Session(test_db.engine) as session:
        log = session.query(test_db.ContainerActionLog).first()
    assert log.sucesso == 0
    assert log.acao == "stop"


def test_start_container_404_quando_container_nao_existe(auth_client):
    import httpx
    mock_response = MagicMock()
    mock_response.status_code = 404
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": []}):
        mock_dc.start_container = AsyncMock(
            side_effect=httpx.HTTPStatusError("nao encontrado", request=MagicMock(), response=mock_response)
        )
        r = auth_client.post("/api/containers/inexistente/start")
    assert r.status_code == 404


def test_control_endpoints_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.post("/api/containers/abc123/start").status_code == 401
    assert client.post("/api/containers/abc123/stop").status_code == 401
    assert client.post("/api/containers/abc123/restart").status_code == 401
```

O fixture `auth_client` já existe no topo de `test_containers_api.py` e usa `test_db` (de `conftest.py`) — ambos já disponíveis nos testes atuais, nenhuma mudança de fixture necessária.

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && py -m pytest tests/test_containers_api.py -k "start_container or stop_container or restart_container or control_endpoints" -v`
Expected: FAIL — `404 Not Found` (rota ainda não existe) nos testes que esperam 200/502.

- [ ] **Step 3: Implementar os endpoints**

O arquivo `backend/api/containers.py` atual é:

```python
from fastapi import APIRouter
from collector.scheduler import docker_client, get_last_metrics

containers_router = APIRouter()


@containers_router.get("/containers")
def list_containers():
    metrics = get_last_metrics()
    return {"containers": metrics.get("containers", [])}


@containers_router.get("/containers/{container_id}/logs")
async def get_logs(container_id: str, tail: int = 100):
    logs = await docker_client.get_logs(container_id, tail=tail)
    return {"logs": logs}
```

Substitua por:

```python
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_token_data
from collector.scheduler import docker_client, get_last_metrics
from models.database import ContainerActionLog, engine

containers_router = APIRouter()


@containers_router.get("/containers")
def list_containers():
    metrics = get_last_metrics()
    return {"containers": metrics.get("containers", [])}


@containers_router.get("/containers/{container_id}/logs")
async def get_logs(container_id: str, tail: int = 100):
    logs = await docker_client.get_logs(container_id, tail=tail)
    return {"logs": logs}


def _container_name(container_id: str) -> str:
    metrics = get_last_metrics()
    for c in metrics.get("containers", []):
        if c.get("id") == container_id or c.get("id_full") == container_id:
            return c.get("name", container_id)
    return container_id


async def _run_action(container_id: str, acao: str, fn, token_data: dict) -> dict:
    container_name = _container_name(container_id)
    username = token_data.get("sub", "desconhecido")

    try:
        await fn(container_id)
    except httpx.HTTPStatusError as e:
        erro = str(e)
        status_code = 404 if e.response.status_code == 404 else 502
        with Session(engine) as session:
            session.add(ContainerActionLog(
                username=username, container_id=container_id, container_name=container_name,
                acao=acao, sucesso=0, erro=erro,
            ))
            session.commit()
        raise HTTPException(status_code=status_code, detail=f"Falha ao {acao} container: {erro}")

    with Session(engine) as session:
        session.add(ContainerActionLog(
            username=username, container_id=container_id, container_name=container_name,
            acao=acao, sucesso=1, erro=None,
        ))
        session.commit()
    return {"ok": True}


@containers_router.post("/containers/{container_id}/start")
async def start_container(container_id: str, token_data: dict = Depends(get_token_data)):
    return await _run_action(container_id, "start", docker_client.start_container, token_data)


@containers_router.post("/containers/{container_id}/stop")
async def stop_container(container_id: str, token_data: dict = Depends(get_token_data)):
    return await _run_action(container_id, "stop", docker_client.stop_container, token_data)


@containers_router.post("/containers/{container_id}/restart")
async def restart_container(container_id: str, token_data: dict = Depends(get_token_data)):
    return await _run_action(container_id, "restart", docker_client.restart_container, token_data)
```

- [ ] **Step 4: Rodar toda a suíte de `test_containers_api.py` e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_containers_api.py -v`
Expected: todos os testes passam, incluindo os 7 novos.

- [ ] **Step 5: Commit**

```bash
git add backend/api/containers.py backend/tests/test_containers_api.py
git commit -m "feat: endpoints de start/stop/restart de containers com log de auditoria"
```

---

### Task 4: `docker-compose.yml` — remover `:ro` do socket Docker

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:**
- Nenhuma (mudança de infraestrutura, sem código consumido/produzido).

- [ ] **Step 1: Remover o `:ro` do mount do `docker.sock`**

Em `docker-compose.yml`, a linha atual (dentro de `monitor-backend.volumes`) é:

```yaml
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

Substitua por:

```yaml
      - /var/run/docker.sock:/var/run/docker.sock
```

- [ ] **Step 2: Validar sintaxe do compose**

Run: `docker compose config --quiet`
Expected: nenhum erro de sintaxe (comando não falha).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "fix: remove :ro do mount do docker.sock (backend agora controla containers)"
```

---

### Task 5: Motor de alertas — contexto para CPU/RAM/Load/Disco

**Files:**
- Modify: `backend/notifications/alert_engine.py`
- Test: `backend/tests/test_alert_engine.py`

**Interfaces:**
- Consumes: campos `cpu_percent`, `mem_percent`, `net_rx_mb`, `net_tx_mb`, `name` de cada item da lista `containers` (já produzidos por `DockerClient.collect_all()`); `ContainerDiskUsage` (Task 1).
- Produces: `_build_metric_context(metrica: str, containers: list, session: Session) -> dict | None` — usado também pela Task 6 indiretamente (via `_evaluate_rule`). `AlertLog.contexto` passa a ser preenchido para alertas de métrica.

- [ ] **Step 1: Escrever os testes que falham**

Adicione a `backend/tests/test_alert_engine.py`, ao final do arquivo:

```python
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
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -k "contexto" -v`
Expected: FAIL — `TypeError: the JSON object must be str... not None` ou `AssertionError` (campo `contexto` ainda não existe/é sempre `None`).

- [ ] **Step 3: Adicionar imports e as funções auxiliares de contexto**

Em `backend/notifications/alert_engine.py`, o topo do arquivo atual é:

```python
import logging
import re
from datetime import datetime

from sqlalchemy.orm import Session

from models.database import AlertLog, AlertRule, engine
```

Substitua por:

```python
import json
import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from models.database import AlertLog, AlertRule, ContainerDiskUsage, engine
```

Logo após a definição de `_OPERATORS` (antes de `_get_metric_value`), adicione:

```python
def _top_by(containers: list, key: str, n: int = 3) -> list:
    ranked = sorted(
        (c for c in containers if c.get(key) is not None),
        key=lambda c: c[key], reverse=True,
    )[:n]
    return [{"nome": c.get("name", "?"), "valor": round(c[key], 1)} for c in ranked]


def _top_by_rede(containers: list, n: int = 3) -> list:
    def trafego(c):
        return (c.get("net_rx_mb") or 0) + (c.get("net_tx_mb") or 0)
    ranked = sorted(containers, key=trafego, reverse=True)[:n]
    return [
        {"nome": c.get("name", "?"), "valor_mb": round(trafego(c), 1)}
        for c in ranked if trafego(c) > 0
    ]


def _top_disco(session: Session, n: int = 3) -> list:
    latest = (
        session.query(ContainerDiskUsage.collected_at)
        .order_by(ContainerDiskUsage.collected_at.desc())
        .first()
    )
    if latest is None:
        return []
    rows = (
        session.query(ContainerDiskUsage)
        .filter(ContainerDiskUsage.collected_at == latest[0])
        .order_by(ContainerDiskUsage.size_rw_mb.desc())
        .limit(n)
        .all()
    )
    return [{"nome": r.container_name, "valor_mb": round(r.size_rw_mb or 0, 1)} for r in rows]


def _build_metric_context(metrica: str, containers: list, session: Session) -> Optional[dict]:
    if metrica in ("cpu_percent", "load_1m"):
        ctx = {}
        top_cpu = _top_by(containers, "cpu_percent")
        top_rede = _top_by_rede(containers)
        if top_cpu:
            ctx["top_cpu"] = top_cpu
        if top_rede:
            ctx["top_rede"] = top_rede
        return ctx or None
    if metrica == "ram_percent":
        ctx = {}
        top_mem = _top_by(containers, "mem_percent")
        top_rede = _top_by_rede(containers)
        if top_mem:
            ctx["top_mem"] = top_mem
        if top_rede:
            ctx["top_rede"] = top_rede
        return ctx or None
    if metrica == "disk_percent":
        top_disco = _top_disco(session)
        return {"top_disco": top_disco} if top_disco else None
    return None
```

- [ ] **Step 4: Passar `containers` para `_evaluate_rule` e gravar `contexto`**

Na mesma arquivo, a assinatura e o corpo de `_evaluate_rule` atuais são:

```python
def _evaluate_rule(session: Session, rule: AlertRule, value: float, mensagem: str, now: datetime, vps_name: str):
    op = _OPERATORS.get(rule.operador)
    if op is None or value is None:
        return

    condition_true = op(value, rule.threshold)

    open_log = (
        session.query(AlertLog)
        .filter(AlertLog.rule_id == rule.id, AlertLog.resolved_at.is_(None))
        .first()
    )

    if condition_true and open_log is None:
        session.add(AlertLog(
            rule_id=rule.id,
            triggered_at=now,
            severidade=rule.severidade,
            metrica=rule.metrica,
            valor_no_disparo=value,
            threshold=rule.threshold,
            mensagem=mensagem,
            vps_name=vps_name,
        ))
```

Substitua a assinatura e o branch `if condition_true and open_log is None:` por:

```python
def _evaluate_rule(session: Session, rule: AlertRule, value: float, mensagem: str, now: datetime, vps_name: str, containers: list):
    op = _OPERATORS.get(rule.operador)
    if op is None or value is None:
        return

    condition_true = op(value, rule.threshold)

    open_log = (
        session.query(AlertLog)
        .filter(AlertLog.rule_id == rule.id, AlertLog.resolved_at.is_(None))
        .first()
    )

    if condition_true and open_log is None:
        contexto = _build_metric_context(rule.metrica, containers, session)
        session.add(AlertLog(
            rule_id=rule.id,
            triggered_at=now,
            severidade=rule.severidade,
            metrica=rule.metrica,
            valor_no_disparo=value,
            threshold=rule.threshold,
            mensagem=mensagem,
            vps_name=vps_name,
            contexto=json.dumps(contexto) if contexto else None,
        ))
```

(O restante do corpo de `_evaluate_rule` — branches `elif` — não muda.)

- [ ] **Step 5: Repassar `containers` na chamada de `_evaluate_rule` dentro de `evaluate()`**

Em `evaluate()`, a linha atual:

```python
                        mensagem = f"{rule.nome}: {value:.1f} {rule.operador} {rule.threshold}"
                        _evaluate_rule(session, rule, value, mensagem, now, vps_name)
```

Substitua por:

```python
                        mensagem = f"{rule.nome}: {value:.1f} {rule.operador} {rule.threshold}"
                        _evaluate_rule(session, rule, value, mensagem, now, vps_name, containers)
```

- [ ] **Step 6: Rodar toda a suíte de `test_alert_engine.py` e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -v`
Expected: todos os testes passam (os já existentes continuam passando porque `evaluate(metrics, containers)` mantém a mesma assinatura de 2 argumentos obrigatórios; só `_evaluate_rule`, interno, ganhou parâmetro novo).

- [ ] **Step 7: Commit**

```bash
git add backend/notifications/alert_engine.py backend/tests/test_alert_engine.py
git commit -m "feat: alertas de CPU/RAM/Load/Disco gravam contexto de causa provavel"
```

---

### Task 6: Motor de alertas — contexto de motivo real para Container Parado

**Files:**
- Modify: `backend/notifications/alert_engine.py`
- Modify: `backend/collector/scheduler.py`
- Test: `backend/tests/test_alert_engine.py`

**Interfaces:**
- Consumes: `DockerClient.container_inspect(container_id) -> dict` (já existe, retorna JSON com chave `State`).
- Produces: `evaluate(metrics, containers, docker_client=None)` — `docker_client` é opcional e usado só pelo caminho de `container_stopped`; chamada real em produção passa a incluir o `docker_client` do scheduler.

- [ ] **Step 1: Escrever os testes que falham**

Adicione a `backend/tests/test_alert_engine.py`, ao final do arquivo:

```python
def test_container_stopped_grava_contexto_com_motivo_real(fresh_db):
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
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -k "container_stopped_grava or sem_docker_client or inspect_falha" -v`
Expected: FAIL — `TypeError: evaluate() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Tornar `_evaluate_container_stopped` assíncrona e capturar o motivo real**

Em `backend/notifications/alert_engine.py`, a função `_evaluate_container_stopped` atual é:

```python
def _evaluate_container_stopped(session: Session, rule: AlertRule, containers: list, now: datetime, vps_name: str):
    """Avalia regra especial de container parado — uma instância por container."""
    for c in containers:
        if c.get("status") == "running":
            continue

        name = c.get("name", "unknown")
        container_mensagem = f"Container '{name}' parado"

        open_log = (
            session.query(AlertLog)
            .filter(
                AlertLog.rule_id == rule.id,
                AlertLog.resolved_at.is_(None),
                AlertLog.mensagem == container_mensagem,
            )
            .first()
        )

        if open_log is None:
            session.add(AlertLog(
                rule_id=rule.id,
                triggered_at=now,
                severidade=rule.severidade,
                metrica="container_stopped",
                valor_no_disparo=1,
                threshold=1,
                mensagem=container_mensagem,
                vps_name=vps_name,
            ))
```

Substitua por:

```python
async def _evaluate_container_stopped(session: Session, rule: AlertRule, containers: list, now: datetime, vps_name: str, docker_client=None):
    """Avalia regra especial de container parado — uma instância por container."""
    for c in containers:
        if c.get("status") == "running":
            continue

        name = c.get("name", "unknown")
        container_mensagem = f"Container '{name}' parado"

        open_log = (
            session.query(AlertLog)
            .filter(
                AlertLog.rule_id == rule.id,
                AlertLog.resolved_at.is_(None),
                AlertLog.mensagem == container_mensagem,
            )
            .first()
        )

        if open_log is None:
            contexto = None
            container_id = c.get("id_full") or c.get("id")
            if docker_client is not None and container_id:
                try:
                    inspect = await docker_client.container_inspect(container_id)
                    state = inspect.get("State", {})
                    contexto = {
                        "exit_code": state.get("ExitCode"),
                        "oom_killed": state.get("OOMKilled"),
                        "erro": state.get("Error") or None,
                        "finalizado_em": state.get("FinishedAt"),
                    }
                except Exception:
                    logger.exception("Erro ao inspecionar container parado %s", name)
                    contexto = None

            session.add(AlertLog(
                rule_id=rule.id,
                triggered_at=now,
                severidade=rule.severidade,
                metrica="container_stopped",
                valor_no_disparo=1,
                threshold=1,
                mensagem=container_mensagem,
                vps_name=vps_name,
                contexto=json.dumps(contexto) if contexto else None,
            ))
```

(O bloco de resolução de alertas de container, logo abaixo no mesmo método, não muda — continua fora do `for` e do `if open_log is None`.)

- [ ] **Step 4: Atualizar `evaluate()` para aceitar `docker_client` e usar `await` na chamada**

A função `evaluate()` atual (assinatura e chamada relevante) é:

```python
async def evaluate(metrics: dict, containers: list) -> list:
    """Avalia todas as regras ativas e retorna lista de alertas ativos."""
    now = datetime.utcnow()
    try:
        with Session(engine) as session:
            from api.config import get_config
            vps_name = get_config(session, "server_name", "VPS Monitor")

            rules = session.query(AlertRule).filter(AlertRule.ativo == 1).all()

            for rule in rules:
                try:
                    if rule.metrica == "container_stopped":
                        _evaluate_container_stopped(session, rule, containers, now, vps_name)
                    else:
```

Substitua por:

```python
async def evaluate(metrics: dict, containers: list, docker_client=None) -> list:
    """Avalia todas as regras ativas e retorna lista de alertas ativos."""
    now = datetime.utcnow()
    try:
        with Session(engine) as session:
            from api.config import get_config
            vps_name = get_config(session, "server_name", "VPS Monitor")

            rules = session.query(AlertRule).filter(AlertRule.ativo == 1).all()

            for rule in rules:
                try:
                    if rule.metrica == "container_stopped":
                        await _evaluate_container_stopped(session, rule, containers, now, vps_name, docker_client)
                    else:
```

- [ ] **Step 5: Repassar o `docker_client` real a partir do scheduler**

Em `backend/collector/scheduler.py`, a linha atual dentro de `collect_and_store()` é:

```python
        active_alerts = await evaluate(host, containers)
```

Substitua por:

```python
        active_alerts = await evaluate(host, containers, docker_client)
```

- [ ] **Step 6: Rodar toda a suíte de `test_alert_engine.py` e `test_scheduler.py` e confirmar que passam**

Run: `cd backend && py -m pytest tests/test_alert_engine.py tests/test_scheduler.py -v`
Expected: todos os testes de `test_alert_engine.py` passam (incluindo os 3 novos). Em `test_scheduler.py`, `test_collect_and_store_salva_no_banco` deve continuar no mesmo estado de antes desta task (falha pré-existente não relacionada — ver Task 11).

- [ ] **Step 7: Commit**

```bash
git add backend/notifications/alert_engine.py backend/collector/scheduler.py backend/tests/test_alert_engine.py
git commit -m "feat: alerta de container parado grava motivo real (exit code, OOM)"
```

---

### Task 7: Scheduler — coleta de uso de disco por container

**Files:**
- Modify: `backend/collector/scheduler.py`
- Test: `backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `DockerClient.list_containers_with_size()` (Task 2); `ContainerDiskUsage` (Task 1).
- Produces: `collect_disk_usage()` (async, sem retorno) — registrado como job do APScheduler; usado pela Task 5 (`_top_disco` lê os dados que este job grava).

- [ ] **Step 1: Escrever o teste que falha**

Adicione ao final de `backend/tests/test_scheduler.py`:

```python
@pytest.mark.asyncio
async def test_collect_disk_usage_salva_no_banco(test_db):
    mock_data = [
        {"Id": "abc123def456", "Names": ["/logs-service"], "SizeRw": 13107200, "SizeRootFs": 356515840},
        {"Id": "def456abc123", "Names": ["/db"], "SizeRw": 1048576, "SizeRootFs": 209715200},
    ]

    import collector.scheduler as sched
    from sqlalchemy.orm import Session
    from models.database import ContainerDiskUsage

    with patch.object(sched.docker_client, "list_containers_with_size", AsyncMock(return_value=mock_data)):
        await sched.collect_disk_usage()

    with Session(test_db.engine) as session:
        rows = session.query(ContainerDiskUsage).order_by(ContainerDiskUsage.size_rw_mb.desc()).all()
    assert len(rows) == 2
    assert rows[0].container_name == "logs-service"
    assert rows[0].size_rw_mb == pytest.approx(12.5, abs=0.1)
    assert rows[0].size_rootfs_mb == pytest.approx(340.0, abs=0.1)


@pytest.mark.asyncio
async def test_collect_disk_usage_erro_docker_nao_lanca_excecao(test_db):
    import collector.scheduler as sched
    with patch.object(sched.docker_client, "list_containers_with_size", AsyncMock(side_effect=Exception("socket indisponivel"))):
        await sched.collect_disk_usage()  # não deve levantar
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_scheduler.py -k disk_usage -v`
Expected: FAIL — `AttributeError: module 'collector.scheduler' has no attribute 'collect_disk_usage'`.

- [ ] **Step 3: Implementar `collect_disk_usage()` e registrar o job**

Em `backend/collector/scheduler.py`, o import de models atual é:

```python
from models.database import ContainerMetrics, MetricsHistory, engine
```

Substitua por:

```python
from models.database import ContainerDiskUsage, ContainerMetrics, MetricsHistory, engine
```

Logo após a função `collect_and_store()` (antes de `_cleanup`), adicione:

```python
async def collect_disk_usage():
    try:
        containers = await docker_client.list_containers_with_size()
    except Exception:
        logger.exception("Erro ao coletar uso de disco dos containers")
        return

    now = datetime.utcnow()
    with Session(engine) as session:
        for c in containers:
            name = (c["Names"][0].lstrip("/") if c.get("Names") else c["Id"][:12])
            session.add(ContainerDiskUsage(
                collected_at=now,
                container_id=c["Id"][:12],
                container_name=name,
                size_rw_mb=round((c.get("SizeRw") or 0) / 1024 ** 2, 1),
                size_rootfs_mb=round((c.get("SizeRootFs") or 0) / 1024 ** 2, 1),
            ))
        session.commit()
```

Em `_cleanup()`, o bloco final atual é:

```python
    with Session(engine) as session:
        session.query(MetricsHistory).filter(MetricsHistory.collected_at < detailed_cutoff).delete()
        session.query(ContainerMetrics).filter(ContainerMetrics.collected_at < aggregated_cutoff).delete()
        session.commit()
```

Substitua por:

```python
    with Session(engine) as session:
        session.query(MetricsHistory).filter(MetricsHistory.collected_at < detailed_cutoff).delete()
        session.query(ContainerMetrics).filter(ContainerMetrics.collected_at < aggregated_cutoff).delete()
        session.query(ContainerDiskUsage).filter(ContainerDiskUsage.collected_at < aggregated_cutoff).delete()
        session.commit()
```

Em `start_scheduler()`, a função atual é:

```python
def start_scheduler():
    scheduler.add_job(collect_and_store, "interval", seconds=30, id="collect", replace_existing=True)
    scheduler.add_job(_cleanup, "interval", hours=1, id="cleanup", replace_existing=True)
    if not scheduler.running:
        scheduler.start()
    asyncio.ensure_future(collect_and_store())
```

Substitua por:

```python
def start_scheduler():
    scheduler.add_job(collect_and_store, "interval", seconds=30, id="collect", replace_existing=True)
    scheduler.add_job(collect_disk_usage, "interval", minutes=10, id="disk_usage", replace_existing=True)
    scheduler.add_job(_cleanup, "interval", hours=1, id="cleanup", replace_existing=True)
    if not scheduler.running:
        scheduler.start()
    asyncio.ensure_future(collect_and_store())
    asyncio.ensure_future(collect_disk_usage())
```

- [ ] **Step 4: Rodar toda a suíte de `test_scheduler.py` e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_scheduler.py -v`
Expected: os 2 testes novos passam. `test_collect_and_store_salva_no_banco` permanece no mesmo estado pré-existente (ver Task 11).

- [ ] **Step 5: Commit**

```bash
git add backend/collector/scheduler.py backend/tests/test_scheduler.py
git commit -m "feat: coleta uso de disco por container a cada 10 min"
```

---

### Task 8: API de alertas — expor `contexto`

**Files:**
- Modify: `backend/api/alerts.py`
- Test: `backend/tests/test_alerts_api.py`

**Interfaces:**
- Consumes: `AlertLog.contexto` (Task 1).
- Produces: `/api/alerts/active` e `/api/alerts/history` retornam `"contexto": dict | None` em cada item — usado pela Task 10.

- [ ] **Step 1: Escrever os testes que falham**

Adicione ao final de `backend/tests/test_alerts_api.py`:

```python
def test_history_inclui_contexto_desserializado(client):
    import json
    import models.database as db_module
    from datetime import datetime
    with db_module.Session(db_module.engine) as s:
        s.add(db_module.AlertLog(
            rule_id=None, triggered_at=datetime.utcnow(), severidade="critico",
            metrica="cpu_percent", mensagem="teste",
            contexto=json.dumps({"top_cpu": [{"nome": "api", "valor": 90.0}]}),
        ))
        s.commit()

    r = client.get("/api/alerts/history", headers=auth(client))
    assert r.status_code == 200
    assert r.json()[0]["contexto"] == {"top_cpu": [{"nome": "api", "valor": 90.0}]}


def test_history_alerta_sem_contexto_retorna_none(client):
    import models.database as db_module
    from datetime import datetime
    with db_module.Session(db_module.engine) as s:
        s.add(db_module.AlertLog(
            rule_id=None, triggered_at=datetime.utcnow(), severidade="aviso",
            metrica="temperature_c", mensagem="teste sem contexto",
        ))
        s.commit()

    r = client.get("/api/alerts/history", headers=auth(client))
    assert r.status_code == 200
    assert r.json()[0]["contexto"] is None
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && py -m pytest tests/test_alerts_api.py -k contexto -v`
Expected: FAIL — `KeyError: 'contexto'`.

- [ ] **Step 3: Adicionar `contexto` ao `_log_dict`**

Em `backend/api/alerts.py`, o topo do arquivo atual é:

```python
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import verify_token_header
from models.database import AlertLog, AlertRule, get_session
```

Substitua por:

```python
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import verify_token_header
from models.database import AlertLog, AlertRule, get_session
```

A função `_log_dict` atual é:

```python
def _log_dict(a: AlertLog) -> dict:
    return {
        "id": a.id,
        "rule_id": a.rule_id,
        "triggered_at": a.triggered_at.isoformat() + "Z" if a.triggered_at else None,
        "resolved_at": a.resolved_at.isoformat() + "Z" if a.resolved_at else None,
        "severidade": a.severidade,
        "metrica": a.metrica,
        "valor_no_disparo": a.valor_no_disparo,
        "threshold": a.threshold,
        "mensagem": a.mensagem,
        "vps_name": a.vps_name,
    }
```

Substitua por:

```python
def _log_dict(a: AlertLog) -> dict:
    return {
        "id": a.id,
        "rule_id": a.rule_id,
        "triggered_at": a.triggered_at.isoformat() + "Z" if a.triggered_at else None,
        "resolved_at": a.resolved_at.isoformat() + "Z" if a.resolved_at else None,
        "severidade": a.severidade,
        "metrica": a.metrica,
        "valor_no_disparo": a.valor_no_disparo,
        "threshold": a.threshold,
        "mensagem": a.mensagem,
        "vps_name": a.vps_name,
        "contexto": json.loads(a.contexto) if a.contexto else None,
    }
```

- [ ] **Step 4: Rodar toda a suíte de `test_alerts_api.py` e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_alerts_api.py -v`
Expected: todos os testes passam, incluindo os 2 novos.

- [ ] **Step 5: Commit**

```bash
git add backend/api/alerts.py backend/tests/test_alerts_api.py
git commit -m "feat: API de alertas expoe contexto de causa provavel"
```

---

### Task 9: Frontend — botões de controle de containers

**Files:**
- Modify: `frontend/components/ContainerRow.tsx`
- Modify: `frontend/app/containers/page.tsx`

**Interfaces:**
- Produces: prop `onAction?: (id: string, name: string, action: 'start' | 'stop' | 'restart') => void` e `actionLoading?: string | null` em `ContainerRow`.
- Consumes: `POST /api/containers/{id}/start|stop|restart` (Task 3), via `api` (`frontend/lib/api.ts`, já existente).

- [ ] **Step 1: Adicionar os botões de ação em `ContainerRow.tsx`**

Em `frontend/components/ContainerRow.tsx`, a interface `Props` e a assinatura do componente atuais são:

```tsx
interface Props {
  container: ContainerMetric;
  onViewLogs?: (id: string, name: string) => void;
  onToggleExpand?: () => void;
  isExpanded?: boolean;
}
```

```tsx
export default function ContainerRow({ container, onViewLogs, onToggleExpand, isExpanded }: Props) {
```

Substitua por:

```tsx
interface Props {
  container: ContainerMetric;
  onViewLogs?: (id: string, name: string) => void;
  onToggleExpand?: () => void;
  isExpanded?: boolean;
  onAction?: (id: string, name: string, action: 'start' | 'stop' | 'restart') => void;
  actionLoading?: string | null;
}
```

```tsx
export default function ContainerRow({ container, onViewLogs, onToggleExpand, isExpanded, onAction, actionLoading }: Props) {
  const actionBtn: React.CSSProperties = {
    padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border)',
    background: 'transparent', color: 'var(--text)', cursor: 'pointer', fontSize: 13, lineHeight: 1,
  };
  const actionBtnDisabled: React.CSSProperties = { ...actionBtn, opacity: 0.35, cursor: 'not-allowed' };
```

O bloco final da tabela (célula "Ações", com o botão "Ver Logs") atual é:

```tsx
      <td style={{ padding: '10px 16px' }}>
        <button
          onClick={() => onViewLogs?.(container.id, container.name)}
          style={{
            padding: '4px 12px', borderRadius: 6, border: '1px solid var(--border)',
            background: 'transparent', color: 'var(--text)', cursor: 'pointer', fontSize: 12,
          }}
        >
          Ver Logs
        </button>
      </td>
    </tr>
  );
}
```

Substitua por:

```tsx
      <td style={{ padding: '10px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <button
            onClick={() => onViewLogs?.(container.id, container.name)}
            style={{
              padding: '4px 12px', borderRadius: 6, border: '1px solid var(--border)',
              background: 'transparent', color: 'var(--text)', cursor: 'pointer', fontSize: 12,
            }}
          >
            Ver Logs
          </button>
          {onAction && (
            <>
              <button
                title="Iniciar"
                disabled={container.status === 'running' || actionLoading === `${container.id}:start`}
                onClick={() => onAction(container.id, container.name, 'start')}
                style={container.status === 'running' ? actionBtnDisabled : actionBtn}
              >
                {actionLoading === `${container.id}:start` ? '…' : '▶'}
              </button>
              <button
                title="Reiniciar"
                disabled={container.status !== 'running' || actionLoading === `${container.id}:restart`}
                onClick={() => onAction(container.id, container.name, 'restart')}
                style={container.status !== 'running' ? actionBtnDisabled : actionBtn}
              >
                {actionLoading === `${container.id}:restart` ? '…' : '⟳'}
              </button>
              <button
                title="Parar"
                disabled={container.status !== 'running' || actionLoading === `${container.id}:stop`}
                onClick={() => onAction(container.id, container.name, 'stop')}
                style={container.status !== 'running' ? actionBtnDisabled : actionBtn}
              >
                {actionLoading === `${container.id}:stop` ? '…' : '⏹'}
              </button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}
```

- [ ] **Step 2: Adicionar estado, ações e modal de confirmação em `containers/page.tsx`**

Em `frontend/app/containers/page.tsx`, o topo do arquivo atual é:

```tsx
'use client';
import { useState, useEffect, Fragment } from 'react';
import { useWebSocket, ContainerMetric } from '../../lib/ws';
import ContainerRow from '../../components/ContainerRow';
import LineChart from '../../components/LineChart';
import api from '../../lib/api';

type Filter = 'all' | 'running' | 'stopped';
interface Point { ts: string; value: number | null; }
```

Substitua por:

```tsx
'use client';
import { useState, useEffect, Fragment } from 'react';
import { useWebSocket, ContainerMetric } from '../../lib/ws';
import ContainerRow from '../../components/ContainerRow';
import LineChart from '../../components/LineChart';
import Toast from '../../components/Toast';
import api from '../../lib/api';

type Filter = 'all' | 'running' | 'stopped';
type ContainerAction = 'start' | 'stop' | 'restart';
interface Point { ts: string; value: number | null; }
interface ConfirmState { id: string; name: string; action: ContainerAction; }

const MONITOR_CONTAINERS = ['monitor-backend', 'monitor-frontend', 'monitor-nginx'];
const ACTION_LABEL: Record<ContainerAction, string> = { start: 'iniciar', stop: 'parar', restart: 'reiniciar' };
```

Dentro do componente `ContainersPage`, logo após a declaração de `const [filter, setFilter] = useState<Filter>('all');`, adicione:

```tsx
  const [confirmAction, setConfirmAction] = useState<ConfirmState | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionToast, setActionToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  async function runAction(id: string, action: ContainerAction) {
    setActionLoading(`${id}:${action}`);
    try {
      await api.post(`/containers/${id}/${action}`);
      setActionToast({ msg: `Comando de ${ACTION_LABEL[action]} enviado.`, type: 'success' });
    } catch {
      setActionToast({ msg: `Falha ao ${ACTION_LABEL[action]} o container.`, type: 'error' });
    } finally {
      setActionLoading(null);
    }
  }

  function requestAction(id: string, name: string, action: ContainerAction) {
    if (action === 'start') {
      runAction(id, action);
      return;
    }
    setConfirmAction({ id, name, action });
  }
```

Logo antes do `return (` do componente, não há mudança adicional necessária — os handlers acima já estão disponíveis no escopo do JSX abaixo.

- [ ] **Step 3: Passar as novas props para `ContainerRow` e renderizar o modal de confirmação e o toast**

Ainda em `frontend/app/containers/page.tsx`, o uso atual de `<ContainerRow>` é:

```tsx
                  <ContainerRow
                    container={c}
                    onViewLogs={openLogs}
                    onToggleExpand={() => setExpanded(expanded === c.id ? null : c.id)}
                    isExpanded={expanded === c.id}
                  />
```

Substitua por:

```tsx
                  <ContainerRow
                    container={c}
                    onViewLogs={openLogs}
                    onToggleExpand={() => setExpanded(expanded === c.id ? null : c.id)}
                    isExpanded={expanded === c.id}
                    onAction={requestAction}
                    actionLoading={actionLoading}
                  />
```

O final do arquivo (fechamento do log modal e do componente) atual é:

```tsx
          </div>
        </div>
      )}
    </div>
  );
}
```

Substitua por:

```tsx
          </div>
        </div>
      )}

      {/* Modal de confirmação de ação */}
      {confirmAction && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
            zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
          onClick={() => setConfirmAction(null)}
        >
          <div
            style={{
              background: 'var(--card)', border: '1px solid var(--border)',
              borderRadius: 12, padding: 24, maxWidth: 420,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>
              {MONITOR_CONTAINERS.includes(confirmAction.name) ? 'Atenção: container do próprio monitor' : 'Confirmar ação'}
            </h3>
            <p style={{ color: 'var(--muted)', marginBottom: 20, fontSize: 14 }}>
              {MONITOR_CONTAINERS.includes(confirmAction.name)
                ? `Este é um container do próprio VPS Monitor. ${confirmAction.action === 'stop' ? 'Parar' : 'Reiniciar'} "${confirmAction.name}" pode derrubar o painel de monitoramento temporariamente. Deseja continuar?`
                : `Tem certeza que deseja ${ACTION_LABEL[confirmAction.action]} o container "${confirmAction.name}"?`}
            </p>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setConfirmAction(null)}
                style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
              >
                Cancelar
              </button>
              <button
                onClick={() => { runAction(confirmAction.id, confirmAction.action); setConfirmAction(null); }}
                style={{ padding: '8px 20px', background: 'var(--danger)', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
              >
                Confirmar
              </button>
            </div>
          </div>
        </div>
      )}

      {actionToast && (
        <Toast
          message={actionToast.msg}
          type={actionToast.type}
          onDismiss={() => setActionToast(null)}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 4: Verificar que o projeto compila**

Run: `cd frontend && npm run build`
Expected: build conclui sem erros de tipo.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/ContainerRow.tsx frontend/app/containers/page.tsx
git commit -m "feat: adiciona botoes de iniciar/parar/reiniciar containers"
```

---

### Task 10: Frontend — exibir causa provável no Histórico de Alertas

**Files:**
- Modify: `frontend/app/alertas/page.tsx`

**Interfaces:**
- Consumes: campo `contexto: Record<string, any> | null` em cada `AlertLog` (Task 8).

- [ ] **Step 1: Adicionar `contexto` à interface `AlertLog` e estado de expansão**

Em `frontend/app/alertas/page.tsx`, a interface `AlertLog` atual é:

```tsx
interface AlertLog {
  id: number
  rule_id: number | null
  triggered_at: string
  resolved_at: string | null
  severidade: string
  metrica: string
  valor_no_disparo: number | null
  threshold: number | null
  mensagem: string | null
  vps_name: string | null
}
```

Substitua por:

```tsx
interface AlertLog {
  id: number
  rule_id: number | null
  triggered_at: string
  resolved_at: string | null
  severidade: string
  metrica: string
  valor_no_disparo: number | null
  threshold: number | null
  mensagem: string | null
  vps_name: string | null
  contexto: Record<string, any> | null
}
```

Dentro do componente `AlertasPage`, logo após `const [filtMetrica, setFiltMetrica] = useState('');`, adicione:

```tsx
  const [expandedAlert, setExpandedAlert] = useState<number | null>(null)
```

- [ ] **Step 2: Criar a função de formatação do contexto**

Antes da declaração de `export default function AlertasPage()`, adicione:

```tsx
function renderContexto(ctx: Record<string, any> | null): React.ReactNode {
  if (!ctx) return <span style={{ color: 'var(--muted)' }}>Sem dados de contexto disponíveis para este alerta.</span>

  const linhas: React.ReactNode[] = []

  if (ctx.top_cpu) {
    linhas.push(
      <div key="top_cpu">
        <strong>Top CPU: </strong>
        {ctx.top_cpu.map((c: any) => `${c.nome} (${c.valor}%)`).join(', ')}
      </div>
    )
  }
  if (ctx.top_mem) {
    linhas.push(
      <div key="top_mem">
        <strong>Top RAM: </strong>
        {ctx.top_mem.map((c: any) => `${c.nome} (${c.valor}%)`).join(', ')}
      </div>
    )
  }
  if (ctx.top_rede) {
    linhas.push(
      <div key="top_rede">
        <strong>Top Rede: </strong>
        {ctx.top_rede.map((c: any) => `${c.nome} (${c.valor_mb} MB)`).join(', ')}
      </div>
    )
  }
  if (ctx.top_disco) {
    linhas.push(
      <div key="top_disco">
        <strong>Top Disco (camada gravável): </strong>
        {ctx.top_disco.map((c: any) => `${c.nome} (${c.valor_mb} MB)`).join(', ')}
      </div>
    )
  }
  if ('exit_code' in ctx || 'oom_killed' in ctx) {
    linhas.push(
      <div key="exit">
        <strong>Motivo: </strong>
        {ctx.oom_killed
          ? 'finalizado por falta de memória (OOM Killed)'
          : `código de saída ${ctx.exit_code ?? '—'}`}
        {ctx.erro ? ` — ${ctx.erro}` : ''}
      </div>
    )
  }

  return linhas.length > 0 ? <div style={{ display: 'grid', gap: 4 }}>{linhas}</div> : renderContexto(null)
}
```

- [ ] **Step 3: Tornar as linhas do Histórico expansíveis**

O bloco da tabela do Histórico atual é:

```tsx
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ color: 'var(--muted)', borderBottom: '1px solid var(--border)' }}>
                {['Severidade', 'Métrica', 'Mensagem', 'VPS', 'Disparado em', 'Resolvido em'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontWeight: 600 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {history.map(a => (
                <tr key={a.id} style={{ borderBottom: '1px solid var(--border)', color: 'var(--text)' }}>
                  <td style={{ padding: '8px 10px' }}><AlertBadge severidade={a.severidade} /></td>
                  <td style={{ padding: '8px 10px' }}>{METRICA_LABELS[a.metrica] ?? a.metrica}</td>
                  <td style={{ padding: '8px 10px', maxWidth: 320 }}>{a.mensagem}</td>
                  <td style={{ padding: '8px 10px' }}><VpsBadge name={a.vps_name} /></td>
                  <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>{a.triggered_at ? formatDt(a.triggered_at) : '—'}</td>
                  <td style={{ padding: '8px 10px', whiteSpace: 'nowrap', color: a.resolved_at ? 'var(--success)' : 'var(--warning)' }}>
                    {a.resolved_at ? formatDt(a.resolved_at) : 'Ativo'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
```

Substitua por:

```tsx
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ color: 'var(--muted)', borderBottom: '1px solid var(--border)' }}>
                <th style={{ padding: '8px 10px', width: 24 }} />
                {['Severidade', 'Métrica', 'Mensagem', 'VPS', 'Disparado em', 'Resolvido em'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontWeight: 600 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {history.map(a => (
                <Fragment key={a.id}>
                  <tr
                    style={{ borderBottom: '1px solid var(--border)', color: 'var(--text)', cursor: 'pointer' }}
                    onClick={() => setExpandedAlert(expandedAlert === a.id ? null : a.id)}
                  >
                    <td style={{ padding: '8px 10px', color: 'var(--muted)' }}>{expandedAlert === a.id ? '▼' : '▶'}</td>
                    <td style={{ padding: '8px 10px' }}><AlertBadge severidade={a.severidade} /></td>
                    <td style={{ padding: '8px 10px' }}>{METRICA_LABELS[a.metrica] ?? a.metrica}</td>
                    <td style={{ padding: '8px 10px', maxWidth: 320 }}>{a.mensagem}</td>
                    <td style={{ padding: '8px 10px' }}><VpsBadge name={a.vps_name} /></td>
                    <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>{a.triggered_at ? formatDt(a.triggered_at) : '—'}</td>
                    <td style={{ padding: '8px 10px', whiteSpace: 'nowrap', color: a.resolved_at ? 'var(--success)' : 'var(--warning)' }}>
                      {a.resolved_at ? formatDt(a.resolved_at) : 'Ativo'}
                    </td>
                  </tr>
                  {expandedAlert === a.id && (
                    <tr>
                      <td colSpan={7} style={{ background: 'var(--surface)', padding: 16, borderBottom: '1px solid var(--border)', fontSize: 12 }}>
                        {renderContexto(a.contexto)}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
```

O import de `Fragment` do React precisa estar disponível — adicione ao topo do arquivo, junto aos demais imports:

```tsx
import { useEffect, useState, useCallback, Fragment, type CSSProperties } from 'react'
```

(Isso substitui a linha de import atual `import { useEffect, useState, useCallback, type CSSProperties } from 'react'`.)

- [ ] **Step 4: Verificar que o projeto compila**

Run: `cd frontend && npm run build`
Expected: build conclui sem erros de tipo.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/alertas/page.tsx
git commit -m "feat: historico de alertas exibe causa provavel expansivel"
```

---

### Task 11: Verificação final da suíte completa

**Files:**
- Nenhum arquivo modificado — apenas verificação.

**Interfaces:**
- N/A.

- [ ] **Step 1: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: mesmas 3 falhas pré-existentes de antes desta feature (`test_metrics_api.py::test_sem_autenticacao_401`, `test_scheduler.py::test_collect_and_store_salva_no_banco`, `test_websocket.py::test_websocket_conecta_e_recebe` — falhas de isolamento de módulo/JWT_SECRET entre testes, não relacionadas a este trabalho) e nenhuma falha nova. Se alguma falha nova aparecer, ela precisa ser corrigida antes de prosseguir.

- [ ] **Step 2: Rodar o build do frontend**

Run: `cd frontend && npm run build`
Expected: build conclui sem erros de tipo ou lint.

- [ ] **Step 3: Validar sintaxe final do compose**

Run: `docker compose config --quiet`
Expected: sem erros.

## Verificação manual (pós-implementação, em ambiente com Docker de verdade)

1. Subir o stack (`docker compose up -d --build`) e confirmar que `monitor-backend` sobe sem erro de permissão no socket.
2. Na página Containers, clicar em ⏹ num container que não seja do monitor, confirmar no modal, e verificar que o status muda para "Parado" no próximo ciclo (até 30s). Clicar em ▶ para religar.
3. Clicar em ⏹ em `monitor-backend` e confirmar que o modal mostra o aviso reforçado antes de prosseguir.
4. Provocar um alerta (ex. parar um container não essencial) e, depois de resolvido, abrir Alertas > Histórico, expandir a linha e conferir que aparece "Motivo: código de saída ..." ou "OOM Killed".
5. Provocar um alerta de CPU/RAM alta (ex. rodar `stress` dentro de um container de teste) e conferir que o Histórico mostra "Top CPU"/"Top RAM"/"Top Rede" com o container correto.
6. Esperar ~10 min após o deploy e consultar `container_disk_usage` no banco (`sqlite3 /app/data/monitor.db "select * from container_disk_usage limit 5;"` dentro do container) para confirmar que a coleta de disco está rodando.

## Self-Review

**Cobertura do spec:**
- Feature A (start/stop/restart + log de auditoria + aviso reforçado nos containers do monitor + `:ro` removido) → Tasks 2, 3, 4, 9.
- Feature B (contexto para CPU/RAM/Load/Disco, motivo real para Container Parado, sem contexto para Temperatura, exposição na API, exibição no Histórico) → Tasks 1, 5, 6, 7, 8, 10.
- Retenção de `ContainerDiskUsage` reaproveitando `retention_aggregated_days` → Task 7, Step 3 (`_cleanup`).
- `evaluate()` mantém compatibilidade com chamadas de 2 argumentos → Task 6, Step 4 (`docker_client=None` opcional), validado pelos testes antigos que não foram alterados.

**Placeholders:** nenhum "TBD"/"implementar depois" — todo código está completo em cada step.

**Consistência de tipos:** `ContainerAction`/`'start' | 'stop' | 'restart'` usado de forma consistente entre `ContainerRow.tsx` (Task 9) e `containers/page.tsx` (Task 9). Chaves do JSON de `contexto` (`top_cpu`, `top_mem`, `top_rede`, `top_disco`, `exit_code`, `oom_killed`, `erro`, `finalizado_em`) usadas de forma idêntica entre `alert_engine.py` (Tasks 5 e 6) e `renderContexto()` no frontend (Task 10). `_evaluate_rule` e `_evaluate_container_stopped` recebem `containers`/`docker_client` com os mesmos nomes de parâmetro em toda a cadeia de chamadas (`evaluate` → Task 5/6).
