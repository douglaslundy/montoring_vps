# Gestão de Containers "Lixo" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar exclusão de containers Docker parados pela UI do monitor, com bloqueio de containers do próprio stack, sem remoção de volumes, e confirmação forte (nome digitado) mostrando tempo parado + uso de disco.

**Architecture:** Novo endpoint `DELETE /api/containers/{id}` reaproveita o padrão `_run_action` já usado por start/stop/restart (audit log em `ContainerActionLog`). Um `GET /api/containers/{id}/disk-usage` novo expõe sob demanda a última amostra de `ContainerDiskUsage` (já coletada a cada 10min). O Docker Engine em si recusa remover container rodando (HTTP 409) sem precisarmos duplicar essa checagem. O socket-proxy precisa de `DELETE=1` (hoje só `CONTAINERS=1`/`POST=1`).

**Tech Stack:** FastAPI + SQLAlchemy + pytest (backend, TDD), Next.js/React/TypeScript (frontend, sem suíte de testes — build + verificação manual), Docker socket-proxy (env var).

## Global Constraints

- Nunca remover volumes (`docker rm` simples — sem `v=true`).
- Nunca permitir remoção forçada de container rodando pela UI — o Docker Engine já recusa isso nativamente sem `force=true`; não duplicar essa checagem no backend (evita race condition).
- Containers `monitor-backend`, `monitor-frontend`, `monitor-nginx` são bloqueados de exclusão no **backend** (403), não só escondidos/desabilitados na UI.
- Escopo de containers listados/excluíveis continua sendo TODOS os containers do host (sem filtrar por projeto) — decisão já confirmada com o usuário.
- Deploy pra produção (VPS 144.91.92.70) só acontece na Task 4, e só com confirmação explícita antes de executar — é uma mudança de segurança (abre `DELETE` no socket-proxy) numa VPS compartilhada com ~30 containers de outros projetos.

---

### Task 1: `docker_client.remove_container`

**Files:**
- Modify: `backend/collector/docker_client.py`
- Test: `backend/tests/test_docker_client.py`

**Interfaces:**
- Produces: `async def remove_container(self, container_id: str) -> None` em `DockerClient` — chama `DELETE /containers/{id}` sem params, propaga `httpx.HTTPStatusError` em caso de erro (inclusive 409 se o container estiver rodando). Consumido pela Task 2.

- [ ] **Step 1: Escrever os testes (devem falhar)**

Adicionar ao final de `backend/tests/test_docker_client.py` (mesmo arquivo, mesma seção de "Container control" já existente, logo após `test_list_containers_with_size`):

```python
@pytest.mark.asyncio
async def test_remove_container_chama_endpoint_correto():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_response.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.delete = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        await client.remove_container("abc123")

    mock_http.delete.assert_called_once_with("/containers/abc123")


@pytest.mark.asyncio
async def test_remove_container_propaga_erro_409_quando_rodando():
    import httpx
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 409
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("conflict", request=MagicMock(), response=mock_response)
    )
    mock_http = AsyncMock()
    mock_http.delete = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        with pytest.raises(httpx.HTTPStatusError):
            await client.remove_container("abc123")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_docker_client.py -v -k remove_container`
Expected: FAIL — `AttributeError: 'DockerClient' object has no attribute 'remove_container'`

- [ ] **Step 3: Implementar `remove_container`**

Em `backend/collector/docker_client.py`, logo depois de `restart_container` (depois da linha `await self._post_action(container_id, "restart", {"t": timeout})`):

```python
    async def remove_container(self, container_id: str) -> None:
        async with self._client() as c:
            r = await c.delete(f"/containers/{container_id}")
            r.raise_for_status()
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_docker_client.py -v -k remove_container`
Expected: PASS (2 testes)

- [ ] **Step 5: Commit**

```bash
git add backend/collector/docker_client.py backend/tests/test_docker_client.py
git commit -m "feat: adiciona remove_container ao DockerClient"
```

---

### Task 2: Endpoints `DELETE /containers/{id}` e `GET /containers/{id}/disk-usage`

