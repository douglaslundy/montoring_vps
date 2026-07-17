# Alerta de Limpeza de Disco Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Job periódico que limpa build cache Docker automaticamente e dispara um alerta (reaproveitando `AlertRule`/`AlertLog`/`AlertNotification` já existentes) quando há imagens sem container associado acima de um threshold configurável — sem apagar nenhuma imagem automaticamente.

**Architecture:** `DockerClient` ganha `list_images`/`prune_build_cache`. Um novo job no scheduler (`check_docker_cleanup`, a cada 6h) poda o cache sempre e soma o tamanho de imagens com `Containers=0`, chamando a função genérica `_evaluate_rule` (já usada por todos os outros tipos de alerta) para cada `AlertRule` ativa com `metrica="docker_reclaimable_mb"`. `_evaluate_rule` ganha um parâmetro `extra_context` opcional para carregar a lista de imagens órfãs no alerta sem precisar duplicar lógica de disparo/notificação/resolução.

**Tech Stack:** FastAPI + SQLAlchemy + APScheduler + pytest (backend, TDD), Next.js/React/TypeScript (frontend, sem suíte de testes — build + verificação manual), Docker socket-proxy (env var).

## Global Constraints

- Nunca apagar imagem ou container automaticamente — só o build cache é limpo sem confirmação (sempre seguro, 0 reclaimable = 0 em uso).
- O alerta mostra dados brutos do Docker (imagem, tamanho, idade) — não tenta classificar "obsoleto" vs "rollback intencional".
- Job roda a cada 6 horas.
- Threshold padrão da regra: 500 MB.
- A regra padrão precisa aparecer tanto em bancos novos quanto em bancos que já existem (produção já tem regras seedadas) — backfill idempotente, não só o seed de banco novo.

---

### Task 1: `DockerClient.list_images` e `DockerClient.prune_build_cache`

**Files:**
- Modify: `backend/collector/docker_client.py`
- Test: `backend/tests/test_docker_client.py`

**Interfaces:**
- Produces: `async def list_images(self) -> list[dict]` — chama `GET /images/json` com `params={"all": False}`, retorna a lista de imagens como o Docker Engine API devolve (cada item tem `RepoTags`, `Size`, `Containers`).
- Produces: `async def prune_build_cache(self) -> dict` — chama `POST /build/prune` com `params={"all": "true"}`.
- Consumido pela Task 3.

- [ ] **Step 1: Escrever os testes (devem falhar)**

Adicionar ao final de `backend/tests/test_docker_client.py`:

```python
@pytest.mark.asyncio
async def test_list_images_chama_endpoint_correto():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_data = [
        {"Id": "sha256:abc", "RepoTags": ["corridas-app:latest"], "Size": 1330000000, "Containers": 1},
        {"Id": "sha256:def", "RepoTags": ["corridas-app:rollback-old"], "Size": 1320000000, "Containers": 0},
    ]
    mock_http = _make_mock_http_client(mock_data)
    with patch.object(client, "_client", return_value=mock_http):
        result = await client.list_images()

    assert result == mock_data
    mock_http.get.assert_called_once_with("/images/json", params={"all": False})


@pytest.mark.asyncio
async def test_prune_build_cache_chama_endpoint_correto():
    from collector.docker_client import DockerClient
    client = DockerClient()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"CachesDeleted": ["abc123"], "SpaceReclaimed": 131600000000}
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch.object(client, "_client", return_value=mock_http):
        result = await client.prune_build_cache()

    assert result == {"CachesDeleted": ["abc123"], "SpaceReclaimed": 131600000000}
    mock_http.post.assert_called_once_with("/build/prune", params={"all": "true"})
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_docker_client.py -v -k "list_images or prune_build_cache"`
Expected: FAIL — `AttributeError: 'DockerClient' object has no attribute 'list_images'`

- [ ] **Step 3: Implementar os métodos**

Em `backend/collector/docker_client.py`, logo depois de `remove_container`:

```python
    async def list_images(self) -> list[dict]:
        async with self._client() as c:
            r = await c.get("/images/json", params={"all": False})
            r.raise_for_status()
            return r.json()

    async def prune_build_cache(self) -> dict:
        async with self._client() as c:
            r = await c.post("/build/prune", params={"all": "true"})
            r.raise_for_status()
            return r.json()
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_docker_client.py -v -k "list_images or prune_build_cache"`
Expected: PASS (2 testes)

- [ ] **Step 5: Commit**

```bash
git add backend/collector/docker_client.py backend/tests/test_docker_client.py
git commit -m "feat: adiciona list_images e prune_build_cache ao DockerClient"
```

---

