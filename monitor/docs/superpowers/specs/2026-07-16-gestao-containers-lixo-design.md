# Gestão de Containers "Lixo" (exclusão via UI)

## Contexto

O monitor já lista todos os containers Docker da VPS (compartilhada com ~30 containers de múltiplos projetos/clientes — ver memória `project_vps_monitor_deploy`), com ações de start/stop/restart via `docker-socket-proxy` (`CONTAINERS=1`/`POST=1`, sem `DELETE=1`). O usuário quer um jeito de identificar containers parados que já não servem pra nada e excluí-los direto pela UI, pra liberar espaço em disco, sem precisar entrar via SSH.

Já existe bastante infraestrutura reaproveitável:
- `ContainerActionLog` (audit log de start/stop/restart, campo `acao` já é string livre).
- `ContainerDiskUsage` (histórico de uso de disco por container, coletado a cada 10min via `collect_disk_usage`).
- O campo `status_text` do Docker (ex: `"Exited (0) 3 weeks ago"`) já indica há quanto tempo o container está parado — não precisa de tracking novo pra isso.
- Modal de confirmação de ação já existe em `app/containers/page.tsx` (`confirmAction`), incluindo aviso especial pros containers do próprio monitor.

## Objetivo

Adicionar exclusão de containers parados pela UI do monitor, com confirmação forte (nome digitado) e informação suficiente (tempo parado + tamanho em disco) pra o usuário ter certeza que é seguro apagar — sem risco de derrubar containers rodando (inclusive de outros projetos) ou os do próprio monitor.

## Fora de escopo

- Restringir a listagem/exclusão só aos containers do stack `vps-monitor` — mantém escopo atual (qualquer container do host).
- Remoção de volumes (`docker rm -v`) — só remove o container, nunca dados associados.
- "Forçar" remoção de container rodando pela UI — sempre exige parar antes.
- Qualquer mudança em `/opt/traefik` ou infraestrutura fora desta VPS/repo.

## Design

### Backend

**`collector/docker_client.py`** — novo método (`_post_action` hoje só faz POST; a remoção é um `DELETE /containers/{id}`, então precisa de um método HTTP próprio, não reaproveita `_post_action`):

```python
async def remove_container(self, container_id: str) -> None:
    async with self._client() as c:
        r = await c.delete(f"/containers/{container_id}")
        r.raise_for_status()
```

Sem `force=true` nem `v=true` nos params — se o container estiver rodando, o Docker recusa com HTTP 409 nativamente (sem precisar de checagem de estado duplicada no nosso código, evitando race condition entre "checar status" e "excluir").

**`api/containers.py`** — novo endpoint:

```python
MONITOR_OWN_CONTAINERS = {"monitor-backend", "monitor-frontend", "monitor-nginx"}

@containers_router.delete("/containers/{container_id}")
async def remove_container(container_id: str, token_data: dict = Depends(get_token_data)):
    container_name = _container_name(container_id)
    if container_name in MONITOR_OWN_CONTAINERS:
        raise HTTPException(status_code=403, detail="Não é possível excluir um container do próprio VPS Monitor.")
    return await _run_action(container_id, "remove", docker_client.remove_container, token_data)
```

Reaproveita `_run_action` (já grava sucesso/erro em `ContainerActionLog` com `acao="remove"` — nenhuma migração de schema necessária, `acao` já é `String` livre). Um 409 do Docker (container rodando) já flui pelo tratamento de erro existente em `_run_action` (retorna 502 hoje pra qualquer erro não-404 — aceitável, mensagem de erro do Docker já vem no `detail`).

**Novo endpoint de disk usage sob demanda:**

```python
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

Buscado sob demanda (só quando o modal de exclusão abre), igual ao padrão já usado por `GET /containers/{id}/logs` — não entra no payload do WebSocket, mantendo o broadcast de 30s leve.

### Infraestrutura

`docker-compose.yml`, serviço `docker-socket-proxy`:

```yaml
    environment:
      - CONTAINERS=1
      - POST=1
      - DELETE=1
```

Aplicado via fluxo de deploy já existente (`git pull` + `deploy.sh`, documentado na memória `project_vps_monitor_deploy`).

### Frontend

**`lib/ws.ts`** ou onde `ContainerAction`/tipos residem — nenhuma mudança de tipo de dados necessária (o campo `status_text` já existe em `ContainerMetric`).

**`app/containers/page.tsx`:**
- `ContainerAction` type: adicionar `'remove'`.
- `ACTION_LABEL`: adicionar `remove: 'excluir'`.
- `ConfirmState`: adicionar campos opcionais `statusText?: string`, `diskUsageMb?: number | null` (preenchidos ao abrir o modal, buscando `GET /containers/{id}/disk-usage`).
- `requestAction`: para `action === 'remove'`, buscar disk-usage antes de abrir o modal (mesmo padrão de `openLogs`).
- Modal de confirmação: branch novo pra `action === 'remove'` mostrando tempo parado + disco, com um `<input>` de confirmação (habilita o botão só quando o texto digitado bate exatamente com `confirmAction.name`).

**`components/ContainerRow.tsx`:**
- `onAction` prop type: adicionar `'remove'`.
- Novo botão "Excluir" (🗑), com `disabled={container.status === 'running' || ...}` (inverso do botão "Iniciar" já existente) — só habilitado pra containers parados.
- Containers em `MONITOR_CONTAINERS` (lista já existente no frontend) continuam podendo ser vistos, mas o botão de excluir fica desabilitado pra eles também (checagem de UI complementar ao 403 do backend).

### Testes

Backend (TDD, seguindo os 158 testes já existentes):
- `test_docker_client.py`: `remove_container` chama `DELETE /containers/{id}` sem `force`/`v` nos params.
- `test_containers_api.py`:
  - `DELETE /containers/{id}` com container parado → sucesso, log de auditoria gravado com `acao="remove"`, `sucesso=1`.
  - `DELETE /containers/{id}` com container rodando (mock retorna 409) → resposta de erro, log de auditoria gravado com `sucesso=0`.
  - `DELETE /containers/{id}` pra `monitor-backend` → 403, sem chamar o Docker.
  - `GET /containers/{id}/disk-usage` sem amostra no banco → `size_rw_mb: null`.
  - `GET /containers/{id}/disk-usage` com amostra → retorna os valores mais recentes.
  - Sem autenticação → 401 (padrão já seguido em outros endpoints).

Frontend: `npm run build` limpo + verificação manual no navegador (abrir "Excluir" num container parado, ver modal com tempo parado + disco, confirmar que o botão só habilita com o nome certo digitado, excluir de fato um container de teste e confirmar que some da lista; confirmar que "Excluir" fica desabilitado num container rodando e nos containers do próprio monitor).

## Arquivos afetados

- **Modificado:** `backend/collector/docker_client.py` (`remove_container`)
- **Modificado:** `backend/api/containers.py` (endpoint DELETE + endpoint disk-usage + `MONITOR_OWN_CONTAINERS`)
- **Modificado:** `backend/tests/test_docker_client.py`, `backend/tests/test_containers_api.py`
- **Modificado:** `docker-compose.yml` (socket-proxy `DELETE=1`)
- **Modificado:** `frontend/app/containers/page.tsx`
- **Modificado:** `frontend/components/ContainerRow.tsx`