**Files:**
- Modify: `backend/api/containers.py`
- Modify: `docker-compose.yml` (socket-proxy `DELETE=1`)
- Test: `backend/tests/test_containers_api.py`

**Interfaces:**
- Consumes: `docker_client.remove_container(id)` da Task 1.
- Produces: `DELETE /api/containers/{id}` (200 com `{"ok": true}` em sucesso; 403 se `container_name` estiver em `MONITOR_OWN_CONTAINERS`; 404/502 em erro do Docker, via `_run_action` já existente). `GET /api/containers/{id}/disk-usage` (200 com `{"size_rw_mb", "size_rootfs_mb", "collected_at"}`, valores `null` se não houver amostra). Consumidos pela Task 3.

- [ ] **Step 1: Escrever os testes (devem falhar)**

Adicionar ao final de `backend/tests/test_containers_api.py`:

```python
def test_remove_container_sucesso(auth_client):
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.remove_container = AsyncMock(return_value=None)
        r = auth_client.delete("/api/containers/abc123")
    assert r.status_code == 200
    mock_dc.remove_container.assert_awaited_once_with("abc123")


def test_remove_container_registra_log_de_sucesso(auth_client, test_db):
    from sqlalchemy.orm import Session
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.remove_container = AsyncMock(return_value=None)
        auth_client.delete("/api/containers/abc123")

    with Session(test_db.engine) as session:
        log = session.query(test_db.ContainerActionLog).first()
    assert log is not None
    assert log.acao == "remove"
    assert log.container_name == "web"
    assert log.sucesso == 1


def test_remove_container_bloqueia_container_do_proprio_monitor(auth_client):
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "xyz789", "name": "monitor-backend"}]}):
        r = auth_client.delete("/api/containers/xyz789")
    assert r.status_code == 403
    mock_dc.remove_container.assert_not_called()


def test_remove_container_erro_409_registra_log_de_falha(auth_client, test_db):
    import httpx
    from sqlalchemy.orm import Session
    mock_response = MagicMock()
    mock_response.status_code = 409
    with patch("api.containers.docker_client") as mock_dc, \
         patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        mock_dc.remove_container = AsyncMock(
            side_effect=httpx.HTTPStatusError("conflict", request=MagicMock(), response=mock_response)
        )
        r = auth_client.delete("/api/containers/abc123")

    assert r.status_code == 502
    with Session(test_db.engine) as session:
        log = session.query(test_db.ContainerActionLog).first()
    assert log.sucesso == 0
    assert log.acao == "remove"


def test_remove_container_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.delete("/api/containers/abc123").status_code == 401


def test_disk_usage_sem_amostra(auth_client):
    with patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        r = auth_client.get("/api/containers/abc123/disk-usage")
    assert r.status_code == 200
    assert r.json() == {"size_rw_mb": None, "size_rootfs_mb": None, "collected_at": None}


def test_disk_usage_com_amostra(auth_client, test_db):
    from sqlalchemy.orm import Session
    from datetime import datetime
    with Session(test_db.engine) as session:
        session.add(test_db.ContainerDiskUsage(
            collected_at=datetime(2026, 7, 16, 10, 0, 0),
            container_id="abc123", container_name="web",
            size_rw_mb=12.5, size_rootfs_mb=340.2,
        ))
        session.commit()

    with patch("collector.scheduler._last_metrics", {"containers": [{"id": "abc123", "name": "web"}]}):
        r = auth_client.get("/api/containers/abc123/disk-usage")

    assert r.status_code == 200
    body = r.json()
    assert body["size_rw_mb"] == 12.5
    assert body["size_rootfs_mb"] == 340.2
    assert body["collected_at"] == "2026-07-16T10:00:00"


def test_disk_usage_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/containers/abc123/disk-usage").status_code == 401
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_containers_api.py -v -k "remove_container or disk_usage"`
Expected: FAIL — `404 Not Found` nas rotas novas, ou `AttributeError` (import de `ContainerDiskUsage` ainda não existe em `api.containers`).