### Task 2: `_evaluate_rule` ganha parâmetro `extra_context`

**Files:**
- Modify: `backend/notifications/alert_engine.py`
- Test: `backend/tests/test_alert_engine.py`

**Interfaces:**
- Produces: `_evaluate_rule(session, rule, value, mensagem, now, vps_name, containers, extra_context: Optional[dict] = None)` — quando `extra_context` é fornecido (não `None`), usa esse dict diretamente como contexto do alerta, em vez de chamar `_build_metric_context`. Comportamento existente (contexto calculado automaticamente por métrica) é preservado quando `extra_context` não é passado. Consumido pela Task 3.

- [ ] **Step 1: Escrever o teste (deve falhar)**

Adicionar ao final de `backend/tests/test_alert_engine.py`:

```python
def test_evaluate_rule_usa_extra_context_quando_fornecido(fresh_db):
    from notifications.alert_engine import _evaluate_rule
    from sqlalchemy.orm import Session
    import json

    rule_id = add_rule(fresh_db, threshold=500.0, metrica="docker_reclaimable_mb", operador=">")

    with Session(fresh_db) as s:
        rule = s.get(AlertRule, rule_id)
        _evaluate_rule(
            s, rule, 800.0, "teste", datetime.utcnow(), "VPS Teste", [],
            extra_context={"imagens_orfas": [{"repo_tag": "old:latest", "tamanho_mb": 800.0}]},
        )
        s.commit()

    with Session(fresh_db) as s:
        log = s.query(AlertLog).first()
    assert log is not None
    contexto = json.loads(log.contexto)
    assert contexto["imagens_orfas"][0]["repo_tag"] == "old:latest"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -v -k "extra_context"`
Expected: FAIL — `TypeError: _evaluate_rule() got an unexpected keyword argument 'extra_context'`

- [ ] **Step 3: Implementar o parâmetro**

Em `backend/notifications/alert_engine.py`, trocar a assinatura e o corpo de `_evaluate_rule`:

```python
def _evaluate_rule(session: Session, rule: AlertRule, value: float, mensagem: str, now: datetime, vps_name: str, containers: list, extra_context: Optional[dict] = None):
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
        contexto = extra_context if extra_context is not None else _build_metric_context(rule.metrica, containers, session)
        open_log = AlertLog(
            rule_id=rule.id,
            triggered_at=now,
            severidade=rule.severidade,
            metrica=rule.metrica,
            valor_no_disparo=value,
            threshold=rule.threshold,
            mensagem=mensagem,
            vps_name=vps_name,
            contexto=json.dumps(contexto) if contexto else None,
        )
        session.add(open_log)
        session.flush()  # garante open_log.id para o FK de AlertNotification
```

(o restante do corpo da função, a partir de `if condition_true and open_log is not None:`, permanece exatamente igual)

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -v -k "extra_context"`
Expected: PASS

- [ ] **Step 5: Rodar toda a suíte de test_alert_engine.py**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -v`
Expected: todos os testes existentes continuam passando (mudança é aditiva — parâmetro novo com default `None`, nenhuma chamada existente de `_evaluate_rule` é afetada).

- [ ] **Step 6: Commit**

```bash
git add backend/notifications/alert_engine.py backend/tests/test_alert_engine.py
git commit -m "feat: _evaluate_rule aceita extra_context para contexto customizado"
```

---

### Task 3: Job `check_docker_cleanup`

