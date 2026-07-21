# Fechar Lacunas de Monitoramento Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar 3 lacunas de monitoramento de risco de instabilidade — alerta de swap alto, alerta de container em restart loop (com sinalização de OOM), e atribuição de alertas de CPU/RAM por projeto — sem adicionar nenhuma infraestrutura nova (nenhum script no host, diferente das últimas 3 features).

**Architecture:** Tudo dentro do container `monitor-backend` já existente. Swap vem de `/proc/meminfo` (já montado via `PROC_BASE`). OOM de container reaproveita `docker_client.container_inspect()` (`State.OOMKilled`), já usado por `_evaluate_container_stopped`. Restart loop usa o histórico já coletado em `ContainerMetrics.restart_count` a cada 30s. Atribuição por projeto reaproveita `agrupar_por_projeto` (`api/_project_grouping.py`, já extraído na feature de backup/restore). As 3 regras novas (`Swap Alto`, `Swap Crítico`, `Container em Restart Loop`) usam o mesmo mecanismo de `AlertRule` já existente — aparecem automaticamente na tela de regras de alerta, sem UI nova pra configuração.

**Tech Stack:** FastAPI + SQLAlchemy + pytest (backend, TDD), Next.js/React/TypeScript (frontend, sem suíte de testes — build limpo).

## Global Constraints

- Nenhum script novo no host, nenhum mount novo no `docker-compose.yml` — tudo dentro do código Python já existente.
- As regras novas (`Swap Alto`, `Swap Crítico`, `Container em Restart Loop`) entram em `_DEFAULT_RULES` e são inseridas automaticamente por `init_db()` em bancos já existentes (mesmo padrão já usado pras 8 regras atuais — `init_db()` já faz `if session.query(AlertRule).count() == 0: ... insere todas`, então bancos **já populados** (produção) precisam do mesmo tratamento incremental já usado pra "Espaço em Disco Reaproveitável": checar se cada regra nova existe pelo nome antes de inserir, individualmente, fora do bloco `if count == 0`).
- `ContainerMetrics.container_id` grava o **ID curto** (`c["id"]`, 12 chars) — nunca usar `c["id_full"]` pra consultar essa tabela. `id_full` só serve pra chamar `docker_client.container_inspect()`.
- Restart loop usa os campos genéricos `threshold` (nº de reinícios) e `duracao_minutos` (janela em minutos) da própria `AlertRule` como parâmetros reais da avaliação — não hardcoded no código — pra ficar editável na tela de regras já existente.
- Toda mudança em `MetricsHistory` (novas colunas) segue o padrão `try/except` de `ALTER TABLE` já usado em `init_db()` — nunca migração que quebre um banco já em produção.

---

### Task 1: Monitoramento de Swap

**Files:**
- Modify: `backend/collector/host.py`
- Modify: `backend/models/database.py`
- Modify: `backend/collector/scheduler.py`
- Test: `backend/tests/test_host_collector.py`, `backend/tests/test_database.py`, `backend/tests/test_scheduler.py`, `backend/tests/test_alert_engine.py`

**Interfaces:**
- Produces: `_read_swap(proc_base) -> dict` (`{"total_mb", "used_mb", "percent"}`), incluído em `collect_host_metrics()` como chave `"swap"`. `MetricsHistory.swap_used_mb`/`swap_percent` (Float). `_get_metric_value("swap_percent", ...)`. Duas novas `AlertRule`: `"Swap Alto"` (aviso, 70%), `"Swap Crítico"` (crítico, 90%).

- [ ] **Step 1: Escrever os testes de `_read_swap` (devem falhar)**

Em `backend/tests/test_host_collector.py`, adicionar `SwapTotal`/`SwapFree` ao fixture `proc_dir` existente (não muda nenhum teste já existente, só adiciona linhas ao `meminfo`):