- [ ] **Step 3: Implementar os endpoints**

Em `backend/api/containers.py`, trocar a linha de import:

```python
from models.database import ContainerActionLog
```

por:

```python
from models.database import ContainerActionLog, ContainerDiskUsage
```

Adicionar logo após `containers_router = APIRouter()`:

```python
MONITOR_OWN_CONTAINERS = {"monitor-backend", "monitor-frontend", "monitor-nginx"}
```

Adicionar ao final do arquivo (depois de `restart_container`):

```python
@containers_router.delete("/containers/{container_id}")
async def remove_container(container_id: str, token_data: dict = Depends(get_token_data)):
    container_name = _container_name(container_id)
    if container_name in MONITOR_OWN_CONTAINERS:
        raise HTTPException(status_code=403, detail="Não é possível excluir um container do próprio VPS Monitor.")
    return await _run_action(container_id, "remove", docker_client.remove_container, token_data)


@containers_router.get("/containers/{container_id}/disk-usage")
def container_disk_usage(container_id: str):
    container_name = _container_name(container_id)
    with Session(db_module.engine) as session:
        row = (
            session.query(ContainerDiskUsage)
            .filter(ContainerDiskUsage.container_name == container_name)
            .order_by(ContainerDiskUsage.collected_at.desc())
            .first()
        )
    if not row:
        return {"size_rw_mb": None, "size_rootfs_mb": None, "collected_at": None}
    return {
        "size_rw_mb": row.size_rw_mb,
        "size_rootfs_mb": row.size_rootfs_mb,
        "collected_at": row.collected_at.isoformat(),
    }
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_containers_api.py -v -k "remove_container or disk_usage"`
Expected: PASS (8 testes)

- [ ] **Step 5: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: `166 passed` (158 já existentes + 8 novos), sem regressões.

- [ ] **Step 6: Habilitar `DELETE=1` no socket-proxy**

Em `docker-compose.yml`, no serviço `docker-socket-proxy`, trocar:

```yaml
    environment:
      - CONTAINERS=1
      - POST=1
```

por:

```yaml
    environment:
      - CONTAINERS=1
      - POST=1
      - DELETE=1
```

- [ ] **Step 7: Commit**

```bash
git add backend/api/containers.py backend/tests/test_containers_api.py docker-compose.yml
git commit -m "feat: adiciona exclusao de containers parados via API"
```

---

### Task 3: Frontend — botão de exclusão + modal de confirmação

**Files:**
- Modify: `frontend/components/ContainerRow.tsx`
- Modify: `frontend/app/containers/page.tsx`

**Interfaces:**
- Consumes: `DELETE /api/containers/{id}` e `GET /api/containers/{id}/disk-usage` da Task 2.

- [ ] **Step 1: Adicionar o botão "Excluir" em `ContainerRow.tsx`**

Trocar a linha da prop `onAction`:

```tsx
  onAction?: (id: string, name: string, action: 'start' | 'stop' | 'restart') => void;
```

por:

```tsx
  onAction?: (id: string, name: string, action: 'start' | 'stop' | 'restart' | 'remove') => void;
```

Adicionar, logo depois do botão "Parar" (depois de `{actionLoading === \`${container.id}:stop\` ? '…' : '⏹'}` e antes do `</>`):

```tsx
              <button
                title="Excluir"
                disabled={container.status === 'running' || actionLoading === `${container.id}:remove`}
                onClick={() => onAction(container.id, container.name, 'remove')}
                style={container.status === 'running' ? actionBtnDisabled : actionBtn}
              >
                {actionLoading === `${container.id}:remove` ? '…' : '🗑'}
              </button>
```

- [ ] **Step 2: Estender tipos e estado em `page.tsx`**

Trocar:

```tsx
type ContainerAction = 'start' | 'stop' | 'restart';
interface Point { ts: string; value: number | null; }
interface ConfirmState { id: string; name: string; action: ContainerAction; }

const MONITOR_CONTAINERS = ['monitor-backend', 'monitor-frontend', 'monitor-nginx'];
const ACTION_LABEL: Record<ContainerAction, string> = { start: 'iniciar', stop: 'parar', restart: 'reiniciar' };
```

