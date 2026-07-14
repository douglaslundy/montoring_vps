# Acessos por Serviço + Gráficos de Acessos e Recursos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reordenar a tabela da página Acessos para agrupar por sistema/serviço (com toggle para ver IPs por sistema), e adicionar dois gráficos de linha (acessos por projeto e recursos do container correspondente) com filtro Dia (últimas 12h ou dia específico) e Mês.

**Architecture:** Backend FastAPI/SQLAlchemy: uma tabela nova (`AccessLogHourly`) para dar granularidade de hora aos acessos, três endpoints novos em `access_logs.py` (`summary-por-sistema`, `container-para-sistema`, `timeseries`) e um em `metrics.py` (`container-history`), compartilhando um helper de bucketing de tempo (`api/time_buckets.py`). Frontend Next.js: reescreve a tabela de `acessos/page.tsx` e adiciona um componente novo (`AccessProjectCharts.tsx`) que reaproveita o `LineChart` já existente.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy / SQLite / pytest / pytest-asyncio (backend). Next.js 16 / React 18 / TypeScript / recharts / axios (frontend, sem framework de teste automatizado — verificação manual via `npm run dev`).

## Global Constraints

- Todo dado de tempo é tratado em **UTC** — sem conversão de fuso horário local (igual ao resto do sistema de acessos já existente).
- Buckets sem nenhum acesso no gráfico de **acessos** retornam `value: 0` (ausência de acesso é zero, não "sem amostra").
- Buckets sem nenhuma coleta no gráfico de **recursos** retornam campos `null` (ausência de coleta é "sem amostra", diferente de zero).
- Nenhum mapeamento manual sistema→container — só via labels do Traefik lidas do `docker.sock` (ver Tarefa 5).
- Comandos de teste do backend devem ser rodados de dentro de `monitor/backend/` com `py -3 -m pytest tests/<arquivo> -v` (confirmado que `python` sozinho não está no PATH deste ambiente, mas `py -3` está).

---

### Task 1: Tabela `AccessLogHourly`

**Files:**
- Modify: `backend/models/database.py` (logo após o bloco de `AccessLogDaily` + seus índices, antes de `class IpGeoCache`)
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Produces: `AccessLogHourly` (colunas `id`, `hour: str` formato `"YYYY-MM-DD HH"` UTC, `sistema: str`, `count: int`), usada pelas Tarefas 2, 3 e 7.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `backend/tests/test_database.py`:

```python
def test_tabela_access_log_hourly_criada(test_db):
    with test_db.engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result}
    assert "access_log_hourly" in tables


def test_insert_access_log_hourly(test_db):
    with Session(test_db.engine) as session:
        session.add(test_db.AccessLogHourly(
            hour="2026-07-12 14", sistema="app2.dlsistemas.com.br", count=5,
        ))
        session.commit()
        fetched = session.query(test_db.AccessLogHourly).first()
    assert fetched.count == 5
    assert fetched.hour == "2026-07-12 14"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -3 -m pytest tests/test_database.py -v -k access_log_hourly`
Expected: FAIL com `AttributeError: module 'models.database' has no attribute 'AccessLogHourly'`

- [ ] **Step 3: Implementar o modelo**

Em `backend/models/database.py`, logo depois de:

```python
Index("ix_access_log_daily_day", AccessLogDaily.day)
Index("ix_access_log_daily_ip", AccessLogDaily.ip)
```

adicionar:

```python


class AccessLogHourly(Base):
    __tablename__ = "access_log_hourly"
    id = Column(Integer, primary_key=True, autoincrement=True)
    hour = Column(String, nullable=False)      # "YYYY-MM-DD HH", UTC
    sistema = Column(String, nullable=False)
    count = Column(Integer, nullable=False, default=0)


Index("ix_access_log_hourly_hour", AccessLogHourly.hour)
Index("ix_access_log_hourly_sistema", AccessLogHourly.sistema)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -3 -m pytest tests/test_database.py -v`
Expected: todos os testes (incluindo os 2 novos) PASS

- [ ] **Step 5: Commit**

```bash
git add backend/models/database.py backend/tests/test_database.py
git commit -m "feat: adiciona tabela access_log_hourly para granularidade por hora"
```

---

### Task 2: Coletor grava `AccessLogHourly`

**Files:**
- Modify: `backend/collector/access_log_tailer.py`
- Test: `backend/tests/test_access_log_tailer.py`

**Interfaces:**
- Consumes: `db_module.AccessLogHourly` (Tarefa 1).
- Produces: `_upsert_hourly(session, hour: str, sistema: str) -> None`, chamada de dentro de `_process_line`.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao final de `backend/tests/test_access_log_tailer.py`:

```python
@pytest.mark.asyncio
async def test_processa_linha_grava_access_log_hourly(test_db, tmp_path, monkeypatch):
    when = datetime.utcnow()
    log_file = tmp_path / "access.log"
    log_file.write_text(_traefik_line(when=when) + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        hourly = session.query(test_db.AccessLogHourly).first()

    assert hourly is not None
    assert hourly.sistema == "app2.dlsistemas.com.br"
    assert hourly.hour == when.strftime("%Y-%m-%d %H")
    assert hourly.count == 1
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -3 -m pytest tests/test_access_log_tailer.py -v -k hourly`
Expected: FAIL — `session.query(test_db.AccessLogHourly)` não existe ainda ou `hourly is None` (a asserção `assert hourly is not None` falha)

- [ ] **Step 3: Implementar `_upsert_hourly` e chamá-lo em `_process_line`**

Em `backend/collector/access_log_tailer.py`, logo depois de `_upsert_daily`:

```python
def _upsert_hourly(session: Session, hour: str, sistema: str) -> None:
    row = (
        session.query(db_module.AccessLogHourly)
        .filter_by(hour=hour, sistema=sistema)
        .first()
    )
    if row:
        row.count += 1
    else:
        session.add(db_module.AccessLogHourly(hour=hour, sistema=sistema, count=1))
```

E em `_process_line`, mudar a última linha:

```python
    _upsert_daily(session, accessed_at.strftime("%Y-%m-%d"), ip, sistema)
```

para:

```python
    _upsert_daily(session, accessed_at.strftime("%Y-%m-%d"), ip, sistema)
    _upsert_hourly(session, accessed_at.strftime("%Y-%m-%d %H"), sistema)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -3 -m pytest tests/test_access_log_tailer.py -v`
Expected: todos PASS

- [ ] **Step 5: Commit**

```bash
git add backend/collector/access_log_tailer.py backend/tests/test_access_log_tailer.py
git commit -m "feat: coletor grava contagem de acessos por hora em access_log_hourly"
```

---

### Task 3: Retenção de `AccessLogHourly`

**Files:**
- Modify: `backend/collector/scheduler.py`
- Test: `backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `AccessLogHourly` (Tarefa 1), `_cleanup()` já existente.

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao final de `backend/tests/test_scheduler.py`:

```python
@pytest.mark.asyncio
async def test_cleanup_remove_access_log_hourly_antigo(test_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    from datetime import datetime, timedelta
    from sqlalchemy.orm import Session

    old_hour = (datetime.utcnow() - timedelta(days=10)).strftime("%Y-%m-%d %H")
    recent_hour = datetime.utcnow().strftime("%Y-%m-%d %H")
    with Session(test_db.engine) as session:
        session.add(test_db.AccessLogHourly(hour=old_hour, sistema="app2.dlsistemas.com.br", count=3))
        session.add(test_db.AccessLogHourly(hour=recent_hour, sistema="app2.dlsistemas.com.br", count=1))
        session.commit()

    import importlib
    import collector.scheduler as sched
    importlib.reload(sched)
    await sched._cleanup()

    with Session(test_db.engine) as session:
        rows = session.query(test_db.AccessLogHourly).all()
    assert len(rows) == 1
    assert rows[0].hour == recent_hour
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -3 -m pytest tests/test_scheduler.py -v -k access_log_hourly`
Expected: FAIL — os dois registros continuam lá (`len(rows) == 2`)

- [ ] **Step 3: Implementar a limpeza**

Em `backend/collector/scheduler.py`, mudar o import:

```python
from models.database import AccessLog, AccessLogDaily, ContainerDiskUsage, ContainerMetrics, MetricsHistory, engine
```

para:

```python
from models.database import AccessLog, AccessLogDaily, AccessLogHourly, ContainerDiskUsage, ContainerMetrics, MetricsHistory, engine
```

E em `_cleanup()`, logo depois de:

```python
        session.query(AccessLog).filter(AccessLog.accessed_at < detailed_cutoff).delete()
```

adicionar:

```python
        session.query(AccessLogHourly).filter(AccessLogHourly.hour < detailed_cutoff.strftime("%Y-%m-%d %H")).delete()
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -3 -m pytest tests/test_scheduler.py -v`
Expected: todos PASS

- [ ] **Step 5: Commit**

```bash
git add backend/collector/scheduler.py backend/tests/test_scheduler.py
git commit -m "feat: retencao detalhada tambem limpa access_log_hourly"
```

---

### Task 4: Endpoint `GET /api/access-logs/summary-por-sistema`

**Files:**
- Modify: `backend/api/access_logs.py`
- Test: `backend/tests/test_access_logs_api.py`

**Interfaces:**
- Consumes: `AccessLogDaily` (existente), `_cutoff_day(days)` (existente).
- Produces: rota `GET /api/access-logs/summary-por-sistema?ip=&days=` retornando `list[{"sistema": str, "total_acessos": int, "ips": [{"ip": str, "count": int, "ultimo_acesso": str}]}]` — consumida pela Tarefa 9 (frontend).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `backend/tests/test_access_logs_api.py`:

```python
def test_summary_por_sistema_agrega_por_sistema_e_ip(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "circuitodascorridas.dlsistemas.com.br", 5)
    _seed_daily(db, today, "198.51.100.20", "circuitodascorridas.dlsistemas.com.br", 3)
    _seed_daily(db, today, "203.0.113.10", "monitor.dlsistemas.com.br", 2)

    r = client.get("/api/access-logs/summary-por-sistema?days=7")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    top = data[0]
    assert top["sistema"] == "circuitodascorridas.dlsistemas.com.br"
    assert top["total_acessos"] == 8
    assert top["ips"][0]["ip"] == "203.0.113.10"
    assert top["ips"][0]["count"] == 5


def test_summary_por_sistema_filtra_por_ip_prefixo(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "circuitodascorridas.dlsistemas.com.br", 5)
    _seed_daily(db, today, "198.51.100.20", "circuitodascorridas.dlsistemas.com.br", 3)

    r = client.get("/api/access-logs/summary-por-sistema?days=7&ip=203.0.113")
    data = r.json()
    assert len(data) == 1
    assert data[0]["total_acessos"] == 5
    assert len(data[0]["ips"]) == 1
    assert data[0]["ips"][0]["ip"] == "203.0.113.10"


def test_summary_por_sistema_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/access-logs/summary-por-sistema").status_code == 401
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -3 -m pytest tests/test_access_logs_api.py -v -k summary_por_sistema`
Expected: FAIL com 404 (rota não existe)

- [ ] **Step 3: Implementar o endpoint**

Em `backend/api/access_logs.py`, logo depois da função `summary` (antes de `sistemas`):

```python
@router.get("/summary-por-sistema")
def summary_por_sistema(
    ip: Optional[str] = None,
    days: int = Query(30),
    session: Session = Depends(get_session),
):
    cutoff = _cutoff_day(days)
    q = session.query(AccessLogDaily).filter(AccessLogDaily.day >= cutoff)
    if ip:
        q = q.filter(AccessLogDaily.ip.like(f"{ip}%"))
    rows = q.all()

    by_sistema: dict[str, dict] = {}
    for r in rows:
        entry = by_sistema.setdefault(r.sistema, {"sistema": r.sistema, "total_acessos": 0, "ips": {}})
        entry["total_acessos"] += r.count
        ip_entry = entry["ips"].setdefault(r.ip, {"ip": r.ip, "count": 0, "ultimo_acesso": r.day})
        ip_entry["count"] += r.count
        if r.day > ip_entry["ultimo_acesso"]:
            ip_entry["ultimo_acesso"] = r.day

    result = [
        {
            "sistema": v["sistema"],
            "total_acessos": v["total_acessos"],
            "ips": sorted(v["ips"].values(), key=lambda x: -x["count"]),
        }
        for v in by_sistema.values()
    ]
    result.sort(key=lambda x: -x["total_acessos"])
    return result
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -3 -m pytest tests/test_access_logs_api.py -v`
Expected: todos PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/access_logs.py backend/tests/test_access_logs_api.py
git commit -m "feat: endpoint summary-por-sistema agrega acessos por sistema e ip"
```

---

### Task 5: Endpoint `GET /api/access-logs/container-para-sistema`

**Files:**
- Modify: `backend/api/access_logs.py`
- Test: `backend/tests/test_access_logs_api.py`

**Interfaces:**
- Consumes: `docker_client.list_containers() -> list[dict]` (já existe em `collector/docker_client.py`, retorna JSON bruto do Docker com campo `Labels: dict[str, str]` e `Names: list[str]`).
- Produces: rota `GET /api/access-logs/container-para-sistema?sistema=` retornando `{"container_name": str | None}` — consumida pela Tarefa 10 (frontend).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `backend/tests/test_access_logs_api.py`:

```python
def test_container_para_sistema_acha_pelo_host_label(auth_client):
    client, _ = auth_client
    fake_containers = [
        {
            "Id": "abc123def456",
            "Names": ["/circuitodascorridas-app"],
            "Labels": {
                "traefik.enable": "true",
                "traefik.http.routers.circuitodascorridas.rule": "Host(`circuitodascorridas.dlsistemas.com.br`)",
            },
        },
        {"Id": "def456abc123", "Names": ["/outro"], "Labels": {}},
    ]
    with patch("api.access_logs.docker_client") as mock_dc:
        mock_dc.list_containers = AsyncMock(return_value=fake_containers)
        r = client.get("/api/access-logs/container-para-sistema?sistema=circuitodascorridas.dlsistemas.com.br")
    assert r.status_code == 200
    assert r.json() == {"container_name": "circuitodascorridas-app"}


def test_container_para_sistema_multiplos_hosts_na_mesma_regra(auth_client):
    client, _ = auth_client
    fake_containers = [
        {
            "Id": "abc123def456",
            "Names": ["/app-multi"],
            "Labels": {
                "traefik.http.routers.multi.rule": "Host(`a.dlsistemas.com.br`) || Host(`b.dlsistemas.com.br`)",
            },
        },
    ]
    with patch("api.access_logs.docker_client") as mock_dc:
        mock_dc.list_containers = AsyncMock(return_value=fake_containers)
        r = client.get("/api/access-logs/container-para-sistema?sistema=b.dlsistemas.com.br")
    assert r.json() == {"container_name": "app-multi"}


def test_container_para_sistema_nao_encontrado_retorna_null(auth_client):
    client, _ = auth_client
    with patch("api.access_logs.docker_client") as mock_dc:
        mock_dc.list_containers = AsyncMock(return_value=[])
        r = client.get("/api/access-logs/container-para-sistema?sistema=inexistente.com.br")
    assert r.json() == {"container_name": None}


def test_container_para_sistema_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/access-logs/container-para-sistema?sistema=x.com").status_code == 401
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -3 -m pytest tests/test_access_logs_api.py -v -k container_para_sistema`
Expected: FAIL com 404 (rota não existe) ou `AttributeError` (não há `api.access_logs.docker_client` pra dar patch)

- [ ] **Step 3: Implementar o endpoint**

Em `backend/api/access_logs.py`, no topo do arquivo, mudar os imports de:

```python
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.auth import verify_token_header
from collector.geoip import lookup_ip
from models.database import AccessLog, AccessLogDaily, get_session
```

para:

```python
import re
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.auth import verify_token_header
from collector.geoip import lookup_ip
from collector.scheduler import docker_client
from models.database import AccessLog, AccessLogDaily, get_session
```

Depois, logo após a função `sistemas()`, adicionar:

```python
_TRAEFIK_RULE_LABEL_RE = re.compile(r"^traefik\.http\.routers\.[^.]+\.rule$")
_HOST_RE = re.compile(r"Host\(`([^`]+)`\)")


@router.get("/container-para-sistema")
async def container_para_sistema(sistema: str):
    containers = await docker_client.list_containers()
    for container in containers:
        labels = container.get("Labels") or {}
        for key, rule in labels.items():
            if not _TRAEFIK_RULE_LABEL_RE.match(key):
                continue
            if sistema in _HOST_RE.findall(rule):
                names = container.get("Names") or []
                name = names[0].lstrip("/") if names else container["Id"][:12]
                return {"container_name": name}
    return {"container_name": None}
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -3 -m pytest tests/test_access_logs_api.py -v`
Expected: todos PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/access_logs.py backend/tests/test_access_logs_api.py
git commit -m "feat: endpoint container-para-sistema mapeia dominio para container via labels do Traefik"
```

---

### Task 6: Helper de bucketing de tempo (`api/time_buckets.py`)

**Files:**
- Create: `backend/api/time_buckets.py`
- Test: `backend/tests/test_time_buckets.py`

**Interfaces:**
- Produces:
  - `hourly_buckets(day: Optional[str] = None) -> list[datetime]` — sem `day`: últimas 12h corridas (âncora na hora cheia atual); com `day` ("YYYY-MM-DD"): 00h–23h daquele dia (só até a hora atual se for hoje).
  - `daily_buckets(month: Optional[str] = None) -> list[str]` — sem `month`: mês atual só até hoje; com `month` ("YYYY-MM"): todos os dias daquele mês (só até hoje se for o mês atual).
  - Consumidas pelas Tarefas 7 e 8.

- [ ] **Step 1: Escrever os testes que falham**

Criar `backend/tests/test_time_buckets.py`:

```python
from datetime import datetime, timedelta

from api.time_buckets import daily_buckets, hourly_buckets


def test_hourly_buckets_sem_dia_retorna_ultimas_12h():
    buckets = hourly_buckets()
    assert len(buckets) == 12
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    assert buckets[-1] == now
    assert buckets[0] == now - timedelta(hours=11)


def test_hourly_buckets_dia_passado_retorna_24h():
    dia = (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d")
    buckets = hourly_buckets(dia)
    assert len(buckets) == 24
    assert buckets[0].strftime("%Y-%m-%d %H") == f"{dia} 00"
    assert buckets[-1].strftime("%Y-%m-%d %H") == f"{dia} 23"


def test_hourly_buckets_dia_de_hoje_vai_so_ate_hora_atual():
    hoje = datetime.utcnow().strftime("%Y-%m-%d")
    hora_atual = datetime.utcnow().hour
    buckets = hourly_buckets(hoje)
    assert len(buckets) == hora_atual + 1
    assert buckets[-1].hour == hora_atual


def test_daily_buckets_mes_passado_retorna_todos_os_dias():
    now = datetime.utcnow()
    ultimo_dia_mes_anterior = now.replace(day=1) - timedelta(days=1)
    mes_anterior = ultimo_dia_mes_anterior.strftime("%Y-%m")
    buckets = daily_buckets(mes_anterior)
    assert len(buckets) == ultimo_dia_mes_anterior.day
    assert buckets[0] == f"{mes_anterior}-01"
    assert buckets[-1] == ultimo_dia_mes_anterior.strftime("%Y-%m-%d")


def test_daily_buckets_mes_atual_vai_so_ate_hoje():
    now = datetime.utcnow()
    buckets = daily_buckets(now.strftime("%Y-%m"))
    assert len(buckets) == now.day
    assert buckets[-1] == now.strftime("%Y-%m-%d")


def test_daily_buckets_sem_mes_usa_mes_atual():
    assert daily_buckets() == daily_buckets(datetime.utcnow().strftime("%Y-%m"))
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -3 -m pytest tests/test_time_buckets.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'api.time_buckets'`

- [ ] **Step 3: Implementar o helper**

Criar `backend/api/time_buckets.py`:

```python
import calendar
from datetime import datetime, timedelta
from typing import Optional


def hourly_buckets(day: Optional[str] = None) -> list[datetime]:
    if day:
        start = datetime.strptime(day, "%Y-%m-%d")
        now = datetime.utcnow()
        last_hour = now.hour if start.date() == now.date() else 23
        return [start.replace(hour=h) for h in range(last_hour + 1)]
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    return [now - timedelta(hours=h) for h in range(11, -1, -1)]


def daily_buckets(month: Optional[str] = None) -> list[str]:
    now = datetime.utcnow()
    month = month or now.strftime("%Y-%m")
    year, mon = int(month[:4]), int(month[5:7])
    _, days_in_month = calendar.monthrange(year, mon)
    is_current_month = (year, mon) == (now.year, now.month)
    last_day = now.day if is_current_month else days_in_month
    return [f"{month}-{d:02d}" for d in range(1, last_day + 1)]
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -3 -m pytest tests/test_time_buckets.py -v`
Expected: todos PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/time_buckets.py backend/tests/test_time_buckets.py
git commit -m "feat: helper de bucketing de tempo por hora e por dia do mes"
```

---

### Task 7: Endpoint `GET /api/access-logs/timeseries`

**Files:**
- Modify: `backend/api/access_logs.py`
- Test: `backend/tests/test_access_logs_api.py`

**Interfaces:**
- Consumes: `hourly_buckets`, `daily_buckets` (Tarefa 6); `AccessLogHourly` (Tarefa 1); `AccessLogDaily` (existente).
- Produces: rota `GET /api/access-logs/timeseries?sistema=&granularity=hour|day&day=&month=` retornando `{"granularity": str, "data": [{"ts": str, "value": int}]}` — consumida pela Tarefa 10 (frontend).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `backend/tests/test_access_logs_api.py`:

```python
def _seed_hourly(db, hour, sistema, count):
    with Session(db.engine) as session:
        session.add(db.AccessLogHourly(hour=hour, sistema=sistema, count=count))
        session.commit()


def test_timeseries_hour_ultimas_12h(auth_client):
    client, db = auth_client
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    _seed_hourly(db, now.strftime("%Y-%m-%d %H"), "app2.dlsistemas.com.br", 7)
    _seed_hourly(db, (now - timedelta(hours=1)).strftime("%Y-%m-%d %H"), "app2.dlsistemas.com.br", 3)

    r = client.get("/api/access-logs/timeseries?sistema=app2.dlsistemas.com.br&granularity=hour")
    assert r.status_code == 200
    data = r.json()
    assert data["granularity"] == "hour"
    assert len(data["data"]) == 12
    assert data["data"][-1]["value"] == 7
    assert data["data"][-2]["value"] == 3
    assert data["data"][0]["value"] == 0


def test_timeseries_hour_dia_especifico(auth_client):
    client, db = auth_client
    day_dt = datetime.utcnow() - timedelta(days=2)
    day = day_dt.strftime("%Y-%m-%d")
    _seed_hourly(db, f"{day} 09", "app2.dlsistemas.com.br", 4)
    _seed_hourly(db, f"{day} 15", "app2.dlsistemas.com.br", 6)

    r = client.get(f"/api/access-logs/timeseries?sistema=app2.dlsistemas.com.br&granularity=hour&day={day}")
    data = r.json()
    assert len(data["data"]) == 24
    assert data["data"][9]["value"] == 4
    assert data["data"][15]["value"] == 6
    assert data["data"][0]["value"] == 0


def test_timeseries_day_mes_passado_completo(auth_client):
    client, db = auth_client
    now = datetime.utcnow()
    ultimo_dia_mes_anterior = now.replace(day=1) - timedelta(days=1)
    mes_anterior = ultimo_dia_mes_anterior.strftime("%Y-%m")
    dias_no_mes = ultimo_dia_mes_anterior.day

    _seed_daily(db, f"{mes_anterior}-01", "203.0.113.10", "app2.dlsistemas.com.br", 10)
    _seed_daily(db, ultimo_dia_mes_anterior.strftime("%Y-%m-%d"), "203.0.113.10", "app2.dlsistemas.com.br", 20)

    r = client.get(f"/api/access-logs/timeseries?sistema=app2.dlsistemas.com.br&granularity=day&month={mes_anterior}")
    data = r.json()
    assert data["granularity"] == "day"
    assert len(data["data"]) == dias_no_mes
    primeiro = next(d for d in data["data"] if d["ts"] == f"{mes_anterior}-01")
    assert primeiro["value"] == 10
    ultimo = next(d for d in data["data"] if d["ts"] == ultimo_dia_mes_anterior.strftime("%Y-%m-%d"))
    assert ultimo["value"] == 20


def test_timeseries_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/access-logs/timeseries?sistema=x.com").status_code == 401
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -3 -m pytest tests/test_access_logs_api.py -v -k timeseries`
Expected: FAIL com 404 (rota não existe)

- [ ] **Step 3: Implementar o endpoint**

Em `backend/api/access_logs.py`, atualizar o import de `models.database` para incluir `AccessLogHourly`:

```python
from models.database import AccessLog, AccessLogDaily, AccessLogHourly, get_session
```

E adicionar o import do helper de buckets, logo abaixo dos imports existentes:

```python
from api.time_buckets import daily_buckets, hourly_buckets
```

Depois, ao final do arquivo, adicionar:

```python
@router.get("/timeseries")
def timeseries(
    sistema: str,
    granularity: str = Query("hour"),
    day: Optional[str] = None,
    month: Optional[str] = None,
    session: Session = Depends(get_session),
):
    if granularity == "day":
        buckets = daily_buckets(month)
        rows = (
            session.query(AccessLogDaily)
            .filter(AccessLogDaily.sistema == sistema, AccessLogDaily.day.in_(buckets))
            .all()
        )
        totals: dict[str, int] = {}
        for r in rows:
            totals[r.day] = totals.get(r.day, 0) + r.count
        return {
            "granularity": "day",
            "data": [{"ts": b, "value": totals.get(b, 0)} for b in buckets],
        }

    hours = hourly_buckets(day)
    keys = [h.strftime("%Y-%m-%d %H") for h in hours]
    rows = (
        session.query(AccessLogHourly)
        .filter(AccessLogHourly.sistema == sistema, AccessLogHourly.hour.in_(keys))
        .all()
    )
    totals = {r.hour: r.count for r in rows}
    return {
        "granularity": "hour",
        "data": [
            {"ts": h.strftime("%Y-%m-%dT%H:00:00") + "Z", "value": totals.get(key, 0)}
            for h, key in zip(hours, keys)
        ],
    }
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -3 -m pytest tests/test_access_logs_api.py -v`
Expected: todos PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/access_logs.py backend/tests/test_access_logs_api.py
git commit -m "feat: endpoint timeseries retorna serie de acessos por hora ou por dia do mes"
```

---

### Task 8: Endpoint `GET /api/metrics/container-history`

**Files:**
- Modify: `backend/api/metrics.py`
- Test: `backend/tests/test_metrics_api.py`

**Interfaces:**
- Consumes: `hourly_buckets`, `daily_buckets` (Tarefa 6); `ContainerMetrics` (existente).
- Produces: rota `GET /api/metrics/container-history?container_name=&granularity=hour|day&day=&month=` retornando `{"granularity": str, "data": [{"ts": str, "cpu_percent": float|None, "mem_percent": float|None, "net_rx_mb": float|None, "net_tx_mb": float|None}]}` — consumida pela Tarefa 10 (frontend).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `backend/tests/test_metrics_api.py`:

```python
def test_container_history_hour_agrega_media_por_bucket(auth_client):
    client, db = auth_client
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    with Session(db.engine) as session:
        session.add(db.ContainerMetrics(
            collected_at=now, container_id="abc", container_name="circuitodascorridas-app",
            cpu_percent=10.0, mem_percent=40.0, net_rx_mb=1.0, net_tx_mb=0.5,
        ))
        session.add(db.ContainerMetrics(
            collected_at=now + timedelta(minutes=10), container_id="abc", container_name="circuitodascorridas-app",
            cpu_percent=20.0, mem_percent=50.0, net_rx_mb=2.0, net_tx_mb=1.5,
        ))
        session.commit()

    r = client.get("/api/metrics/container-history?container_name=circuitodascorridas-app&granularity=hour")
    assert r.status_code == 200
    data = r.json()
    assert data["granularity"] == "hour"
    last_bucket = data["data"][-1]
    assert last_bucket["cpu_percent"] == 15.0
    assert last_bucket["mem_percent"] == 45.0


def test_container_history_bucket_sem_amostra_retorna_null(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/container-history?container_name=inexistente&granularity=hour")
    data = r.json()
    assert all(p["cpu_percent"] is None for p in data["data"])


def test_container_history_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/metrics/container-history?container_name=x").status_code == 401
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -3 -m pytest tests/test_metrics_api.py -v -k container_history`
Expected: FAIL com 404 (rota não existe)

- [ ] **Step 3: Implementar o endpoint**

Em `backend/api/metrics.py`, atualizar os imports do topo de:

```python
from datetime import datetime, timedelta
from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from models.database import MetricsHistory, get_session
from api.auth import verify_token_header
from collector.scheduler import get_last_metrics
```

para:

```python
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from api.time_buckets import daily_buckets, hourly_buckets
from models.database import ContainerMetrics, MetricsHistory, get_session
from api.auth import verify_token_header
from collector.scheduler import get_last_metrics
```

Depois, ao final do arquivo, adicionar:

```python
def _avg(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _bucket_point(ts: str, rows: list) -> dict:
    return {
        "ts": ts,
        "cpu_percent": _avg([r.cpu_percent for r in rows]),
        "mem_percent": _avg([r.mem_percent for r in rows]),
        "net_rx_mb": _avg([r.net_rx_mb for r in rows]),
        "net_tx_mb": _avg([r.net_tx_mb for r in rows]),
    }


@metrics_router.get("/metrics/container-history")
def container_history(
    container_name: str,
    granularity: str = Query("hour"),
    day: Optional[str] = None,
    month: Optional[str] = None,
    auth=Depends(verify_token_header),
    session: Session = Depends(get_session),
):
    if granularity == "day":
        buckets = daily_buckets(month)
        start = datetime.strptime(buckets[0], "%Y-%m-%d")
        end = datetime.strptime(buckets[-1], "%Y-%m-%d") + timedelta(days=1)
        rows = (
            session.query(ContainerMetrics)
            .filter(
                ContainerMetrics.container_name == container_name,
                ContainerMetrics.collected_at >= start,
                ContainerMetrics.collected_at < end,
            )
            .all()
        )
        by_bucket: dict[str, list] = {b: [] for b in buckets}
        for r in rows:
            by_bucket[r.collected_at.strftime("%Y-%m-%d")].append(r)
        return {
            "granularity": "day",
            "data": [_bucket_point(b, by_bucket[b]) for b in buckets],
        }

    hours = hourly_buckets(day)
    keys = [h.strftime("%Y-%m-%d %H") for h in hours]
    start = hours[0]
    end = hours[-1] + timedelta(hours=1)
    rows = (
        session.query(ContainerMetrics)
        .filter(
            ContainerMetrics.container_name == container_name,
            ContainerMetrics.collected_at >= start,
            ContainerMetrics.collected_at < end,
        )
        .all()
    )
    by_bucket = {k: [] for k in keys}
    for r in rows:
        by_bucket[r.collected_at.strftime("%Y-%m-%d %H")].append(r)
    return {
        "granularity": "hour",
        "data": [
            _bucket_point(h.strftime("%Y-%m-%dT%H:00:00") + "Z", by_bucket[key])
            for h, key in zip(hours, keys)
        ],
    }
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -3 -m pytest tests/test_metrics_api.py -v`
Expected: todos PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/metrics.py backend/tests/test_metrics_api.py
git commit -m "feat: endpoint container-history retorna media de cpu/ram/rede por bucket"
```

---

### Task 9: Frontend — tabela de Acessos agrupada por sistema

**Files:**
- Modify: `frontend/app/acessos/page.tsx`

**Interfaces:**
- Consumes: `GET /api/access-logs/summary-por-sistema?ip=&days=` (Tarefa 4); `AccessIpModal` (existente, sem mudanças).
- Produces: nenhuma interface nova consumida por outra tarefa (a Tarefa 10 é um componente à parte, montado dentro desta página).

Não há framework de teste automatizado no frontend deste repositório — a verificação é manual, rodando o dev server.

- [ ] **Step 1: Reescrever a página**

Substituir todo o conteúdo de `frontend/app/acessos/page.tsx` por:

```tsx
'use client';
import React, { useState, useEffect, useCallback } from 'react';
import api from '../../lib/api';
import AccessIpModal from '../../components/AccessIpModal';
import AccessProjectCharts from '../../components/AccessProjectCharts';

type Range = '24h' | '7d' | '30d';

interface IpCount { ip: string; count: number; ultimo_acesso: string; }
interface SistemaSummaryRow { sistema: string; total_acessos: number; ips: IpCount[]; }

const RANGES: { value: Range; label: string; days: number }[] = [
  { value: '24h', label: '24 horas', days: 1 },
  { value: '7d', label: '7 dias', days: 7 },
  { value: '30d', label: '30 dias', days: 30 },
];

function fmtRelativeDay(day: string): string {
  const todayStr = new Date().toISOString().slice(0, 10);
  const diffDays = Math.round((Date.parse(todayStr) - Date.parse(day)) / 86400000);
  if (diffDays === 0) return 'hoje';
  if (diffDays === 1) return 'ontem';
  if (diffDays < 0) return day;
  return `há ${diffDays} dias`;
}

export default function AcessosPage() {
  const [range, setRange] = useState<Range>('7d');
  const [ipFiltro, setIpFiltro] = useState('');
  const [rows, setRows] = useState<SistemaSummaryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandidos, setExpandidos] = useState<Set<string>>(new Set());
  const [ipSelecionado, setIpSelecionado] = useState<string | null>(null);

  const days = RANGES.find(r => r.value === range)!.days;

  const loadSummary = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number> = { days };
      if (ipFiltro) params.ip = ipFiltro;
      const r = await api.get('/access-logs/summary-por-sistema', { params });
      setRows(r.data ?? []);
    } catch { setRows([]); }
    finally { setLoading(false); }
  }, [days, ipFiltro]);

  useEffect(() => {
    const t = setTimeout(loadSummary, 300);
    return () => clearTimeout(t);
  }, [loadSummary]);

  const toggleExpandido = (sistema: string) => {
    setExpandidos(prev => {
      const next = new Set(prev);
      if (next.has(sistema)) next.delete(sistema); else next.add(sistema);
      return next;
    });
  };

  const tabBtn = (active: boolean): React.CSSProperties => ({
    padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#000' : 'var(--muted)',
    fontWeight: active ? 700 : 400,
    cursor: 'pointer', fontSize: 12,
  });

  return (
    <div>
      <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 24 }}>Acessos</h1>

      <div style={{ display: 'flex', gap: 20, marginBottom: 20, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Período</div>
          <div style={{ display: 'flex', gap: 6 }}>
            {RANGES.map(r => (
              <button key={r.value} onClick={() => setRange(r.value)} style={tabBtn(range === r.value)}>
                {r.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Filtrar por IP</div>
          <input
            placeholder="ex: 203.0.113"
            value={ipFiltro}
            onChange={(e) => setIpFiltro(e.target.value)}
            style={{
              padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
              background: 'var(--surface)', color: 'var(--text)', fontSize: 12, width: 160,
            }}
          />
        </div>
      </div>

      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden', marginBottom: 32 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
              {['Sistema', 'Total de acessos', ''].map((h) => (
                <th key={h} style={{
                  padding: '10px 16px', textAlign: 'left', fontSize: 11,
                  color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={3} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>
                  {loading ? 'Carregando...' : 'Nenhum acesso registrado no período.'}
                </td>
              </tr>
            ) : (
              rows.map((row) => {
                const aberto = expandidos.has(row.sistema);
                return (
                  <React.Fragment key={row.sistema}>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '10px 16px', fontFamily: 'monospace', fontSize: 13 }}>{row.sistema}</td>
                      <td style={{ padding: '10px 16px' }}>{row.total_acessos}</td>
                      <td style={{ padding: '10px 16px', textAlign: 'right' }}>
                        <button
                          onClick={() => toggleExpandido(row.sistema)}
                          style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: 16 }}
                        >
                          {aberto ? '▾' : '▸'}
                        </button>
                      </td>
                    </tr>
                    {aberto && (
                      <tr>
                        <td colSpan={3} style={{ padding: '0 16px 16px', background: 'var(--surface)' }}>
                          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8 }}>
                            <thead>
                              <tr>
                                {['IP', 'Acessos', 'Último acesso'].map(h => (
                                  <th key={h} style={{ padding: '6px 10px', textAlign: 'left', fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase' }}>{h}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {row.ips.map(ipRow => (
                                <tr key={ipRow.ip} style={{ borderTop: '1px solid var(--border)' }}>
                                  <td style={{ padding: '6px 10px' }}>
                                    <button
                                      onClick={() => setIpSelecionado(ipRow.ip)}
                                      style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontFamily: 'monospace', fontSize: 13, padding: 0 }}
                                    >
                                      {ipRow.ip}
                                    </button>
                                  </td>
                                  <td style={{ padding: '6px 10px' }}>{ipRow.count}</td>
                                  <td style={{ padding: '6px 10px', color: 'var(--muted)', fontSize: 12 }}>{fmtRelativeDay(ipRow.ultimo_acesso)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <AccessProjectCharts />

      {ipSelecionado && (
        <AccessIpModal ip={ipSelecionado} days={days} onClose={() => setIpSelecionado(null)} />
      )}
    </div>
  );
}
```

Nota: `AccessProjectCharts` ainda não existe (Tarefa 10) — a página vai falhar o build até lá. Se preferir, comente a linha `<AccessProjectCharts />` e o import nesta tarefa e reative na Tarefa 10; mas como as duas tarefas costumam ser feitas em sequência imediata, deixar assim já direciona a próxima tarefa.

- [ ] **Step 2: Commit**

```bash
git add frontend/app/acessos/page.tsx
git commit -m "feat: tabela de acessos agrupa por sistema com toggle de ips"
```

(A verificação visual completa — incluindo o componente da Tarefa 10 — acontece no Step de verificação da Tarefa 10, já que esta página só compila com os dois arquivos juntos.)

---

### Task 10: Frontend — componente `AccessProjectCharts` (gráficos de acessos e recursos)

**Files:**
- Create: `frontend/components/AccessProjectCharts.tsx`

**Interfaces:**
- Consumes: `GET /api/access-logs/sistemas` (existente), `GET /api/access-logs/timeseries` (Tarefa 7), `GET /api/access-logs/container-para-sistema` (Tarefa 5), `GET /api/metrics/container-history` (Tarefa 8), `LineChart` (existente, `frontend/components/LineChart.tsx`, props `data: {ts, value}[]`, `color?`, `unit?`, `label?`, `height?`).
- Produces: componente `AccessProjectCharts` (sem props), montado em `frontend/app/acessos/page.tsx` (Tarefa 9).

- [ ] **Step 1: Criar o componente**

Criar `frontend/components/AccessProjectCharts.tsx`:

```tsx
'use client';
import { useState, useEffect, useCallback } from 'react';
import LineChart from './LineChart';
import api from '../lib/api';

type Periodo = 'dia' | 'mes';
type MetricaRecurso = 'cpu' | 'ram' | 'net_rx' | 'net_tx';

interface Ponto { ts: string; value: number | null; }
interface PontoRecurso {
  ts: string;
  cpu_percent: number | null;
  mem_percent: number | null;
  net_rx_mb: number | null;
  net_tx_mb: number | null;
}

const METRICAS_RECURSO: { value: MetricaRecurso; label: string; unit: string; color: string; campo: keyof Omit<PontoRecurso, 'ts'> }[] = [
  { value: 'cpu',    label: 'CPU',     unit: '%',  color: 'var(--accent)',  campo: 'cpu_percent' },
  { value: 'ram',    label: 'RAM',     unit: '%',  color: 'var(--info)',    campo: 'mem_percent' },
  { value: 'net_rx', label: 'Rede ↓', unit: ' MB', color: 'var(--success)', campo: 'net_rx_mb' },
  { value: 'net_tx', label: 'Rede ↑', unit: ' MB', color: '#a78bfa',        campo: 'net_tx_mb' },
];

function hojeISO(): string {
  return new Date().toISOString().slice(0, 10);
}
function mesAtualISO(): string {
  return new Date().toISOString().slice(0, 7);
}

export default function AccessProjectCharts() {
  const [periodo, setPeriodo] = useState<Periodo>('dia');
  const [diaEspecifico, setDiaEspecifico] = useState('');
  const [mes, setMes] = useState(mesAtualISO());
  const [sistemas, setSistemas] = useState<string[]>([]);
  const [projeto, setProjeto] = useState('');
  const [acessos, setAcessos] = useState<Ponto[]>([]);
  const [containerName, setContainerName] = useState<string | null>(null);
  const [recursoMetrica, setRecursoMetrica] = useState<MetricaRecurso>('cpu');
  const [recursos, setRecursos] = useState<PontoRecurso[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.get('/access-logs/sistemas').then(r => {
      const lista: string[] = r.data ?? [];
      setSistemas(lista);
      setProjeto(prev => prev || lista[0] || '');
    }).catch(() => setSistemas([]));
  }, []);

  const paramsPeriodo = useCallback((): Record<string, string> => {
    if (periodo === 'mes') return { granularity: 'day', month: mes };
    return diaEspecifico ? { granularity: 'hour', day: diaEspecifico } : { granularity: 'hour' };
  }, [periodo, mes, diaEspecifico]);

  const loadAcessos = useCallback(async () => {
    if (!projeto) { setAcessos([]); return; }
    setLoading(true);
    try {
      const r = await api.get('/access-logs/timeseries', { params: { sistema: projeto, ...paramsPeriodo() } });
      setAcessos(r.data.data ?? []);
    } catch { setAcessos([]); }
    finally { setLoading(false); }
  }, [projeto, paramsPeriodo]);

  const loadRecursos = useCallback(async () => {
    if (!projeto) { setContainerName(null); setRecursos([]); return; }
    try {
      const r = await api.get('/access-logs/container-para-sistema', { params: { sistema: projeto } });
      const nome = r.data?.container_name ?? null;
      setContainerName(nome);
      if (!nome) { setRecursos([]); return; }
      const rh = await api.get('/metrics/container-history', { params: { container_name: nome, ...paramsPeriodo() } });
      setRecursos(rh.data.data ?? []);
    } catch { setContainerName(null); setRecursos([]); }
  }, [projeto, paramsPeriodo]);

  useEffect(() => { loadAcessos(); }, [loadAcessos]);
  useEffect(() => { loadRecursos(); }, [loadRecursos]);

  const tabBtn = (active: boolean): React.CSSProperties => ({
    padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#000' : 'var(--muted)',
    fontWeight: active ? 700 : 400,
    cursor: 'pointer', fontSize: 12,
  });

  const metricBtn = (active: boolean, color: string): React.CSSProperties => ({
    padding: '5px 12px', borderRadius: 6, border: `1px solid ${active ? color : 'var(--border)'}`,
    background: active ? color + '22' : 'transparent',
    color: active ? color : 'var(--muted)',
    fontWeight: active ? 700 : 400,
    cursor: 'pointer', fontSize: 12,
  });

  const metricaAtual = METRICAS_RECURSO.find(m => m.value === recursoMetrica)!;
  const dadosRecurso: Ponto[] = recursos.map(p => ({ ts: p.ts, value: p[metricaAtual.campo] }));

  return (
    <div style={{ marginBottom: 32 }}>
      <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 16 }}>Acessos por projeto</h2>

      <div style={{ display: 'flex', gap: 20, marginBottom: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Período</div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={() => setPeriodo('dia')} style={tabBtn(periodo === 'dia')}>Dia</button>
            <button onClick={() => setPeriodo('mes')} style={tabBtn(periodo === 'mes')}>Mês</button>
          </div>
        </div>

        {periodo === 'dia' ? (
          <div>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Dia</div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <button onClick={() => setDiaEspecifico('')} style={tabBtn(!diaEspecifico)}>Últimas 12h</button>
              <input
                type="date"
                value={diaEspecifico}
                max={hojeISO()}
                onChange={(e) => setDiaEspecifico(e.target.value)}
                style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 12 }}
              />
            </div>
          </div>
        ) : (
          <div>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Mês</div>
            <input
              type="month"
              value={mes}
              max={mesAtualISO()}
              onChange={(e) => setMes(e.target.value)}
              style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 12 }}
            />
          </div>
        )}

        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Projeto</div>
          <select
            value={projeto}
            onChange={(e) => setProjeto(e.target.value)}
            style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 12 }}
          >
            {sistemas.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </div>

      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, marginBottom: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 16 }}>
          Acessos
          {loading && <span style={{ fontSize: 12, color: 'var(--muted)', marginLeft: 10, fontWeight: 400 }}>Carregando...</span>}
        </div>
        {acessos.length > 0 ? (
          <LineChart data={acessos} unit="" label="Acessos" height={240} />
        ) : (
          <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)' }}>
            {projeto ? 'Sem dados para o período selecionado' : 'Nenhum sistema disponível'}
          </div>
        )}
      </div>

      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24 }}>
        <div style={{ fontWeight: 600, marginBottom: 16 }}>Recursos utilizados</div>
        {containerName ? (
          <>
            <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
              {METRICAS_RECURSO.map(m => (
                <button key={m.value} onClick={() => setRecursoMetrica(m.value)} style={metricBtn(recursoMetrica === m.value, m.color)}>
                  {m.label}
                </button>
              ))}
            </div>
            {dadosRecurso.some(d => d.value !== null) ? (
              <LineChart data={dadosRecurso} color={metricaAtual.color} unit={metricaAtual.unit} height={240} />
            ) : (
              <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)' }}>
                Sem amostras de recurso para o período selecionado
              </div>
            )}
          </>
        ) : (
          <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)', textAlign: 'center' }}>
            Recursos não disponíveis para este projeto (nenhum container do Traefik encontrado para este domínio).
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verificar manualmente**

Não há framework de teste automatizado no frontend. Verificar rodando o dev server:

```bash
cd frontend && npm run dev
```

Abrir `/acessos` no navegador e confirmar:
1. A tabela lista sistemas (não IPs), com total de acessos e um toggle que expande a lista de IPs (contagem + último acesso).
2. Clicar num IP expandido abre o `AccessIpModal` normalmente.
3. A seção "Acessos por projeto" aparece abaixo, com abas Dia/Mês, seletor de dia específico ou mês, e seletor de projeto.
4. Trocar o projeto atualiza os dois gráficos (acessos e recursos).
5. Se o backend não tiver dados reais ainda (ambiente de dev sem Traefik configurado), os estados vazios ("Nenhum acesso registrado...", "Sem dados para o período selecionado", "Recursos não disponíveis...") devem aparecer sem quebrar a página.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/AccessProjectCharts.tsx
git commit -m "feat: componente de graficos de acessos e recursos por projeto"
```

---

## Verificação final

- [ ] Rodar a suíte completa do backend: `cd backend && py -3 -m pytest tests/ -v` — todos os testes (antigos e novos) devem passar.
- [ ] Rodar `cd frontend && npm run build` para garantir que o TypeScript compila sem erros com os dois arquivos novos/alterados.