```python
@pytest.fixture
def proc_dir(tmp_path):
    p = tmp_path / "proc"
    p.mkdir()
    (p / "stat").write_text(
        "cpu  100 0 50 850 0 0 0 0 0 0\n"
        "cpu0 50 0 25 425 0 0 0 0 0 0\n"
    )
    (p / "loadavg").write_text("1.50 1.20 0.90 2/100 1234\n")
    (p / "cpuinfo").write_text(
        "processor\t: 0\nmodel name\t: AMD EPYC 7B13\n"
        "processor\t: 1\nmodel name\t: AMD EPYC 7B13\n"
    )
    (p / "meminfo").write_text(
        "MemTotal:       8192000 kB\n"
        "MemFree:        2048000 kB\n"
        "MemAvailable:   4096000 kB\n"
        "Buffers:         512000 kB\n"
        "Cached:         1024000 kB\n"
        "SwapTotal:      4194304 kB\n"
        "SwapFree:       2097152 kB\n"
    )
    (p / "uptime").write_text("443742.12 1234567.89\n")
    net = p / "net"
    net.mkdir()
    (net / "dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo:    1000       10    0    0    0     0          0         0     1000      10    0    0    0     0       0          0\n"
        "  eth0: 1048576     1000    0    0    0     0          0         0   524288     500    0    0    0     0       0          0\n"
    )
    return str(p)
```

Adicionar o teste novo (junto dos outros `test_ram`, `test_uptime`, etc.):

```python
def test_swap(proc_dir, sys_dir):
    import collector.host as h
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    assert result["swap"]["total_mb"] == pytest.approx(4096.0, abs=1)
    assert result["swap"]["used_mb"] == pytest.approx(2048.0, abs=1)
    assert result["swap"]["percent"] == pytest.approx(50.0, abs=1)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_host_collector.py -v`
Expected: `test_swap` FAIL — `KeyError: 'swap'`. Os outros testes do arquivo continuam passando (a mudança no fixture não quebra nada, já que os testes existentes não checam swap).

- [ ] **Step 3: Implementar `_read_swap` e ligar em `collect_host_metrics`**

Em `backend/collector/host.py`, adicionar logo depois de `_read_ram`:

```python
def _read_swap(proc_base):
    swap = {}
    keys = {"SwapTotal", "SwapFree"}
    with open(f"{proc_base}/meminfo") as f:
        for line in f:
            parts = line.split()
            key = parts[0].rstrip(":")
            if key in keys:
                swap[key] = int(parts[1])
    total_mb = swap.get("SwapTotal", 0) / 1024
    free_mb = swap.get("SwapFree", 0) / 1024
    used_mb = total_mb - free_mb
    pct = round(used_mb / total_mb * 100, 1) if total_mb else 0.0
    return {"total_mb": round(total_mb, 1), "used_mb": round(used_mb, 1), "percent": pct}
```