por:

```tsx
type ContainerAction = 'start' | 'stop' | 'restart' | 'remove';
interface Point { ts: string; value: number | null; }
interface ConfirmState {
  id: string;
  name: string;
  action: ContainerAction;
  statusText?: string;
  diskUsageMb?: number | null;
}

const MONITOR_CONTAINERS = ['monitor-backend', 'monitor-frontend', 'monitor-nginx'];
const ACTION_LABEL: Record<ContainerAction, string> = { start: 'iniciar', stop: 'parar', restart: 'reiniciar', remove: 'excluir' };
```

- [ ] **Step 3: Adicionar estado de texto de confirmação**

Trocar:

```tsx
  const [confirmAction, setConfirmAction] = useState<ConfirmState | null>(null);
```

por:

```tsx
  const [confirmAction, setConfirmAction] = useState<ConfirmState | null>(null);
  const [confirmText, setConfirmText] = useState('');
```

- [ ] **Step 4: Buscar disco/status ao pedir exclusão**

Trocar:

```tsx
  function requestAction(id: string, name: string, action: ContainerAction) {
    if (action === 'start') {
      runAction(id, action);
      return;
    }
    setConfirmAction({ id, name, action });
  }
```

por:

```tsx
  async function requestAction(id: string, name: string, action: ContainerAction) {
    if (action === 'start') {
      runAction(id, action);
      return;
    }
    if (action === 'remove') {
      const container = allContainers.find((c) => c.id === id);
      let diskUsageMb: number | null = null;
      try {
        const r = await api.get(`/containers/${id}/disk-usage`);
        diskUsageMb = r.data.size_rw_mb;
      } catch { /* segue sem dado de disco */ }
      setConfirmText('');
      setConfirmAction({ id, name, action, statusText: container?.status_text, diskUsageMb });
      return;
    }
    setConfirmAction({ id, name, action });
  }
```

- [ ] **Step 5: Estender o modal de confirmação**

Trocar o corpo do modal (dentro de `{confirmAction && ( ... )}`, dentro da `<div>` interna que hoje tem `<h3>` + `<p>` + botões):

```tsx
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
```

por:

```tsx
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>
              {confirmAction.action === 'remove'
                ? 'Excluir container'
                : MONITOR_CONTAINERS.includes(confirmAction.name) ? 'Atenção: container do próprio monitor' : 'Confirmar ação'}
            </h3>
            {confirmAction.action === 'remove' ? (
              <>
                <p style={{ color: 'var(--muted)', marginBottom: 8, fontSize: 14 }}>
                  Excluir "{confirmAction.name}" remove o container permanentemente (sem remover volumes associados). Essa ação não pode ser desfeita.
                </p>
                <p style={{ color: 'var(--muted)', marginBottom: 16, fontSize: 13 }}>
                  {confirmAction.statusText || 'Status indisponível'}
                  {confirmAction.diskUsageMb != null && ` · ${confirmAction.diskUsageMb.toFixed(1)} MB em disco`}
                </p>
                <label style={{ color: 'var(--muted)', fontSize: 12, display: 'block', marginBottom: 6 }}>
                  Digite &quot;{confirmAction.name}&quot; para confirmar:
                </label>
                <input
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  style={{
                    width: '100%', boxSizing: 'border-box', marginBottom: 20,
                    background: 'var(--surface)', border: '1px solid var(--border)',
                    borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 14,
                  }}
                />
              </>
            ) : (
              <p style={{ color: 'var(--muted)', marginBottom: 20, fontSize: 14 }}>
                {MONITOR_CONTAINERS.includes(confirmAction.name)
                  ? `Este é um container do próprio VPS Monitor. ${confirmAction.action === 'stop' ? 'Parar' : 'Reiniciar'} "${confirmAction.name}" pode derrubar o painel de monitoramento temporariamente. Deseja continuar?`
                  : `Tem certeza que deseja ${ACTION_LABEL[confirmAction.action]} o container "${confirmAction.name}"?`}
              </p>
            )}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button
                onClick={() => { setConfirmAction(null); setConfirmText(''); }}
                style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
              >
                Cancelar
              </button>
              <button
                disabled={confirmAction.action === 'remove' && confirmText !== confirmAction.name}
                onClick={() => { runAction(confirmAction.id, confirmAction.action); setConfirmAction(null); setConfirmText(''); }}
                style={{
                  padding: '8px 20px', color: '#fff', border: 'none', borderRadius: 6, fontWeight: 700,
                  background: (confirmAction.action === 'remove' && confirmText !== confirmAction.name) ? 'var(--muted)' : 'var(--danger)',
                  cursor: (confirmAction.action === 'remove' && confirmText !== confirmAction.name) ? 'not-allowed' : 'pointer',
                }}
              >
                Confirmar
              </button>
            </div>
```