**Files:**
- Modify: `backend/collector/scheduler.py`
- Test: `backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `docker_client.prune_build_cache()`, `docker_client.list_images()` (Task 1); `_evaluate_rule(..., extra_context=...)` (Task 2).
- Produces: `async def check_docker_cleanup()`, registrado no scheduler a cada 6 horas com `id="docker_cleanup"`.

- [ ] **Step 1: Escrever os testes (devem falhar)**

Adicionar ao final de `backend/tests/test_scheduler.py`:

```python
@pytest.mark.asyncio
async def test_check_docker_cleanup_poda_cache_sempre(test_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    import collector.scheduler as sched

    with patch.object(sched.docker_client, "prune_build_cache", AsyncMock(return_value={})) as mock_prune, \
         patch.object(sched.docker_client, "list_images", AsyncMock(return_value=[])):
        await sched.check_docker_cleanup()

    mock_prune.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_docker_cleanup_erro_list_images_nao_lanca_excecao(test_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    import collector.scheduler as sched

    with patch.object(sched.docker_client, "prune_build_cache", AsyncMock(return_value={})), \
         patch.object(sched.docker_client, "list_images", AsyncMock(side_effect=Exception("socket indisponivel"))):
        await sched.check_docker_cleanup()  # não deve levantar


@pytest.mark.asyncio
async def test_check_docker_cleanup_dispara_alerta_acima_do_threshold(test_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    import collector.scheduler as sched
    from sqlalchemy.orm import Session

    with Session(test_db.engine) as session:
        session.add(test_db.AlertRule(
            nome="Espaço em Disco Reaproveitável", metrica="docker_reclaimable_mb",
            operador=">", threshold=500, duracao_minutos=0, severidade="aviso",
            canal_email=0, canal_whatsapp=0, cooldown_minutos=1440, ativo=1,
        ))
        session.commit()

    mock_images = [
        {"Id": "sha256:abc", "RepoTags": ["corridas-app:latest"], "Size": 1330000000, "Containers": 1},
        {"Id": "sha256:def", "RepoTags": ["corridas-app:rollback-old"], "Size": 700 * 1024 * 1024, "Containers": 0},
    ]
    with patch.object(sched.docker_client, "prune_build_cache", AsyncMock(return_value={})), \
         patch.object(sched.docker_client, "list_images", AsyncMock(return_value=mock_images)):
        await sched.check_docker_cleanup()

    with Session(test_db.engine) as session:
        log = session.query(test_db.AlertLog).first()
    assert log is not None
    assert log.metrica == "docker_reclaimable_mb"
    assert log.valor_no_disparo == pytest.approx(700.0, abs=0.5)
    import json
    contexto = json.loads(log.contexto)
    assert contexto["imagens_orfas"][0]["repo_tag"] == "corridas-app:rollback-old"


@pytest.mark.asyncio
async def test_check_docker_cleanup_nao_dispara_sem_regra_ativa(test_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    import collector.scheduler as sched
    from sqlalchemy.orm import Session

    mock_images = [
        {"Id": "sha256:def", "RepoTags": ["old:latest"], "Size": 900 * 1024 * 1024, "Containers": 0},
    ]
    with patch.object(sched.docker_client, "prune_build_cache", AsyncMock(return_value={})), \
         patch.object(sched.docker_client, "list_images", AsyncMock(return_value=mock_images)):
        await sched.check_docker_cleanup()

    with Session(test_db.engine) as session:
        count = session.query(test_db.AlertLog).count()
    assert count == 0
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_scheduler.py -v -k "check_docker_cleanup"`
Expected: FAIL — `AttributeError: module 'collector.scheduler' has no attribute 'check_docker_cleanup'`

- [ ] **Step 3: Implementar a função e registrar o job**

Em `backend/collector/scheduler.py`, trocar a linha de import:

```python
from models.database import AccessLog, AccessLogDaily, AccessLogHourly, AlertNotification, ContainerDiskUsage, ContainerMetrics, MetricsHistory, engine
```

por:

```python
from models.database import AccessLog, AccessLogDaily, AccessLogHourly, AlertNotification, AlertRule, ContainerDiskUsage, ContainerMetrics, MetricsHistory, engine
```

E trocar:

```python
from notifications.alert_engine import evaluate
```

por:

```python
from notifications.alert_engine import _evaluate_rule, evaluate
```

Adicionar, logo depois de `get_last_metrics()`:

```python
async def check_docker_cleanup():
    try:
        await docker_client.prune_build_cache()
    except Exception:
        logger.exception("Erro ao limpar build cache do Docker")

    try:
        images = await docker_client.list_images()
    except Exception:
        logger.exception("Erro ao listar imagens Docker")
        return

    orfas = [img for img in images if (img.get("Containers") or 0) == 0]
    reclaimable_mb = sum((img.get("Size") or 0) for img in orfas) / 1024 ** 2

    now = datetime.utcnow()
    with Session(engine) as session:
        from api.config import get_config
        vps_name = get_config(session, "server_name", "VPS Monitor")
        rules = session.query(AlertRule).filter(
            AlertRule.ativo == 1, AlertRule.metrica == "docker_reclaimable_mb"
        ).all()
        if not rules:
            return

        extra_context = {
            "imagens_orfas": [
                {
                    "repo_tag": (img.get("RepoTags") or ["<none>:<none>"])[0],
                    "tamanho_mb": round((img.get("Size") or 0) / 1024 ** 2, 1),
                    "criada_em": img.get("Created"),
                }
                for img in orfas
            ]
        } if orfas else None

        for rule in rules:
            mensagem = f"{rule.nome}: {reclaimable_mb:.0f} MB em imagens sem container associado"
            _evaluate_rule(session, rule, reclaimable_mb, mensagem, now, vps_name, [], extra_context=extra_context)
        session.commit()
```

E em `start_scheduler()`, adicionar mais uma linha de `add_job`:

```python
def start_scheduler():
    scheduler.add_job(collect_and_store, "interval", seconds=30, id="collect", replace_existing=True)
    scheduler.add_job(collect_disk_usage, "interval", minutes=10, id="disk_usage", replace_existing=True)
    scheduler.add_job(tail_access_log, "interval", seconds=15, id="access_log_tail", replace_existing=True)
    scheduler.add_job(_cleanup, "interval", hours=1, id="cleanup", replace_existing=True)
    scheduler.add_job(check_docker_cleanup, "interval", hours=6, id="docker_cleanup", replace_existing=True)
    if not scheduler.running:
        scheduler.start()
    asyncio.ensure_future(collect_and_store())
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_scheduler.py -v -k "check_docker_cleanup"`
Expected: PASS (4 testes)

- [ ] **Step 5: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: `178 passed` (171 já existentes + 2 da Task 1 + 1 da Task 2 + 4 da Task 3 = 178 — conferir o número exato na saída, mas sem nenhum `FAILED`).

- [ ] **Step 6: Commit**

```bash
git add backend/collector/scheduler.py backend/tests/test_scheduler.py
git commit -m "feat: adiciona job periodico check_docker_cleanup"
```

---

### Task 4: Regra padrão com backfill + permissões no socket-proxy

**Files:**
- Modify: `backend/models/database.py`
- Modify: `docker-compose.yml`
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Produces: regra `AlertRule` com `nome="Espaço em Disco Reaproveitável"`, `metrica="docker_reclaimable_mb"` — existe tanto em bancos novos (seed) quanto em bancos que já tinham regras antes desta mudança (backfill idempotente).

- [ ] **Step 1: Escrever o teste (deve falhar)**

Adicionar ao final de `backend/tests/test_database.py`:

```python
def test_init_db_backfill_regra_espaco_reaproveitavel_banco_existente(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import importlib
    import models.database as db_module
    importlib.reload(db_module)
    db_module.init_db()

    from sqlalchemy.orm import Session
    with Session(db_module.engine) as session:
        # Remove a regra pra simular um banco de producao anterior a essa feature
        rule = session.query(db_module.AlertRule).filter_by(nome="Espaço em Disco Reaproveitável").first()
        assert rule is not None
        session.delete(rule)
        session.commit()

    db_module.init_db()  # roda de novo, como se fosse um redeploy

    with Session(db_module.engine) as session:
        rules = session.query(db_module.AlertRule).filter_by(nome="Espaço em Disco Reaproveitável").all()
    assert len(rules) == 1
    assert rules[0].metrica == "docker_reclaimable_mb"
    assert rules[0].threshold == 500
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_database.py -v -k "backfill_regra_espaco"`
Expected: FAIL — `assert rule is not None` falha (regra ainda não existe em `_DEFAULT_RULES`)

- [ ] **Step 3: Adicionar a regra padrão e o backfill**

Em `backend/models/database.py`, no final da lista `_DEFAULT_RULES` (depois da entrada `"Container Parado"`):

```python
    {"nome": "Espaço em Disco Reaproveitável", "metrica": "docker_reclaimable_mb", "operador": ">", "threshold": 500, "duracao_minutos": 0, "severidade": "aviso", "cooldown_minutos": 1440},
```

Em `init_db()`, logo depois do bloco:

```python
        if session.query(AlertRule).count() == 0:
            for rule in _DEFAULT_RULES:
                session.add(AlertRule(**rule))
```

adicionar:

```python
        if not session.query(AlertRule).filter_by(nome="Espaço em Disco Reaproveitável").first():
            session.add(AlertRule(
                nome="Espaço em Disco Reaproveitável", metrica="docker_reclaimable_mb",
                operador=">", threshold=500, duracao_minutos=0,
                severidade="aviso", cooldown_minutos=1440,
            ))
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_database.py -v -k "backfill_regra_espaco"`
Expected: PASS

- [ ] **Step 5: Rodar toda a suíte de test_database.py**

Run: `cd backend && py -m pytest tests/test_database.py -v`
Expected: todos os testes existentes continuam passando.

- [ ] **Step 6: Adicionar as permissões no socket-proxy**

Em `docker-compose.yml`, serviço `docker-socket-proxy`:

```yaml
    environment:
      - CONTAINERS=1
      - POST=1
      - DELETE=1
      - IMAGES=1
      - BUILD=1
```

- [ ] **Step 7: Commit**

```bash
git add backend/models/database.py backend/tests/test_database.py docker-compose.yml
git commit -m "feat: regra padrao de espaco reaproveitavel + permissoes IMAGES/BUILD no socket-proxy"
```

---

### Task 5: Frontend — métrica nova + renderização do contexto

**Files:**
- Modify: `frontend/app/alertas/page.tsx`

**Interfaces:**
- Consumes: `AlertLog.contexto` com o formato `{"imagens_orfas": [{"repo_tag": string, "tamanho_mb": number, "criada_em": string | null}]}` produzido pela Task 3.

- [ ] **Step 1: Adicionar a métrica à lista**

Trocar:

```tsx
const METRICAS = ['cpu_percent', 'ram_percent', 'disk_percent', 'temperature_c', 'load_1m', 'container_stopped']
```

por:

```tsx
const METRICAS = ['cpu_percent', 'ram_percent', 'disk_percent', 'temperature_c', 'load_1m', 'container_stopped', 'docker_reclaimable_mb']
```

E trocar:

```tsx
const METRICA_LABELS: Record<string, string> = {
  cpu_percent: 'CPU (%)',
  ram_percent: 'RAM (%)',
  disk_percent: 'Disco (%)',
  temperature_c: 'Temperatura (°C)',
  load_1m: 'Load Average 1m',
  container_stopped: 'Container Parado',
}
```

por:

```tsx
const METRICA_LABELS: Record<string, string> = {
  cpu_percent: 'CPU (%)',
  ram_percent: 'RAM (%)',
  disk_percent: 'Disco (%)',
  temperature_c: 'Temperatura (°C)',
  load_1m: 'Load Average 1m',
  container_stopped: 'Container Parado',
  docker_reclaimable_mb: 'Espaço Reaproveitável (Docker)',
}
```

- [ ] **Step 2: Adicionar a renderização do contexto**

Trocar:

```tsx
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
```

por:

```tsx
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
  if (ctx.imagens_orfas) {
    linhas.push(
      <div key="imagens_orfas">
        <strong>Imagens sem container associado: </strong>
        {ctx.imagens_orfas.map((i: any) => `${i.repo_tag} (${i.tamanho_mb} MB)`).join(', ')}
      </div>
    )
  }

  return linhas.length > 0 ? <div style={{ display: 'grid', gap: 4 }}>{linhas}</div> : renderContexto(null)
```

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build limpo, sem erros de tipo.

- [ ] **Step 4: Teste manual no navegador**

1. Abrir `/alertas` → aba "Regras". Confirmar que a regra "Espaço em Disco Reaproveitável" aparece na lista (criada pelo backfill da Task 4 ao rodar o backend local).
2. Clicar em "Editar" nessa regra → confirmar que "Espaço Reaproveitável (Docker)" aparece selecionado no dropdown de métrica.
3. (Opcional, exige Docker local ou dados de teste no banco) Se houver um `AlertLog` com `metrica="docker_reclaimable_mb"` e `contexto` preenchido, abrir o alerta na aba "Ativas" ou "Histórico" e confirmar que a lista de imagens órfãs aparece formatada.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/alertas/page.tsx
git commit -m "feat: adiciona metrica docker_reclaimable_mb na UI de alertas"
```

---

### Task 6: Deploy para produção

**Files:** nenhum (ação operacional, sem mudança de código)

**Atenção:** esta task só deve ser executada após confirmação explícita do usuário — abre `IMAGES=1`/`BUILD=1` no socket-proxy da VPS de produção (144.91.92.70) e registra um job novo que roda `docker build prune` automaticamente a cada 6h.

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

- [ ] **Step 4: Confirmar que a regra padrão foi criada em produção (backfill)**

```bash
ssh root@144.91.92.70 "docker exec monitor-backend python3 -c \"
import models.database as db
from sqlalchemy.orm import Session
with Session(db.engine) as s:
    r = s.query(db.AlertRule).filter_by(nome='Espaço em Disco Reaproveitável').first()
    print('regra existe:', r is not None, '- threshold:', r.threshold if r else None)
\""
```

Expected: `regra existe: True - threshold: 500.0`

- [ ] **Step 5: Confirmar que o job novo está agendado**

```bash
ssh root@144.91.92.70 "docker logs monitor-backend --tail 50 2>&1 | grep -i 'docker_cleanup\|scheduler'"
```

Se não aparecer nada de erro relacionado, o job foi registrado silenciosamente (comportamento normal — só loga em caso de erro). Para confirmar de forma mais direta, aguardar até 6h ou verificar manualmente chamando a função uma vez:

```bash
ssh root@144.91.92.70 "docker exec monitor-backend python3 -c \"
import asyncio
import collector.scheduler as sched
asyncio.run(sched.check_docker_cleanup())
print('ok')
\""
```

Expected: `ok`, sem traceback.