Em `collect_host_metrics()`, adicionar `"swap": _read_swap(proc_base),` no dict retornado, logo depois de `"ram": _read_ram(proc_base),`.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_host_collector.py -v`
Expected: PASS (todos, incluindo `test_swap`)

- [ ] **Step 5: Escrever o teste de `MetricsHistory` (deve falhar)**

Em `backend/tests/test_database.py`, adicionar:

```python
def test_insert_metrics_history_com_swap(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        record = test_db.MetricsHistory(
            collected_at=datetime.utcnow(),
            cpu_percent=10.0, ram_percent=50.0, disk_percent=30.0,
            swap_used_mb=2048.0, swap_percent=50.0,
        )
        session.add(record)
        session.commit()
        fetched = session.query(test_db.MetricsHistory).first()
    assert fetched.swap_percent == 50.0
    assert fetched.swap_used_mb == 2048.0
```

- [ ] **Step 6: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_database.py -v -k swap`
Expected: FAIL — `TypeError: 'swap_used_mb' is an invalid keyword argument for MetricsHistory`

- [ ] **Step 7: Adicionar as colunas, o `ALTER TABLE` e as 2 regras novas**

Em `backend/models/database.py`, na classe `MetricsHistory`, adicionar logo depois de `ram_percent`:

```python
    swap_used_mb = Column(Float)
    swap_percent = Column(Float)
```

Em `_DEFAULT_RULES`, adicionar logo depois de `"RAM Crítica"`:

```python
    {"nome": "Swap Alto", "metrica": "swap_percent", "operador": ">", "threshold": 70, "duracao_minutos": 5, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "Swap Crítico", "metrica": "swap_percent", "operador": ">", "threshold": 90, "duracao_minutos": 2, "severidade": "critico", "cooldown_minutos": 15},
```

Em `init_db()`, no bloco `with engine.connect() as conn:` que já faz os `ALTER TABLE` de `alert_log` (try/except, "Coluna já existe"), adicionar:

```python
        try:
            conn.execute(text("ALTER TABLE metrics_history ADD COLUMN swap_used_mb FLOAT"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
        try:
            conn.execute(text("ALTER TABLE metrics_history ADD COLUMN swap_percent FLOAT"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
```

Ainda em `init_db()`, logo depois do bloco que insere `_DEFAULT_RULES` (dentro do `if session.query(AlertRule).count() == 0:`) e do `if not session.query(AlertRule).filter_by(nome="Espaço em Disco Reaproveitável").first():` já existente, adicionar o mesmo padrão pras 2 regras novas (garante que bancos **já em produção**, que não passam pelo `count() == 0`, também recebam as regras novas):

```python
        if not session.query(AlertRule).filter_by(nome="Swap Alto").first():
            session.add(AlertRule(
                nome="Swap Alto", metrica="swap_percent", operador=">", threshold=70,
                duracao_minutos=5, severidade="aviso", cooldown_minutos=30,
            ))
        if not session.query(AlertRule).filter_by(nome="Swap Crítico").first():
            session.add(AlertRule(
                nome="Swap Crítico", metrica="swap_percent", operador=">", threshold=90,
                duracao_minutos=2, severidade="critico", cooldown_minutos=15,
            ))
```

- [ ] **Step 8: Atualizar o teste de contagem de regras padrão**

Em `backend/tests/test_database.py`, `test_regras_padrao_inseridas` hoje espera `count == 10`. Mudar pra `count == 12` (10 + `Swap Alto` + `Swap Crítico`).

- [ ] **Step 9: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_database.py -v`
Expected: PASS (todos, incluindo `test_insert_metrics_history_com_swap` e `test_regras_padrao_inseridas` com o novo total)

- [ ] **Step 10: Extender `_get_metric_value` e testar**

Em `backend/tests/test_alert_engine.py`, adicionar (junto aos outros testes de `_get_metric_value`, indiretamente testados via `evaluate`):

```python
def test_swap_alto_cria_alerta(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=70.0, metrica="swap_percent", operador=">")
    metrics = make_metrics()
    metrics["swap"] = {"percent": 85.0}
    result = asyncio.run(evaluate(metrics, []))
    assert len(result) == 1
    assert result[0]["metrica"] == "swap_percent"
```

Em `backend/notifications/alert_engine.py`, `_get_metric_value`, adicionar:

```python
    if metrica == "swap_percent":
        return metrics.get("swap", {}).get("percent")
```

- [ ] **Step 11: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -v -k swap`
Expected: PASS

- [ ] **Step 12: Ligar o swap no `scheduler.py` (payload + insert) e testar**

Em `backend/tests/test_scheduler.py`, `test_collect_and_store_salva_no_banco`, adicionar `"swap"` ao `mock_host` e uma asserção:

```python
    mock_host = {
        "cpu": {"percent": 25.0, "load": [1.0, 0.8, 0.6], "cores": 4, "model": "Test CPU"},
        "ram": {"total_mb": 8192, "used_mb": 2048, "available_mb": 6144, "percent": 25.0},
        "swap": {"total_mb": 4096, "used_mb": 1024, "percent": 25.0},
        "disk": {"total_gb": 100.0, "used_gb": 30.0, "available_gb": 70.0, "percent": 30.0, "mountpoint": "/"},
        "net": {"rx_bytes_s": 1024, "tx_bytes_s": 512, "interface": "eth0"},
        "uptime": {"days": 1, "hours": 2, "minutes": 30, "seconds": 95400},
        "temperature_c": 42.5,
    }
```

E depois do `assert row.temperature_c == 42.5`, adicionar:

```python
    assert row.swap_percent == 25.0
    assert row.swap_used_mb == 1024.0
```

Em `backend/collector/scheduler.py`, `collect_and_store()`, adicionar ao insert de `MetricsHistory` (logo depois de `ram_percent=host["ram"]["percent"],`):

```python
                swap_used_mb=host["swap"]["used_mb"],
                swap_percent=host["swap"]["percent"],
```

E ao dict `payload` (logo depois de `"ram": host["ram"],`):

```python
            "swap": host["swap"],
```

- [ ] **Step 13: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_scheduler.py -v`
Expected: PASS

- [ ] **Step 14: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: todos os testes passando, sem `FAILED`.

- [ ] **Step 15: Commit**

```bash
git add backend/collector/host.py backend/models/database.py backend/collector/scheduler.py backend/tests/test_host_collector.py backend/tests/test_database.py backend/tests/test_scheduler.py backend/tests/test_alert_engine.py
git commit -m "feat: adiciona monitoramento de swap com alertas Alto/Critico"
```

---

### Task 2: Alerta de Container em Restart Loop (com sinalização de OOM)

**Files:**
- Modify: `backend/models/database.py`
- Modify: `backend/notifications/alert_engine.py`
- Test: `backend/tests/test_database.py`, `backend/tests/test_alert_engine.py`

**Interfaces:**
- Consumes: `ContainerMetrics` (já existente, populado pelo `scheduler.py` a cada 30s).
- Produces: `_evaluate_restart_loop(session, rule, containers, now, vps_name, docker_client=None)`, ligado em `evaluate()`. Nova `AlertRule` `"Container em Restart Loop"`.

- [ ] **Step 1: Adicionar a regra padrão e atualizar o teste de contagem**

Em `backend/models/database.py`, em `_DEFAULT_RULES`, adicionar (logo depois de `"Container Parado"`):

```python
    {"nome": "Container em Restart Loop", "metrica": "container_restart_loop", "operador": ">=", "threshold": 3, "duracao_minutos": 10, "severidade": "critico", "cooldown_minutos": 30},
```

Em `init_db()`, no mesmo bloco de `if not session.query(AlertRule).filter_by(nome=...)` adicionado na Task 1, adicionar (garante bancos já em produção):

```python
        if not session.query(AlertRule).filter_by(nome="Container em Restart Loop").first():
            session.add(AlertRule(
                nome="Container em Restart Loop", metrica="container_restart_loop", operador=">=",
                threshold=3, duracao_minutos=10, severidade="critico", cooldown_minutos=30,
            ))
```

Em `backend/tests/test_database.py`, `test_regras_padrao_inseridas` (já em 12 após a Task 1): mudar pra `count == 13`.

- [ ] **Step 2: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_database.py -v -k regras_padrao`
Expected: PASS (13)

- [ ] **Step 3: Escrever os testes de `_evaluate_restart_loop` (devem falhar)**

Em `backend/tests/test_alert_engine.py`, adicionar:

```python
def _add_container_metrics(engine, container_id, restart_counts, minutos_atras_inicial=9):
    """Insere uma série de ContainerMetrics simulando o histórico de restart_count
    nos últimos `minutos_atras_inicial` minutos (1 ponto por minuto, mais recente por último)."""
    from datetime import timedelta
    from models.database import ContainerMetrics
    now = datetime.utcnow()
    with Session(engine) as s:
        for i, rc in enumerate(restart_counts):
            minutos_atras = minutos_atras_inicial - i
            s.add(ContainerMetrics(
                collected_at=now - timedelta(minutes=minutos_atras),
                container_id=container_id, container_name="worker",
                restart_count=rc, status="running",
            ))
        s.commit()


def test_restart_loop_dispara_com_3_aumentos_em_10min(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_restart_loop", operador=">=", threshold=3, duracao_minutos=10, cooldown_minutos=30)
    _add_container_metrics(fresh_db, "abc123", [0, 1, 1, 2, 2, 3, 3, 3, 3, 3])
    containers = [{"id": "abc123", "id_full": "abc123fullid", "name": "worker", "status": "running"}]
    result = asyncio.run(evaluate(make_metrics(), containers))
    assert any("worker" in r["mensagem"] and "restart loop" in r["mensagem"] for r in result)


def test_restart_loop_nao_dispara_abaixo_do_threshold(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_restart_loop", operador=">=", threshold=3, duracao_minutos=10, cooldown_minutos=30)
    _add_container_metrics(fresh_db, "abc123", [0, 0, 0, 1, 1, 1, 1, 1, 1, 1])
    containers = [{"id": "abc123", "id_full": "abc123fullid", "name": "worker", "status": "running"}]
    result = asyncio.run(evaluate(make_metrics(), containers))
    assert result == []


def test_restart_loop_contexto_sinaliza_oom(fresh_db):
    from unittest.mock import AsyncMock
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_restart_loop", operador=">=", threshold=3, duracao_minutos=10, cooldown_minutos=30)
    _add_container_metrics(fresh_db, "abc123", [0, 1, 1, 2, 2, 3, 3, 3, 3, 3])
    containers = [{"id": "abc123", "id_full": "abc123fullid", "name": "worker", "status": "running"}]

    mock_dc = AsyncMock()
    mock_dc.container_inspect = AsyncMock(return_value={"State": {"OOMKilled": True}})
    asyncio.run(evaluate(make_metrics(), containers, mock_dc))

    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.metrica == "container_restart_loop").first()
    ctx = json.loads(log.contexto)
    assert ctx["oom_killed"] is True
    mock_dc.container_inspect.assert_awaited_once_with("abc123fullid")


def test_restart_loop_resolve_quando_para_de_reiniciar(fresh_db):
    from notifications.alert_engine import evaluate
    from models.database import ContainerMetrics
    add_rule(fresh_db, metrica="container_restart_loop", operador=">=", threshold=3, duracao_minutos=10, cooldown_minutos=30)
    _add_container_metrics(fresh_db, "abc123", [0, 1, 1, 2, 2, 3, 3, 3, 3, 3])
    containers = [{"id": "abc123", "id_full": "abc123fullid", "name": "worker", "status": "running"}]
    asyncio.run(evaluate(make_metrics(), containers))
    assert count_open(fresh_db) == 1

    # Simula a janela de 10min avançando (equivalente ao que aconteceria de
    # verdade com o tempo passando entre execuções do scheduler): remove os
    # pontos antigos (que tinham os aumentos) e insere só histórico estável.
    # Sem isso, os pontos antigos continuariam dentro da janela de 10min
    # (o teste roda em milissegundos, não passa tempo real de verdade) e o
    # alerta nunca resolveria.
    with Session(fresh_db) as s:
        s.query(ContainerMetrics).filter(ContainerMetrics.container_id == "abc123").delete()
        s.commit()
    _add_container_metrics(fresh_db, "abc123", [3] * 10)
    asyncio.run(evaluate(make_metrics(), containers))
    assert count_open(fresh_db) == 0
```

- [ ] **Step 4: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -v -k restart_loop`
Expected: FAIL — regra `container_restart_loop` cai no `else` genérico de `evaluate()` (usa `_get_metric_value`, que não conhece essa métrica, retorna `None`, pula a regra) — os 4 testes falham por não encontrar o alerta esperado.

- [ ] **Step 5: Implementar `_evaluate_restart_loop`**

Em `backend/notifications/alert_engine.py`, adicionar o import de `timedelta` e `ContainerMetrics`:

```python
from datetime import datetime, timedelta
```

```python
from models.database import AlertLog, AlertNotification, AlertRule, ContainerDiskUsage, ContainerMetrics, engine
```

Adicionar a função, logo depois de `_evaluate_container_stopped`:

```python
async def _evaluate_restart_loop(session: Session, rule: AlertRule, containers: list, now: datetime, vps_name: str, docker_client=None):
    """Avalia regra especial de restart loop — uma instância por container."""
    janela_inicio = now - timedelta(minutes=rule.duracao_minutos)
    containers_em_loop = set()

    for c in containers:
        # ContainerMetrics.container_id grava o ID curto (c["id"]), não o
        # id_full usado pra inspecionar via API do Docker — os dois campos
        # têm valores diferentes, usar o errado faz a consulta não achar nada.
        container_id = c.get("id")
        id_full = c.get("id_full") or container_id
        name = c.get("name", "unknown")
        if not container_id:
            continue

        contagens = (
            session.query(ContainerMetrics.restart_count)
            .filter(
                ContainerMetrics.container_id == container_id,
                ContainerMetrics.collected_at >= janela_inicio,
            )
            .order_by(ContainerMetrics.collected_at)
            .all()
        )
        valores = [r[0] for r in contagens if r[0] is not None]
        if len(valores) < 2:
            continue
        aumentos = sum(1 for i in range(1, len(valores)) if valores[i] > valores[i - 1])
        if aumentos < rule.threshold:
            continue

        containers_em_loop.add(name)
        mensagem = f"Container '{name}' em restart loop ({aumentos} reinícios em {rule.duracao_minutos}min)"
        open_log = (
            session.query(AlertLog)
            .filter(AlertLog.rule_id == rule.id, AlertLog.resolved_at.is_(None), AlertLog.mensagem == mensagem)
            .first()
        )
        if open_log is None:
            contexto = {"reinicios": aumentos, "janela_minutos": rule.duracao_minutos}
            if docker_client is not None:
                try:
                    inspect = await docker_client.container_inspect(id_full)
                    contexto["oom_killed"] = inspect.get("State", {}).get("OOMKilled")
                except Exception:
                    logger.exception("Erro ao inspecionar container em restart loop %s", name)

            open_log = AlertLog(
                rule_id=rule.id, triggered_at=now, severidade=rule.severidade,
                metrica="container_restart_loop", valor_no_disparo=aumentos, threshold=rule.threshold,
                mensagem=mensagem, vps_name=vps_name, contexto=json.dumps(contexto),
            )
            session.add(open_log)
            session.flush()

        cooldown_ok = (
            open_log.last_notified_at is None or
            (now - open_log.last_notified_at).total_seconds() / 60 >= rule.cooldown_minutos
        )
        if cooldown_ok:
            _notify_alert(session, open_log, rule, now)

    # Resolve alertas de containers que pararam de reiniciar nesta janela
    open_logs = (
        session.query(AlertLog)
        .filter(AlertLog.rule_id == rule.id, AlertLog.resolved_at.is_(None))
        .all()
    )
    for log in open_logs:
        m = re.search(r"Container '(.+)' em restart loop", log.mensagem or "")
        if not m:
            continue
        if m.group(1) not in containers_em_loop:
            log.resolved_at = now
            _notify_resolution(session, log, rule)
```

Em `evaluate()`, adicionar o `elif` (junto do `if rule.metrica == "container_stopped"` já existente):

```python
                    if rule.metrica == "container_stopped":
                        await _evaluate_container_stopped(session, rule, containers, now, vps_name, docker_client)
                    elif rule.metrica == "container_restart_loop":
                        await _evaluate_restart_loop(session, rule, containers, now, vps_name, docker_client)
                    else:
```

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -v -k restart_loop`
Expected: PASS (4 testes)

- [ ] **Step 7: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: todos os testes passando, sem `FAILED`.

- [ ] **Step 8: Commit**

```bash
git add backend/models/database.py backend/notifications/alert_engine.py backend/tests/test_database.py backend/tests/test_alert_engine.py
git commit -m "feat: adiciona alerta de restart loop de container com sinalizacao de OOM"
```

---

### Task 3: Atribuição de Alertas por Projeto

**Files:**
- Modify: `backend/notifications/alert_engine.py`
- Test: `backend/tests/test_alert_engine.py`

**Interfaces:**
- Consumes: `agrupar_por_projeto` (`api/_project_grouping.py`, já existente).
- Produces: `_top_projetos(containers, key, n=3) -> list`, usado dentro de `_build_metric_context` pra `cpu_percent`/`load_1m`/`ram_percent`, adicionando `ctx["top_projetos"]`.

- [ ] **Step 1: Escrever os testes (devem falhar)**

Em `backend/tests/test_alert_engine.py`, adicionar:

```python
def test_top_projetos_agrupa_soma_e_ordena():
    from notifications.alert_engine import _top_projetos
    containers = [
        {"name": "mecanicapro-backend-1", "cpu_percent": 30.0, "labels": {"com.docker.compose.project": "mecanicapro"}},
        {"name": "mecanicapro-worker-1", "cpu_percent": 20.0, "labels": {"com.docker.compose.project": "mecanicapro"}},
        {"name": "corridas-app", "cpu_percent": 15.0, "labels": {"com.docker.compose.project": "corridas"}},
        {"name": "orfao", "cpu_percent": 5.0, "labels": {}},
    ]
    resultado = _top_projetos(containers, "cpu_percent")
    assert resultado[0]["nome"] == "mecanicapro"
    assert resultado[0]["valor"] == 50.0
    assert resultado[1]["nome"] == "corridas"
    assert resultado[1]["valor"] == 15.0
    assert all(p["nome"] != "(sem projeto)" for p in resultado)


def test_cpu_alert_grava_contexto_top_projetos(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=80.0, metrica="cpu_percent", operador=">")
    containers = [
        {"name": "mecanicapro-backend-1", "cpu_percent": 70.0, "mem_percent": 0.0,
         "labels": {"com.docker.compose.project": "mecanicapro"}, "net_rx_mb": 0.0, "net_tx_mb": 0.0},
        {"name": "corridas-app", "cpu_percent": 10.0, "mem_percent": 0.0,
         "labels": {"com.docker.compose.project": "corridas"}, "net_rx_mb": 0.0, "net_tx_mb": 0.0},
    ]
    asyncio.run(evaluate(make_metrics(cpu=90.0), containers))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
    ctx = json.loads(log.contexto)
    assert ctx["top_projetos"][0]["nome"] == "mecanicapro"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -v -k top_projetos`
Expected: FAIL — `ImportError: cannot import name '_top_projetos'`

- [ ] **Step 3: Implementar `_top_projetos` e ligar em `_build_metric_context`**

Em `backend/notifications/alert_engine.py`, adicionar logo depois de `_top_by_rede`:

```python
def _top_projetos(containers: list, key: str, n: int = 3) -> list:
    from api._project_grouping import agrupar_por_projeto
    grupos = agrupar_por_projeto(containers)
    somas = [
        {"nome": nome, "valor": round(sum(c.get(key, 0) or 0 for c in membros), 1)}
        for nome, membros in grupos.items() if nome != "(sem projeto)"
    ]
    return sorted(somas, key=lambda p: p["valor"], reverse=True)[:n]
```

Em `_build_metric_context`, adicionar `top_projetos` nos blocos de `cpu_percent`/`load_1m` e `ram_percent`:

```python
def _build_metric_context(metrica: str, containers: list, session: Session) -> Optional[dict]:
    if metrica in ("cpu_percent", "load_1m"):
        ctx = {}
        top_cpu = _top_by(containers, "cpu_percent")
        top_rede = _top_by_rede(containers)
        top_projetos = _top_projetos(containers, "cpu_percent")
        if top_cpu:
            ctx["top_cpu"] = top_cpu
        if top_rede:
            ctx["top_rede"] = top_rede
        if top_projetos:
            ctx["top_projetos"] = top_projetos
        return ctx or None
    if metrica == "ram_percent":
        ctx = {}
        top_mem = _top_by(containers, "mem_percent")
        top_rede = _top_by_rede(containers)
        top_projetos = _top_projetos(containers, "mem_percent")
        if top_mem:
            ctx["top_mem"] = top_mem
        if top_rede:
            ctx["top_rede"] = top_rede
        if top_projetos:
            ctx["top_projetos"] = top_projetos
        return ctx or None
    if metrica == "disk_percent":
        top_disco = _top_disco(session)
        return {"top_disco": top_disco} if top_disco else None
    return None
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -v -k "top_projetos or cpu_alert"`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: todos os testes passando, sem `FAILED`.

- [ ] **Step 6: Commit**

```bash
git add backend/notifications/alert_engine.py backend/tests/test_alert_engine.py
git commit -m "feat: adiciona atribuicao de alertas de CPU/RAM por projeto"
```

---

### Task 4: Frontend — Card de Swap no Dashboard

**Files:**
- Modify: `frontend/lib/ws.ts`
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Consumes: `swap` no payload de métricas (Task 1, já entregue via websocket).

- [ ] **Step 1: Adicionar `swap` ao tipo `MetricsPayload`**

Em `frontend/lib/ws.ts`, no `interface MetricsPayload`, adicionar logo depois de `ram`:

```typescript
  swap: { total_mb: number; used_mb: number; percent: number };
```

- [ ] **Step 2: Adicionar o card de Swap no dashboard**

Em `frontend/app/page.tsx`, no grid de `MetricCard` (dentro de `{/* Cards de resumo */}`), adicionar logo depois do card de "Disco":

```tsx
        <MetricCard
          title="Swap"
          value={`${effectiveData?.swap?.percent?.toFixed(1) ?? '—'}%`}
          subtitle={effectiveData ? `${(effectiveData.swap.used_mb / 1024).toFixed(1)} GB / ${(effectiveData.swap.total_mb / 1024).toFixed(1)} GB` : undefined}
          percent={effectiveData?.swap?.percent}
        />
```

Não mexer em mais nada no arquivo (grid `repeat(5, 1fr)` continua igual — com 9 cards em vez de 8, o CSS grid já quebra em duas linhas automaticamente, sem precisar mudar `gridTemplateColumns`).

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build limpo, sem erros de tipo.

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/ws.ts frontend/app/page.tsx
git commit -m "feat: adiciona card de Swap no dashboard"
```

---

### Task 5: Deploy para produção

**Files:** nenhum (ação operacional, sem mudança de código)

**Atenção:** diferente das últimas 3 features, esta **não precisa de nenhum passo extra de infraestrutura** (sem mount novo, sem pacote novo no host, sem cron novo) — só rebuild + restart dos containers já existentes. Ainda assim, pede confirmação do usuário antes (mesmo padrão das features anteriores), já que altera `docker-compose` em produção.

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

- [ ] **Step 4: Confirmar que as 2 novas regras (+ a de restart loop) foram semeadas no banco de produção**

```bash
ssh root@144.91.92.70 "sqlite3 /var/lib/docker/volumes/vps-monitor_vps_monitor_data/_data/monitor.db \"SELECT nome, metrica, threshold FROM alert_rules WHERE metrica IN ('swap_percent', 'container_restart_loop');\""
```

Expected: 3 linhas (`Swap Alto`, `Swap Crítico`, `Container em Restart Loop`) — confirma que o `init_db()` incremental rodou corretamente no banco já existente em produção (não só em banco novo).

- [ ] **Step 5: Confirmar logs limpos**

```bash
ssh root@144.91.92.70 "docker logs monitor-backend --tail 30 2>&1"
```

Expected: sem erros de import/inicialização, ciclo de coleta rodando normalmente.

- [ ] **Step 6: Verificação visual (usuário)**

Card de Swap aparecendo no dashboard (`https://monitor.dlsistemas.com.br`), refletindo o uso real de swap da VPS (~52% no momento do brainstorming). Sem teste manual de "forçar restart loop" — seria destrutivo sem necessidade; a confiança vem da suíte de testes.