Também trocar o `onClick` do overlay do modal (clicar fora fecha) de `onClick={() => setConfirmAction(null)}` para `onClick={() => { setConfirmAction(null); setConfirmText(''); }}`, mantendo consistência com o botão "Cancelar".

- [ ] **Step 6: Build**

Run: `cd frontend && npm run build`
Expected: build limpo, sem erros de tipo.

- [ ] **Step 7: Teste manual no navegador**

Com o backend local rodando (mesmo esquema de banco/rewrite temporário já usado na tarefa do modal de regras, se necessário):

1. Abrir `/containers`. Confirmar que o botão 🗑 aparece desabilitado (cinza) em containers rodando.
2. Parar um container de teste. Confirmar que o botão 🗑 fica habilitado.
3. Clicar em 🗑 → modal abre mostrando status (`Exited ... ago`) e tamanho em disco (se houver amostra).
4. Digitar um nome errado → botão "Confirmar" continua desabilitado.
5. Digitar o nome exato do container → botão habilita, clicar → container some da lista, toast de sucesso.
6. Tentar excluir `monitor-backend`/`monitor-frontend`/`monitor-nginx` (se acessível localmente) → botão 🗑 deve estar sempre desabilitado (container rodando) e, mesmo se forçado via API diretamente, deve retornar 403.

- [ ] **Step 8: Commit**

```bash
git add frontend/components/ContainerRow.tsx frontend/app/containers/page.tsx
git commit -m "feat: adiciona exclusao de containers parados na UI"
```

---

### Task 4: Deploy para produção

**Files:** nenhum (ação operacional, sem mudança de código)

**Interfaces:**
- Consumes: commits das Tasks 1-3 já na `main` local.

**Atenção:** esta task só deve ser executada após confirmação explícita do usuário — abre `DELETE` no socket-proxy da VPS de produção (144.91.92.70), compartilhada com ~30 containers de outros projetos.

- [ ] **Step 1: Push para o remoto**

```bash
git push origin main
```

- [ ] **Step 2: Deploy na VPS (fluxo já documentado — git pull + deploy.sh)**

```bash
ssh root@144.91.92.70 "cd /opt/vps-monitor && git pull --ff-only && bash monitor/deploy.sh"
```

- [ ] **Step 3: Confirmar que os containers do monitor subiram saudáveis**

```bash
ssh root@144.91.92.70 "docker ps --filter name=vps-monitor --format '{{.Names}}\t{{.Status}}'"
```

Expected: `vps-monitor-backend`, `vps-monitor-frontend`, `vps-monitor-nginx`, `vps-monitor-socket-proxy` todos `Up`.

- [ ] **Step 4: Smoke test do endpoint novo em produção**

```bash
ssh root@144.91.92.70 "docker ps -a --filter name=vps-monitor --format '{{.Names}}: {{.Status}}'"
```

Escolher (se existir) um container de teste já parado e sem relação com nenhum projeto ativo antes de testar exclusão real em produção — não usar um container aleatório de outro cliente para esse teste.
