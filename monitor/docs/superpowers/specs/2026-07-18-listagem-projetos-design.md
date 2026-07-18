# Listagem de Projetos da VPS

## Contexto

O usuário pediu uma nova página que liste todos os "projetos" rodando na VPS compartilhada (~9 stacks docker-compose além do próprio monitor): nome do projeto, domínio, quantidade de containers e consumo de recursos (CPU/RAM). Esta é a primeira das 3 tarefas restantes do backlog, na ordem definida pelo usuário: (1) listagem de projetos, (2) interface Traefik, (3) backup/restore.

Investigação feita na VPS de produção confirmou:
- Todo container carrega o label `com.docker.compose.project` — mecanismo natural de agrupamento (ex: `mecanicapro-backend-1`, `mecanicapro-frontend-1` etc. todos com `com.docker.compose.project=mecanicapro`). Um caso notável: `portainer` roda sob o projeto `traefik` (não é uma stack própria).
- 6 dos 7 projetos com domínio público resolvem via label `traefik.http.routers.*.rule` em pelo menos um container (reaproveitando a mesma regex já usada em `api/access_logs.py::container_para_sistema`: `_TRAEFIK_RULE_LABEL_RE` + `_HOST_RE`).
- `mecanicapro` é exceção: roteamento via arquivo dinâmico do Traefik (`/opt/traefik/dynamic/mecanicapro.yml`, regra `HostRegexp` para `*.dlsistemas.com.br`), sem nenhuma label de container. Usuário optou por também ler esse arquivo (mount read-only) em vez de deixar em branco.

## Objetivo

Nova página `/projetos` que agrupa os containers da VPS por projeto docker-compose e mostra: nome do projeto, domínio (quando resolvível), quantidade de containers, CPU % total e RAM total consumida.

## Fora de escopo

- Histórico/gráficos de evolução por projeto ao longo do tempo (dashboard "ao vivo" apenas, sem persistência nova).
- Qualquer ação (start/stop/delete) a partir desta página — isso já existe em `/containers`.
- Resolução de domínio para qualquer padrão de arquivo dinâmico do Traefik além de casar pelo nome do arquivo (`{projeto}.yml`) — suficiente para o único caso real hoje (mecanicapro). Padrões mais genéricos ficam para a spec de gestão do Traefik (próximo item do backlog).

## Design

### Backend — `collector/docker_client.py`

`collect_all()` passa a incluir as labels brutas do container no dict retornado:

```python
result.append({
    ...,
    "labels": container.get("Labels") or {},
})
```

Nenhuma outra mudança nesse arquivo. `Labels` já vem em `list_containers()` (payload cru da Docker API), só não era propagado pro dict final.

### Backend — `api/projects.py` (novo)

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

**Nota:** `mem_limit_mb` (por container) não é somado entre containers do projeto — quando um container não tem limite de memória explícito, o Docker reporta o "limite" como a RAM total do host, o que tornaria a soma sem sentido (ex: 5 containers sem limite = 5× a RAM do host). Em vez disso, `mem_percent_do_host` usa `mem_usage_mb` (uso real, sempre correto) dividido pela RAM total do host, já disponível em `_last_metrics["ram"]["total_mb"]` (populado por `collect_host_metrics()` no scheduler).

Registro em `main.py`: `from api.projects import router as projects_router` + `app.include_router(projects_router, prefix="/api", **_protected)` — mesmo padrão de `containers_router`/`metrics_router` (autenticação via `_protected` compartilhado, não embutida no router).

### Docker

`docker-compose.yml`, serviço `monitor-backend`, novo volume read-only:

```yaml
    volumes:
      - /opt/traefik/dynamic:/opt/traefik/dynamic:ro
```

Nenhuma mudança de permissão no `docker-socket-proxy` — os dados de container já vêm do cache de `_last_metrics` (populado a cada 30s pelo `collect_and_store()`), sem chamada nova à API do Docker.

### Frontend — nova página `app/projetos/page.tsx`

- Busca `GET /api/projects` a cada 30s (`setInterval`, mesmo padrão de `app/containers/page.tsx`).
- Tabela: Projeto | Domínio | Containers | CPU % | RAM (MB usado · % do host). Linha com `dominio: null` mostra "—".
- Clique na linha expande e mostra os containers membros (nome + status), estilo simples reaproveitando `card`/`input` do padrão visual já usado em `/seguranca`.
- Sem ações (start/stop/delete) — só leitura.
- Novo item no `NAV` de `app/layout.tsx`: `{ href: '/projetos', label: 'Projetos', icon: '📦' }`.

### Testes (TDD, backend)

- `docker_client.collect_all()`: container com labels mockadas → `"labels"` presente no dict de saída.
- `_dominio_por_labels`: container com `traefik.http.routers.x.rule` contendo `Host(...)` → extrai o host; nenhum container com a label → retorna `None`.
- `_dominio_por_arquivo_dinamico`: arquivo `{projeto}.yml` existente com `HostRegexp(...)` → extrai; arquivo inexistente → `None` (usa `TRAEFIK_DYNAMIC_DIR` configurável via env, mesmo padrão do `FAIL2BAN_JAIL_DIR`, apontando pra um dir temporário nos testes).
- `GET /api/projects`: agrupamento correto (containers de projetos diferentes não se misturam); soma de CPU/RAM por grupo; `mem_percent_do_host` calculado a partir de `mem_usage_mb` somado ÷ RAM total do host (nunca soma `mem_limit_mb`); container sem label de projeto cai em `"(sem projeto)"`; 401 sem token.

Frontend: `npm run build` limpo. Verificação manual fica por conta do usuário.

## Arquivos afetados

- **Novo:** `backend/api/projects.py`, `frontend/app/projetos/page.tsx`
- **Modificado:** `backend/collector/docker_client.py`, `backend/main.py`, `frontend/app/layout.tsx`, `docker-compose.yml`
- **Novo (testes):** `backend/tests/test_projects_api.py` (inclui os testes de `_dominio_por_labels`/`_dominio_por_arquivo_dinamico`, e o teste de labels em `test_docker_client.py`)
