# Listagem de Projetos da VPS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nova página `/projetos` que agrupa os containers da VPS por projeto docker-compose (via label `com.docker.compose.project`) e mostra domínio, quantidade de containers, CPU % e RAM consumida por projeto.

**Architecture:** Backend agrega dados já cacheados em `_last_metrics` (sem chamada nova ao Docker) num novo endpoint `GET /api/projects`; domínio é resolvido via labels de Traefik nos containers e, como fallback, lendo os arquivos dinâmicos do Traefik (novo mount read-only). Frontend é uma página nova, só leitura, com polling de 30s (mesmo padrão de `app/alertas/page.tsx`).

**Tech Stack:** Python/FastAPI/pytest (backend), Next.js/React/TypeScript (frontend), Docker Compose.

## Global Constraints

- `mem_limit_mb` nunca é somado entre containers de um projeto (ver spec: containers sem limite explícito reportam a RAM total do host como "limite", o que tornaria a soma sem sentido). Use `mem_percent_do_host = mem_usage_mb_total / host_total_mb * 100`.
- Domínio resolvido primeiro via label `traefik.http.routers.*.rule` de qualquer container do grupo; se nenhum, fallback lendo `{TRAEFIK_DYNAMIC_DIR}/{projeto}.yml` (casamento só por nome de arquivo == nome do projeto — nada mais genérico nesta spec).
- `TRAEFIK_DYNAMIC_DIR` configurável via variável de ambiente (default `/opt/traefik/dynamic`), mesmo padrão de `FAIL2BAN_JAIL_DIR`/`FAIL2BAN_FILTER_DIR` em `api/fail2ban.py`, para permitir apontar a um diretório temporário nos testes.
- Endpoint novo é read-only — nenhuma ação (start/stop/delete) nesta página.
- Sem persistência nova em banco (sem tabela/modelo novo).
- Registro do router em `main.py` segue o padrão de `containers_router`/`metrics_router` (`app.include_router(projects_router, prefix="/api", **_protected)`), não o padrão auto-contido de `fail2ban_router`.

---

### Task 1: `collect_all()` expõe as labels do container

**Files:**
- Modify: `backend/collector/docker_client.py:193-209` (dentro do loop de `collect_all()`)
- Test: `backend/tests/test_docker_client.py`

**Interfaces:**
- Produces: cada dict retornado por `DockerClient.collect_all()` passa a ter a chave `"labels": dict` (labels brutas do container, `{}` se ausente). Tasks seguintes (Task 2) leem essa chave via `c.get("labels")`.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao final de `backend/tests/test_docker_client.py` (mesmo estilo do `test_collect_all` já existente, por volta da linha 170):

```python
@pytest.mark.asyncio
async def test_collect_all_inclui_labels():
    from collector.docker_client import DockerClient

    containers_com_label = [{
        **MOCK_CONTAINERS[0],
        "Labels": {"com.docker.compose.project": "mecanicapro"},
    }]

    async def mock_list():
        return containers_com_label

    async def mock_container_stats(cid):
        return MOCK_PROCESSED_STATS

    client = DockerClient()
    with patch.object(client, "list_containers", mock_list), \
         patch.object(client, "container_stats", mock_container_stats):
        result = await client.collect_all()

    assert result[0]["labels"] == {"com.docker.compose.project": "mecanicapro"}


@pytest.mark.asyncio
async def test_collect_all_labels_ausentes_vira_dict_vazio():
    from collector.docker_client import DockerClient

    containers_sem_label = [dict(MOCK_CONTAINERS[0])]
    containers_sem_label[0].pop("Labels", None)

    async def mock_list():
        return containers_sem_label

    async def mock_container_stats(cid):
        return MOCK_PROCESSED_STATS

    client = DockerClient()
    with patch.object(client, "list_containers", mock_list), \
         patch.object(client, "container_stats", mock_container_stats):
        result = await client.collect_all()

    assert result[0]["labels"] == {}
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `pytest tests/test_docker_client.py -k labels -v`
Expected: FAIL com `KeyError: 'labels'`

- [ ] **Step 3: Implementar**

Em `backend/collector/docker_client.py`, dentro do loop `for container, stats in zip(containers, stats_list):` de `collect_all()` (linha ~193), adicionar a chave `"labels"` ao dict retornado:

```python
            result.append({
                "id": container["Id"][:12],
                "id_full": container["Id"],
                "name": name,
                "image": container.get("Image", ""),
                "status": container.get("State", "unknown"),
                "status_text": container.get("Status", ""),
                "cpu_percent": cpu_pct,
                "mem_usage_mb": mem_usage,
                "mem_limit_mb": mem_limit,
                "mem_percent": mem_pct,
                "net_rx_mb": net_rx,
                "net_tx_mb": net_tx,
                "block_read_mb": block_read,
                "block_write_mb": block_write,
                "restart_count": container.get("HostConfig", {}).get("RestartCount", 0),
                "labels": container.get("Labels") or {},
            })
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_docker_client.py -v`
Expected: PASS (todos, incluindo os 2 novos)

- [ ] **Step 5: Commit**

```bash
git add backend/collector/docker_client.py backend/tests/test_docker_client.py
git -c user.name="Douglas Lundy" -c user.email="douglaslundy@gmail.com" commit -m "feat: expõe labels de container em collect_all"
```

---

### Task 2: Endpoint `GET /api/projects` com resolução de domínio

**Files:**
- Create: `backend/api/projects.py`
- Modify: `backend/main.py:16-38` (registro do router)
- Test: `backend/tests/test_projects_api.py`

**Interfaces:**
- Consumes: `collector.scheduler.get_last_metrics() -> dict` (já existe, retorna `{"containers": [...], "ram": {"total_mb": float, ...}, ...}`; cada container tem `"labels": dict`, `"cpu_percent": float`, `"mem_usage_mb": float`, `"name": str`, `"status": str`, produzido pela Task 1).
- Produces: `router` (APIRouter, sem prefixo, sem dependency embutida) exportado de `api/projects.py`, registrado no próprio `main.py` nesta mesma task (Step 3b) com a rota final `GET /api/projects`.
- Produces: funções internas `_dominio_por_labels(containers: list[dict]) -> str | None`, `_dominio_por_arquivo_dinamico(projeto: str) -> str | None`, `_resolver_dominio(projeto: str, containers: list[dict]) -> str | None`, e a constante `TRAEFIK_DYNAMIC_DIR` (lida de `os.environ`) — usadas apenas dentro deste arquivo, mas testadas diretamente pelos testes desta task.

- [ ] **Step 1: Escrever os testes que falham**

Criar `backend/tests/test_projects_api.py`:

```python
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
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `pytest tests/test_projects_api.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'api.projects'` ou `ImportError`) — os testes que usam `TestClient(main.app)` também falhariam com 404 mesmo depois de criar o arquivo, até o Step 3b (registro em `main.py`) ser feito, já que a fixture `auth_client` carrega o `main.app` real, não um app isolado.

- [ ] **Step 3a: Implementar `api/projects.py`**

Criar `backend/api/projects.py`:

```python
import os
import re
from fastapi import APIRouter
from collector.scheduler import get_last_metrics

router = APIRouter()

TRAEFIK_DYNAMIC_DIR = os.environ.get("TRAEFIK_DYNAMIC_DIR", "/opt/traefik/dynamic")
_TRAEFIK_RULE_LABEL_RE = re.compile(r"^traefik\.http\.routers\.[^.]+\.rule$")
_HOST_RE = re.compile(r"Host(?:Regexp)?\(`([^`]+)`\)")


def _dominio_por_labels(containers: list[dict]) -> str | None:
    for c in containers:
        labels = c.get("labels") or {}
        for key, rule in labels.items():
            if not _TRAEFIK_RULE_LABEL_RE.match(key):
                continue
            hosts = _HOST_RE.findall(rule)
            if hosts:
                return hosts[0]
    return None


def _dominio_por_arquivo_dinamico(projeto: str) -> str | None:
    path = os.path.join(TRAEFIK_DYNAMIC_DIR, f"{projeto}.yml")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            conteudo = f.read()
    except OSError:
        return None
    hosts = _HOST_RE.findall(conteudo)
    return hosts[0] if hosts else None


def _resolver_dominio(projeto: str, containers: list[dict]) -> str | None:
    return _dominio_por_labels(containers) or _dominio_por_arquivo_dinamico(projeto)


@router.get("/projects")
def list_projects():
    metrics = get_last_metrics()
    containers = metrics.get("containers", [])
    host_total_mb = (metrics.get("ram") or {}).get("total_mb", 0.0)

    grupos: dict[str, list[dict]] = {}
    for c in containers:
        projeto = (c.get("labels") or {}).get("com.docker.compose.project", "(sem projeto)")
        grupos.setdefault(projeto, []).append(c)

    projetos = []
    for nome, membros in grupos.items():
        mem_usage_mb = round(sum(m.get("mem_usage_mb", 0.0) for m in membros), 2)
        projetos.append({
            "nome": nome,
            "dominio": _resolver_dominio(nome, membros),
            "container_count": len(membros),
            "cpu_percent": round(sum(m.get("cpu_percent", 0.0) for m in membros), 2),
            "mem_usage_mb": mem_usage_mb,
            "mem_percent_do_host": (
                round(mem_usage_mb / host_total_mb * 100, 2) if host_total_mb else 0.0
            ),
            "containers": [
                {"name": m.get("name"), "status": m.get("status")} for m in membros
            ],
        })
    projetos.sort(key=lambda p: p["nome"])
    return {"projects": projetos}
```

Nota: `TRAEFIK_DYNAMIC_DIR` é lido no import do módulo (mesmo padrão de `FAIL2BAN_JAIL_DIR` em `api/fail2ban.py`) — por isso os testes que mudam a env var fazem `importlib.reload(projects_mod)` (e `importlib.reload(main)` quando o teste passa pelo endpoint via `TestClient`) antes de usar o novo valor.

- [ ] **Step 3b: Registrar o router em `main.py`**

Os testes de `test_projects_api.py` que usam `TestClient(main.app)` só vão passar depois deste passo — o router precisa estar registrado no app real (não só existir como módulo) pra rota responder e pra autenticação (`_protected`) se aplicar.

Em `backend/main.py`, adicionar o import junto aos outros routers (linha 17, após `fail2ban_router`):

```python
from api.fail2ban import router as fail2ban_router
from api.projects import router as projects_router
```

E o registro junto aos outros protegidos por `_protected` (linha 38, após `containers_router`):

```python
app.include_router(containers_router, prefix="/api", **_protected)
app.include_router(projects_router, prefix="/api", **_protected)
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_projects_api.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Rodar a suíte completa do backend**

Run: `pytest -v`
Expected: PASS (todos os testes do backend)

- [ ] **Step 6: Commit**

```bash
git add backend/api/projects.py backend/tests/test_projects_api.py backend/main.py
git -c user.name="Douglas Lundy" -c user.email="douglaslundy@gmail.com" commit -m "feat: endpoint GET /api/projects agrupando containers por projeto docker-compose"
```

---

### Task 3: Montar o diretório dinâmico do Traefik no compose

**Files:**
- Modify: `docker-compose.yml` (serviço `monitor-backend`, bloco `volumes`)

**Interfaces:**
- Consumes: nada de código — só disponibiliza em produção o diretório que `api.projects._dominio_por_arquivo_dinamico` (Task 2) já sabe ler via `TRAEFIK_DYNAMIC_DIR`.

- [ ] **Step 1: Adicionar o volume read-only no `docker-compose.yml`**

No serviço `monitor-backend`, adicionar ao bloco `volumes:` (mesmo bloco onde já estão os mounts do fail2ban):

```yaml
      - /opt/traefik/dynamic:/opt/traefik/dynamic:ro
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git -c user.name="Douglas Lundy" -c user.email="douglaslundy@gmail.com" commit -m "feat: monta config dinâmica do Traefik (read-only) no monitor-backend"
```

---

### Task 4: Página `/projetos` no frontend

**Files:**
- Create: `frontend/app/projetos/page.tsx`
- Modify: `frontend/app/layout.tsx` (array `NAV`)

**Interfaces:**
- Consumes: `GET /api/projects` → `{"projects": [{"nome": string, "dominio": string | null, "container_count": number, "cpu_percent": number, "mem_usage_mb": number, "mem_percent_do_host": number, "containers": [{"name": string, "status": string}]}]}` (produzido pela Task 2).

- [ ] **Step 1: Criar a página**

Criar `frontend/app/projetos/page.tsx`:

```tsx
'use client';
import { useState, useEffect, useCallback } from 'react';
import api from '../../lib/api';

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

const card: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8, cursor: 'pointer',
};

export default function ProjetosPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);

  const loadProjects = useCallback(async () => {
    try { setProjects((await api.get('/projects')).data.projects); } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { setLoading(true); loadProjects(); }, [loadProjects]);

  useEffect(() => {
    const id = setInterval(loadProjects, 30000);
    return () => clearInterval(id);
  }, [loadProjects]);

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
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
    </div>
  );
}
```

- [ ] **Step 2: Adicionar item de navegação**

Em `frontend/app/layout.tsx`, no array `NAV` (após a linha do `/containers`, por volta da linha 9):

```tsx
  { href: '/containers', label: 'Containers', icon: '🐳' },
  { href: '/projetos', label: 'Projetos', icon: '📦' },
```

- [ ] **Step 3: Rodar o build do frontend**

Run: `npm run build` (dentro de `frontend/`)
Expected: build limpo, sem erros de tipo.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/projetos/page.tsx frontend/app/layout.tsx
git -c user.name="Douglas Lundy" -c user.email="douglaslundy@gmail.com" commit -m "feat: página /projetos listando projetos da VPS agrupados por stack docker-compose"
```

Verificação manual (UI no navegador) fica por conta do usuário, conforme combinado nesta sessão.

---

## Deploy

Depois que todas as tasks estiverem commitadas e a suíte completa (`pytest -v`) passando:

```bash
git push origin main
ssh root@144.91.92.70 "cd /opt/vps-monitor && git pull --ff-only && bash monitor/deploy.sh"
```

Como o `docker-compose.yml` mudou (novo volume), confirmar que `deploy.sh` roda `docker compose up -d` (recria o container `monitor-backend` com o mount novo) e não só um restart — mesmo cuidado já tomado na feature de fail2ban.

Depois do deploy, checagem rápida (substitua `SEU_TOKEN` pelo token de login):

```bash
ssh root@144.91.92.70 "curl -s -H 'Authorization: Bearer SEU_TOKEN' http://localhost/api/projects | head -c 2000"
```

Confirmar que `mecanicapro` aparece com `dominio` preenchido (via arquivo dinâmico) e os demais projetos com domínio via label.
