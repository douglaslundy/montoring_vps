# VPS Monitor — Fase 2: Motor de Alertas

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar o motor de avaliação de alertas, a API de gerenciamento e a página de alertas; adicionar endpoint `/api/health` para monitoramento externo (UptimeRobot).

**Architecture:** O `AlertEngine.evaluate()` substitui o stub existente em `backend/notifications/alert_engine.py`, roda a cada ciclo de 30s via scheduler, consulta regras ativas no DB, cria/resolve `AlertLog` e retorna alertas ativos para o broadcast WS. A API `backend/api/alerts.py` expõe CRUD de regras e listagem de alertas. O frontend adiciona a página `/alertas` com 3 abas.

**Tech Stack:** FastAPI, SQLAlchemy 2.x (sync), APScheduler (wired), Next.js 14, TypeScript, Recharts (para duração dos alertas).

## Global Constraints

- Python 3.11, FastAPI 0.115, SQLAlchemy 2.x sync, APScheduler 3.10
- Next.js 14 App Router, TypeScript, Recharts, axios — SEM Tailwind
- Todos componentes frontend: `'use client'`
- `tsc --noEmit` zero erros
- Auth: `verify_token_header` em todas as rotas `/api/*` exceto `/api/health`
- CSS: apenas variáveis `--bg`, `--surface`, `--card`, `--border`, `--text`, `--muted`, `--accent`, `--success`, `--danger`, `--warning`, `--info`
- Interface em português brasileiro
- Dark theme apenas
- `axios` com `baseURL='/api'` — **não repetir `/api/` nas chamadas**

## Estado Existente da Fase 1

- `backend/models/database.py`: modelos `AlertRule` e `AlertLog` já existem com todos os campos
- `backend/notifications/alert_engine.py`: stub `async def evaluate(metrics: dict, containers: list) -> list: return []`
- `backend/collector/scheduler.py` linha 63: `active_alerts = await evaluate(host, containers)` — já wired
- `backend/collector/scheduler.py` linha 74: `"active_alerts": active_alerts` — já no payload WS
- 9 regras padrão já inseridas no DB pelo `init_db()`
- `frontend/lib/ws.ts`: interface `ActiveAlert` já tem `id`, `severidade`, `metrica`, `mensagem`, `triggered_at`
- `frontend/app/alertas/`: existe como diretório com `.gitkeep`
- Sidebar em `frontend/app/layout.tsx`: link "Alertas" aponta para `/alertas` (confirmar se já existe)

## Campos do modelo AlertRule

```python
id, nome, metrica, operador, threshold, duracao_minutos,
severidade, canal_email, canal_whatsapp, cooldown_minutos, ativo, criado_em
```

## Campos do modelo AlertLog

```python
id, rule_id, triggered_at, resolved_at, severidade, metrica,
valor_no_disparo, threshold, mensagem, notificado_email,
notificado_whatsapp, erro_email, erro_whatsapp
```

---

### Task 1: Health Endpoint + UptimeRobot Docs

**Files:**
- Create: `backend/api/health.py`
- Modify: `backend/main.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `GET /api/health` → `{"status": "ok", "uptime_seconds": float, "version": "1.0.0"}` — sem autenticação

- [ ] **Step 1: Criar `backend/api/health.py`**

```python
import time
from fastapi import APIRouter

router = APIRouter()
_started_at = time.time()

@router.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _started_at, 1),
        "version": "1.0.0",
    }
```

- [ ] **Step 2: Registrar rota em `backend/main.py`**

Adicione logo após as outras importações de routers:
```python
from api.health import router as health_router
```
E no bloco de `app.include_router(...)`:
```python
app.include_router(health_router)
```
Note: esta rota NÃO usa `verify_token_header` — é pública por design.

- [ ] **Step 3: Testar manualmente**

```bash
# Com o servidor rodando:
curl http://localhost:8000/api/health
# Esperado: {"status":"ok","uptime_seconds":X.X,"version":"1.0.0"}
```

- [ ] **Step 4: Atualizar README.md**

Adicione uma seção "Monitoramento Externo (UptimeRobot)" após as instruções de deploy existentes:

```markdown
## Monitoramento Externo — Uptime (VPS caída)

O VPS Monitor detecta problemas **internos** (CPU alta, RAM cheia etc.), mas não pode
alertar sobre queda total da VPS porque roda nela mesma.

Para receber alertas quando a VPS ficar offline, configure um monitor externo gratuito:

### UptimeRobot (recomendado, gratuito)

1. Acesse [uptimerobot.com](https://uptimerobot.com) e crie uma conta
2. Clique em **+ Add New Monitor**
3. Preencha:
   - **Monitor Type:** HTTP(s)
   - **Friendly Name:** VPS Monitor — Health
   - **URL:** `https://monitor.dlsistemas.com.br/api/health`
   - **Monitoring Interval:** 1 minuto (padrão) ou configure conforme preferir
4. Em **Alert Contacts**, adicione seu e-mail ou webhook WhatsApp
5. Clique em **Create Monitor**

> O endpoint `/api/health` não requer autenticação e retorna `{"status":"ok"}` quando
> o sistema está operacional. Se a VPS cair, o UptimeRobot detecta em até 1 minuto
> e envia o alerta imediatamente.
```

- [ ] **Step 5: Commit**

```bash
git add backend/api/health.py backend/main.py README.md
git commit -m "feat: endpoint /api/health para monitoramento externo + docs UptimeRobot"
```

---

### Task 2: Alert Engine — Avaliador de Regras

**Files:**
- Modify: `backend/notifications/alert_engine.py` (substituir stub)
- Create: `backend/tests/test_alert_engine.py`

**Interfaces:**
- Consumes: `evaluate(metrics: dict, containers: list) -> list` — assinatura existente no scheduler
- `metrics` tem estrutura: `{"cpu": {"percent": float, "load": [f,f,f]}, "ram": {"percent": float}, "disk": {"percent": float}, "temperature_c": float | None}`
- `containers` é lista de dicts com `{"name": str, "status": str, "cpu_percent": float, ...}`
- Produces: lista de dicts `[{"id": int, "severidade": str, "metrica": str, "mensagem": str, "triggered_at": str}]`

**Lógica do motor:**

Métricas suportadas e como extrair do `metrics` dict:

| metrica | extração |
|---------|----------|
| `cpu_percent` | `metrics["cpu"]["percent"]` |
| `ram_percent` | `metrics["ram"]["percent"]` |
| `disk_percent` | `metrics["disk"]["percent"]` |
| `temperature_c` | `metrics.get("temperature_c")` — pode ser None |
| `load_1m` | `metrics["cpu"]["load"][0]` |
| `container_stopped` | especial — ver abaixo |

**Regra `container_stopped`:** Para cada container em `containers` com `status != "running"`, cria/mantém um AlertLog com `mensagem = f"Container '{name}' parado"`. Quando o container volta a running, resolve o AlertLog correspondente.

**Operadores:** `>`, `<`, `>=`, `<=`, `==`

**Ciclo de vida do alerta:**
- Condição VERDADEIRA + sem AlertLog aberto → cria AlertLog com `triggered_at=now`
- Condição VERDADEIRA + AlertLog aberto → nada (alerta já ativo)
- Condição FALSA + AlertLog aberto → resolve: `resolved_at=now`
- Retorna todos AlertLog com `resolved_at IS NULL`

Para `duracao_minutos`: criamos o AlertLog imediatamente, mas a flag `notificado_email`/`notificado_whatsapp` só é setada na Fase 3. O campo `duracao_minutos` está disponível para a Fase 3 calcular se deve notificar.

**Identificar AlertLog único:** O AlertLog de uma regra é identificado por `rule_id` (para regras normais) ou por `rule_id + mensagem` (para `container_stopped`, que pode ter múltiplas instâncias).

- [ ] **Step 1: Implementar `backend/notifications/alert_engine.py`**

```python
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from models.database import AlertLog, AlertRule, engine

logger = logging.getLogger(__name__)

_OPERATORS = {
    ">": lambda v, t: v > t,
    "<": lambda v, t: v < t,
    ">=": lambda v, t: v >= t,
    "<=": lambda v, t: v <= t,
    "==": lambda v, t: v == t,
}


def _get_metric_value(metrica: str, metrics: dict, containers: list):
    """Retorna o valor atual da métrica ou None se indisponível."""
    if metrica == "cpu_percent":
        return metrics.get("cpu", {}).get("percent")
    if metrica == "ram_percent":
        return metrics.get("ram", {}).get("percent")
    if metrica == "disk_percent":
        return metrics.get("disk", {}).get("percent")
    if metrica == "temperature_c":
        return metrics.get("temperature_c")
    if metrica == "load_1m":
        load = metrics.get("cpu", {}).get("load", [])
        return load[0] if load else None
    return None


def _evaluate_rule(session: Session, rule: AlertRule, value: float, mensagem: str, now: datetime):
    """Avalia uma regra simples (não container_stopped)."""
    op = _OPERATORS.get(rule.operador)
    if op is None or value is None:
        return

    condition_true = op(value, rule.threshold)

    # Busca AlertLog aberto para esta regra
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
        ))
    elif not condition_true and open_log is not None:
        open_log.resolved_at = now


def _evaluate_container_stopped(session: Session, rule: AlertRule, containers: list, now: datetime):
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
            ))

    # Resolve containers que voltaram a running
    running_names = {c["name"] for c in containers if c.get("status") == "running"}
    open_container_logs = (
        session.query(AlertLog)
        .filter(AlertLog.rule_id == rule.id, AlertLog.resolved_at.is_(None))
        .all()
    )
    for log in open_container_logs:
        container_name = log.mensagem.replace("Container '", "").replace("' parado", "")
        if container_name in running_names:
            log.resolved_at = now


async def evaluate(metrics: dict, containers: list) -> list:
    """Avalia todas as regras ativas e retorna lista de alertas ativos."""
    now = datetime.utcnow()
    try:
        with Session(engine) as session:
            rules = session.query(AlertRule).filter(AlertRule.ativo == 1).all()

            for rule in rules:
                try:
                    if rule.metrica == "container_stopped":
                        _evaluate_container_stopped(session, rule, containers, now)
                    else:
                        value = _get_metric_value(rule.metrica, metrics, containers)
                        if value is None:
                            continue
                        mensagem = f"{rule.nome}: {value:.1f} {rule.operador} {rule.threshold}"
                        _evaluate_rule(session, rule, value, mensagem, now)
                except Exception:
                    logger.exception("Erro avaliando regra %s", rule.nome)

            session.commit()

            # Retorna alertas ativos
            active = (
                session.query(AlertLog)
                .filter(AlertLog.resolved_at.is_(None))
                .order_by(AlertLog.triggered_at.desc())
                .limit(50)
                .all()
            )
            return [
                {
                    "id": a.id,
                    "severidade": a.severidade,
                    "metrica": a.metrica,
                    "mensagem": a.mensagem,
                    "triggered_at": a.triggered_at.isoformat() + "Z",
                }
                for a in active
            ]
    except Exception:
        logger.exception("Erro no motor de alertas")
        return []
```

- [ ] **Step 2: Criar `backend/tests/test_alert_engine.py`**

```python
import asyncio
from datetime import datetime
import pytest
from sqlalchemy.orm import Session
from models.database import AlertLog, AlertRule, engine, init_db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    import models.database as db_module
    test_engine = db_module.create_engine(f"sqlite:///{tmp_path}/test.db")
    db_module.Base.metadata.create_all(test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    import notifications.alert_engine as ae
    monkeypatch.setattr(ae, "engine", test_engine)
    return test_engine


def make_metrics(cpu=10.0, ram=50.0, disk=60.0, temp=40.0, load=0.5):
    return {
        "cpu": {"percent": cpu, "load": [load, load, load]},
        "ram": {"percent": ram},
        "disk": {"percent": disk},
        "temperature_c": temp,
    }


def add_rule(engine, **kwargs):
    defaults = dict(
        nome="Test", metrica="cpu_percent", operador=">",
        threshold=80.0, duracao_minutos=0, severidade="aviso",
        canal_email=0, canal_whatsapp=0, cooldown_minutos=30, ativo=1,
    )
    defaults.update(kwargs)
    with Session(engine) as s:
        rule = AlertRule(**defaults)
        s.add(rule)
        s.commit()
        return rule.id


def count_open(engine):
    with Session(engine) as s:
        return s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).count()


def test_creates_alert_when_threshold_exceeded(fresh_db):
    rule_id = add_rule(fresh_db, threshold=80.0, metrica="cpu_percent", operador=">")
    result = asyncio.run(
        __import__("notifications.alert_engine", fromlist=["evaluate"]).evaluate(
            make_metrics(cpu=90.0), []
        )
    )
    assert len(result) == 1
    assert result[0]["metrica"] == "cpu_percent"
    assert count_open(fresh_db) == 1


def test_no_alert_when_below_threshold(fresh_db):
    add_rule(fresh_db, threshold=80.0, metrica="cpu_percent", operador=">")
    result = asyncio.run(
        __import__("notifications.alert_engine", fromlist=["evaluate"]).evaluate(
            make_metrics(cpu=70.0), []
        )
    )
    assert result == []
    assert count_open(fresh_db) == 0


def test_resolves_alert_when_condition_clears(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=80.0, metrica="cpu_percent", operador=">")
    asyncio.run(evaluate(make_metrics(cpu=90.0), []))
    assert count_open(fresh_db) == 1
    asyncio.run(evaluate(make_metrics(cpu=70.0), []))
    assert count_open(fresh_db) == 0


def test_no_duplicate_alert(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=80.0, metrica="cpu_percent", operador=">")
    asyncio.run(evaluate(make_metrics(cpu=90.0), []))
    asyncio.run(evaluate(make_metrics(cpu=90.0), []))
    assert count_open(fresh_db) == 1


def test_container_stopped_creates_alert(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1)
    containers = [{"name": "nginx", "status": "exited"}]
    result = asyncio.run(evaluate(make_metrics(), containers))
    assert any("nginx" in r["mensagem"] for r in result)


def test_container_stopped_resolves_when_running(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1)
    asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "exited"}]))
    assert count_open(fresh_db) == 1
    asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "running"}]))
    assert count_open(fresh_db) == 0


def test_none_metric_does_not_crash(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="temperature_c", operador=">", threshold=75.0)
    metrics = make_metrics()
    metrics["temperature_c"] = None
    result = asyncio.run(evaluate(metrics, []))
    assert result == []
```

- [ ] **Step 3: Rodar testes**

```bash
cd backend && python -m pytest tests/test_alert_engine.py -v
```

Esperado: 7/7 PASSED

- [ ] **Step 4: Commit**

```bash
git add backend/notifications/alert_engine.py backend/tests/test_alert_engine.py
git commit -m "feat: alert engine — avaliador de regras com ciclo de vida create/resolve"
```

---

### Task 3: Alert API

**Files:**
- Create: `backend/api/alerts.py`
- Modify: `backend/main.py`
- Create: `backend/tests/test_alerts_api.py`

**Interfaces:**
- Consumes: `get_session` generator, `verify_token_header` dependency, modelos `AlertRule`, `AlertLog`
- Produces:
  - `GET /api/alerts/rules` → lista de regras
  - `POST /api/alerts/rules` → criar regra
  - `PUT /api/alerts/rules/{id}` → atualizar regra
  - `DELETE /api/alerts/rules/{id}` → deletar regra
  - `POST /api/alerts/rules/{id}/toggle` → ativar/desativar
  - `GET /api/alerts/active` → alertas não resolvidos
  - `GET /api/alerts/history?from_dt=&to_dt=&severidade=&metrica=&limit=100` → histórico

- [ ] **Step 1: Criar `backend/api/alerts.py`**

```python
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import verify_token_header
from models.database import AlertLog, AlertRule, get_session

router = APIRouter(prefix="/api/alerts", dependencies=[Depends(verify_token_header)])


class RuleIn(BaseModel):
    nome: str
    metrica: str
    operador: str
    threshold: float
    duracao_minutos: int = 5
    severidade: str
    canal_email: int = 1
    canal_whatsapp: int = 1
    cooldown_minutos: int = 30
    ativo: int = 1


@router.get("/rules")
def list_rules(session: Session = Depends(get_session)):
    rules = session.query(AlertRule).order_by(AlertRule.id).all()
    return [
        {
            "id": r.id, "nome": r.nome, "metrica": r.metrica,
            "operador": r.operador, "threshold": r.threshold,
            "duracao_minutos": r.duracao_minutos, "severidade": r.severidade,
            "canal_email": r.canal_email, "canal_whatsapp": r.canal_whatsapp,
            "cooldown_minutos": r.cooldown_minutos, "ativo": r.ativo,
            "criado_em": r.criado_em.isoformat() if r.criado_em else None,
        }
        for r in rules
    ]


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, session: Session = Depends(get_session)):
    rule = AlertRule(**body.model_dump())
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return {"id": rule.id}


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, body: RuleIn, session: Session = Depends(get_session)):
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada")
    for k, v in body.model_dump().items():
        setattr(rule, k, v)
    session.commit()
    return {"ok": True}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, session: Session = Depends(get_session)):
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada")
    session.delete(rule)
    session.commit()
    return {"ok": True}


@router.post("/rules/{rule_id}/toggle")
def toggle_rule(rule_id: int, session: Session = Depends(get_session)):
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada")
    rule.ativo = 0 if rule.ativo else 1
    session.commit()
    return {"ativo": rule.ativo}


@router.get("/active")
def active_alerts(session: Session = Depends(get_session)):
    logs = (
        session.query(AlertLog)
        .filter(AlertLog.resolved_at.is_(None))
        .order_by(AlertLog.triggered_at.desc())
        .all()
    )
    return [_log_dict(a) for a in logs]


@router.get("/history")
def alert_history(
    from_dt: Optional[str] = None,
    to_dt: Optional[str] = None,
    severidade: Optional[str] = None,
    metrica: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    q = session.query(AlertLog).order_by(AlertLog.triggered_at.desc())
    if from_dt:
        q = q.filter(AlertLog.triggered_at >= datetime.fromisoformat(from_dt))
    if to_dt:
        q = q.filter(AlertLog.triggered_at <= datetime.fromisoformat(to_dt))
    if severidade:
        q = q.filter(AlertLog.severidade == severidade)
    if metrica:
        q = q.filter(AlertLog.metrica == metrica)
    logs = q.limit(min(limit, 500)).all()
    return [_log_dict(a) for a in logs]


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
    }
```

- [ ] **Step 2: Registrar router em `backend/main.py`**

```python
from api.alerts import router as alerts_router
# ...
app.include_router(alerts_router)
```

- [ ] **Step 3: Criar `backend/tests/test_alerts_api.py`**

```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import models.database as db_module
    from sqlalchemy import create_engine
    test_engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    db_module.Base.metadata.create_all(test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    db_module.init_db()

    import os
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-32-chars-minimum!!")

    from main import app
    return TestClient(app)


def get_token(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "admin"})
    return r.json()["token"]


def auth(client):
    return {"Authorization": f"Bearer {get_token(client)}"}


def test_list_rules_requires_auth(client):
    r = client.get("/api/alerts/rules")
    assert r.status_code == 401


def test_list_rules_returns_defaults(client):
    r = client.get("/api/alerts/rules", headers=auth(client))
    assert r.status_code == 200
    assert len(r.json()) == 9


def test_create_and_delete_rule(client):
    h = auth(client)
    r = client.post("/api/alerts/rules", json={
        "nome": "Test", "metrica": "cpu_percent", "operador": ">",
        "threshold": 90.0, "severidade": "aviso"
    }, headers=h)
    assert r.status_code == 201
    rule_id = r.json()["id"]

    r2 = client.delete(f"/api/alerts/rules/{rule_id}", headers=h)
    assert r2.status_code == 200


def test_toggle_rule(client):
    h = auth(client)
    rules = client.get("/api/alerts/rules", headers=h).json()
    rid = rules[0]["id"]
    r = client.post(f"/api/alerts/rules/{rid}/toggle", headers=h)
    assert r.status_code == 200
    assert r.json()["ativo"] == 0


def test_active_alerts_empty_initially(client):
    r = client.get("/api/alerts/active", headers=auth(client))
    assert r.status_code == 200
    assert r.json() == []


def test_history_requires_auth(client):
    r = client.get("/api/alerts/history")
    assert r.status_code == 401


def test_update_rule(client):
    h = auth(client)
    rules = client.get("/api/alerts/rules", headers=h).json()
    rid = rules[0]["id"]
    r = client.put(f"/api/alerts/rules/{rid}", json={
        "nome": "CPU Alterado", "metrica": "cpu_percent", "operador": ">",
        "threshold": 99.0, "severidade": "critico",
    }, headers=h)
    assert r.status_code == 200
    updated = client.get("/api/alerts/rules", headers=h).json()
    rule = next(x for x in updated if x["id"] == rid)
    assert rule["threshold"] == 99.0
```

- [ ] **Step 4: Rodar testes**

```bash
cd backend && python -m pytest tests/test_alerts_api.py -v
```

Esperado: 7/7 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/api/alerts.py backend/main.py backend/tests/test_alerts_api.py
git commit -m "feat: API de alertas — CRUD de regras + alertas ativos + histórico"
```

---

### Task 4: AlertBadge Component

**Files:**
- Create: `frontend/components/AlertBadge.tsx`

**Interfaces:**
- Produces: `<AlertBadge severidade="aviso"|"critico"|"info" />` → badge colorido
- critico → `var(--danger)`, aviso → `var(--warning)`, info → `var(--info)`

- [ ] **Step 1: Criar `frontend/components/AlertBadge.tsx`**

```tsx
'use client'

interface Props {
  severidade: string
}

const COLORS: Record<string, string> = {
  critico: 'var(--danger)',
  aviso: 'var(--warning)',
  info: 'var(--info)',
}

const LABELS: Record<string, string> = {
  critico: 'CRÍTICO',
  aviso: 'AVISO',
  info: 'INFO',
}

export default function AlertBadge({ severidade }: Props) {
  const color = COLORS[severidade] ?? 'var(--muted)'
  const label = LABELS[severidade] ?? severidade.toUpperCase()
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 700,
      letterSpacing: '0.05em',
      color: '#fff',
      background: color,
    }}>
      {label}
    </span>
  )
}
```

- [ ] **Step 2: Verificar TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

Esperado: zero erros

- [ ] **Step 3: Commit**

```bash
git add frontend/components/AlertBadge.tsx
git commit -m "feat: componente AlertBadge com severidades critico/aviso/info"
```

---

### Task 5: Alertas Page

**Files:**
- Modify: `frontend/app/alertas/page.tsx` (substituir .gitkeep)
- Modify: `frontend/app/layout.tsx` (confirmar link "Alertas" no sidebar)

**Interfaces:**
- Consumes: `api.get('/alerts/active')` → `AlertLog[]`, `api.get('/alerts/history?...')` → `AlertLog[]`, `api.get('/alerts/rules')` → `AlertRule[]`
- Consumes: `AlertBadge` component
- Consumes: WS `active_alerts` do payload para badge de contagem no sidebar

**Tipos necessários:**

```typescript
interface AlertLog {
  id: number
  rule_id: number
  triggered_at: string
  resolved_at: string | null
  severidade: string
  metrica: string
  valor_no_disparo: number
  threshold: number
  mensagem: string
}

interface AlertRule {
  id: number
  nome: string
  metrica: string
  operador: string
  threshold: number
  duracao_minutos: number
  severidade: string
  canal_email: number
  canal_whatsapp: number
  cooldown_minutos: number
  ativo: number
  criado_em: string | null
}
```

**Formulário de criação/edição de regra:**

Campos: nome (text), metrica (select), operador (select), threshold (number), duracao_minutos (number), severidade (select), cooldown_minutos (number), canal_email (checkbox), canal_whatsapp (checkbox).

Métricas disponíveis: `cpu_percent`, `ram_percent`, `disk_percent`, `temperature_c`, `load_1m`, `container_stopped`
Operadores: `>`, `<`, `>=`, `<=`
Severidades: `critico`, `aviso`, `info`

- [ ] **Step 1: Criar `frontend/app/alertas/page.tsx`**

```tsx
'use client'

import { useEffect, useState, useCallback } from 'react'
import api from '../../lib/api'
import AlertBadge from '../../components/AlertBadge'
import Toast from '../../components/Toast'

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
}

interface AlertRule {
  id: number
  nome: string
  metrica: string
  operador: string
  threshold: number
  duracao_minutos: number
  severidade: string
  canal_email: number
  canal_whatsapp: number
  cooldown_minutos: number
  ativo: number
  criado_em: string | null
}

type Tab = 'ativas' | 'historico' | 'regras'

const METRICAS = ['cpu_percent', 'ram_percent', 'disk_percent', 'temperature_c', 'load_1m', 'container_stopped']
const OPERADORES = ['>', '<', '>=', '<=']
const SEVERIDADES = ['critico', 'aviso', 'info']

const METRICA_LABELS: Record<string, string> = {
  cpu_percent: 'CPU (%)',
  ram_percent: 'RAM (%)',
  disk_percent: 'Disco (%)',
  temperature_c: 'Temperatura (°C)',
  load_1m: 'Load Average 1m',
  container_stopped: 'Container Parado',
}

function elapsed(from: string): string {
  const diff = Math.floor((Date.now() - new Date(from).getTime()) / 1000)
  if (diff < 60) return `${diff}s`
  if (diff < 3600) return `${Math.floor(diff / 60)}m`
  return `${Math.floor(diff / 3600)}h ${Math.floor((diff % 3600) / 60)}m`
}

function formatDt(iso: string): string {
  return new Date(iso).toLocaleString('pt-BR')
}

const emptyForm = (): Omit<AlertRule, 'id' | 'criado_em'> => ({
  nome: '', metrica: 'cpu_percent', operador: '>', threshold: 80,
  duracao_minutos: 5, severidade: 'aviso',
  canal_email: 1, canal_whatsapp: 1, cooldown_minutos: 30, ativo: 1,
})

const card: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8,
}

const badge = (active: boolean): React.CSSProperties => ({
  display: 'inline-block', padding: '4px 12px', borderRadius: 6,
  fontSize: 13, fontWeight: 600, cursor: 'pointer',
  background: active ? 'var(--accent)' : 'var(--surface)',
  color: active ? '#000' : 'var(--muted)',
  border: '1px solid var(--border)',
  marginRight: 8,
})

const input: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
}

export default function AlertasPage() {
  const [tab, setTab] = useState<Tab>('ativas')
  const [active, setActive] = useState<AlertLog[]>([])
  const [history, setHistory] = useState<AlertLog[]>([])
  const [rules, setRules] = useState<AlertRule[]>([])
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null)
  const [form, setForm] = useState(emptyForm())
  const [editId, setEditId] = useState<number | null>(null)
  const [showForm, setShowForm] = useState(false)

  // Filtros histórico
  const [filtSeveridade, setFiltSeveridade] = useState('')
  const [filtMetrica, setFiltMetrica] = useState('')

  const loadActive = useCallback(async () => {
    try { setActive((await api.get('/alerts/active')).data) } catch {}
  }, [])

  const loadHistory = useCallback(async () => {
    const params = new URLSearchParams()
    if (filtSeveridade) params.set('severidade', filtSeveridade)
    if (filtMetrica) params.set('metrica', filtMetrica)
    params.set('limit', '200')
    try { setHistory((await api.get(`/alerts/history?${params}`)).data) } catch {}
  }, [filtSeveridade, filtMetrica])

  const loadRules = useCallback(async () => {
    try { setRules((await api.get('/alerts/rules')).data) } catch {}
  }, [])

  useEffect(() => { loadActive(); loadRules() }, [loadActive, loadRules])
  useEffect(() => { if (tab === 'historico') loadHistory() }, [tab, loadHistory])

  // Auto-refresh ativas a cada 30s
  useEffect(() => {
    const id = setInterval(loadActive, 30000)
    return () => clearInterval(id)
  }, [loadActive])

  async function toggleRule(rule: AlertRule) {
    try {
      const r = await api.post(`/alerts/rules/${rule.id}/toggle`)
      setRules(prev => prev.map(x => x.id === rule.id ? { ...x, ativo: r.data.ativo } : x))
    } catch { setToast({ msg: 'Erro ao alterar regra', type: 'error' }) }
  }

  async function deleteRule(id: number) {
    if (!confirm('Excluir esta regra?')) return
    try {
      await api.delete(`/alerts/rules/${id}`)
      setRules(prev => prev.filter(r => r.id !== id))
      setToast({ msg: 'Regra excluída', type: 'success' })
    } catch { setToast({ msg: 'Erro ao excluir', type: 'error' }) }
  }

  function startEdit(rule: AlertRule) {
    setEditId(rule.id)
    setForm({ nome: rule.nome, metrica: rule.metrica, operador: rule.operador, threshold: rule.threshold, duracao_minutos: rule.duracao_minutos, severidade: rule.severidade, canal_email: rule.canal_email, canal_whatsapp: rule.canal_whatsapp, cooldown_minutos: rule.cooldown_minutos, ativo: rule.ativo })
    setShowForm(true)
  }

  function startCreate() {
    setEditId(null)
    setForm(emptyForm())
    setShowForm(true)
  }

  async function saveRule() {
    try {
      if (editId !== null) {
        await api.put(`/alerts/rules/${editId}`, form)
        setToast({ msg: 'Regra atualizada', type: 'success' })
      } else {
        await api.post('/alerts/rules', form)
        setToast({ msg: 'Regra criada', type: 'success' })
      }
      setShowForm(false)
      loadRules()
    } catch { setToast({ msg: 'Erro ao salvar regra', type: 'error' }) }
  }

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      {toast && <Toast message={toast.msg} type={toast.type} onClose={() => setToast(null)} />}
      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Alertas</h1>

      {/* Tabs */}
      <div style={{ marginBottom: 20 }}>
        {(['ativas', 'historico', 'regras'] as Tab[]).map(t => (
          <button key={t} style={badge(tab === t)} onClick={() => setTab(t)}>
            {t === 'ativas' ? `Ativas (${active.length})` : t === 'historico' ? 'Histórico' : 'Regras'}
          </button>
        ))}
      </div>

      {/* TAB: ATIVAS */}
      {tab === 'ativas' && (
        <div>
          {active.length === 0 && (
            <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>
              ✅ Nenhum alerta ativo no momento
            </p>
          )}
          {active.map(a => (
            <div key={a.id} style={{ ...card, borderLeft: `4px solid ${a.severidade === 'critico' ? 'var(--danger)' : 'var(--warning)'}` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
                <AlertBadge severidade={a.severidade} />
                <span style={{ color: 'var(--text)', fontWeight: 600 }}>{a.mensagem}</span>
              </div>
              <div style={{ color: 'var(--muted)', fontSize: 13 }}>
                Iniciado: {formatDt(a.triggered_at)} · Duração: {elapsed(a.triggered_at)}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* TAB: HISTÓRICO */}
      {tab === 'historico' && (
        <div>
          <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
            <select style={{ ...input, width: 180 }} value={filtSeveridade} onChange={e => setFiltSeveridade(e.target.value)}>
              <option value="">Todas severidades</option>
              {SEVERIDADES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <select style={{ ...input, width: 220 }} value={filtMetrica} onChange={e => setFiltMetrica(e.target.value)}>
              <option value="">Todas métricas</option>
              {METRICAS.map(m => <option key={m} value={m}>{METRICA_LABELS[m]}</option>)}
            </select>
            <button style={{ padding: '6px 14px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 600 }} onClick={loadHistory}>
              Filtrar
            </button>
          </div>
          {history.length === 0 && <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Nenhum alerta no histórico</p>}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ color: 'var(--muted)', borderBottom: '1px solid var(--border)' }}>
                {['Severidade', 'Métrica', 'Mensagem', 'Disparado em', 'Resolvido em'].map(h => (
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
                  <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>{a.triggered_at ? formatDt(a.triggered_at) : '—'}</td>
                  <td style={{ padding: '8px 10px', whiteSpace: 'nowrap', color: a.resolved_at ? 'var(--success)' : 'var(--warning)' }}>
                    {a.resolved_at ? formatDt(a.resolved_at) : 'Ativo'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* TAB: REGRAS */}
      {tab === 'regras' && (
        <div>
          <div style={{ marginBottom: 16 }}>
            <button onClick={startCreate} style={{ padding: '8px 16px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}>
              + Nova Regra
            </button>
          </div>

          {/* Formulário */}
          {showForm && (
            <div style={{ ...card, marginBottom: 20, border: '1px solid var(--accent)' }}>
              <h3 style={{ color: 'var(--text)', marginBottom: 16 }}>{editId ? 'Editar Regra' : 'Nova Regra'}</h3>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Nome</label>
                  <input style={input} value={form.nome} onChange={e => setForm(f => ({ ...f, nome: e.target.value }))} />
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Métrica</label>
                  <select style={input} value={form.metrica} onChange={e => setForm(f => ({ ...f, metrica: e.target.value }))}>
                    {METRICAS.map(m => <option key={m} value={m}>{METRICA_LABELS[m]}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Operador</label>
                  <select style={input} value={form.operador} onChange={e => setForm(f => ({ ...f, operador: e.target.value }))}>
                    {OPERADORES.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Threshold</label>
                  <input type="number" style={input} value={form.threshold} onChange={e => setForm(f => ({ ...f, threshold: Number(e.target.value) }))} />
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Duração mínima (min)</label>
                  <input type="number" style={input} value={form.duracao_minutos} onChange={e => setForm(f => ({ ...f, duracao_minutos: Number(e.target.value) }))} />
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Severidade</label>
                  <select style={input} value={form.severidade} onChange={e => setForm(f => ({ ...f, severidade: e.target.value }))}>
                    {SEVERIDADES.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Cooldown (min)</label>
                  <input type="number" style={input} value={form.cooldown_minutos} onChange={e => setForm(f => ({ ...f, cooldown_minutos: Number(e.target.value) }))} />
                </div>
                <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                  <label style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input type="checkbox" checked={!!form.canal_email} onChange={e => setForm(f => ({ ...f, canal_email: e.target.checked ? 1 : 0 }))} />
                    E-mail
                  </label>
                  <label style={{ color: 'var(--muted)', fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input type="checkbox" checked={!!form.canal_whatsapp} onChange={e => setForm(f => ({ ...f, canal_whatsapp: e.target.checked ? 1 : 0 }))} />
                    WhatsApp
                  </label>
                </div>
              </div>
              <div style={{ marginTop: 16, display: 'flex', gap: 10 }}>
                <button onClick={saveRule} style={{ padding: '8px 20px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}>
                  Salvar
                </button>
                <button onClick={() => setShowForm(false)} style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}>
                  Cancelar
                </button>
              </div>
            </div>
          )}

          {/* Lista de regras */}
          {rules.map(rule => (
            <div key={rule.id} style={{ ...card, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
              <div style={{ flex: 1, minWidth: 200 }}>
                <div style={{ color: 'var(--text)', fontWeight: 600 }}>{rule.nome}</div>
                <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 2 }}>
                  {METRICA_LABELS[rule.metrica] ?? rule.metrica} {rule.operador} {rule.threshold}
                  {rule.duracao_minutos > 0 && ` · ${rule.duracao_minutos}min`}
                  {` · cooldown ${rule.cooldown_minutos}min`}
                </div>
              </div>
              <AlertBadge severidade={rule.severidade} />
              <button
                onClick={() => toggleRule(rule)}
                style={{ padding: '4px 12px', borderRadius: 6, border: '1px solid var(--border)', cursor: 'pointer', fontSize: 12, fontWeight: 600, background: rule.ativo ? 'var(--success)' : 'var(--surface)', color: rule.ativo ? '#fff' : 'var(--muted)' }}
              >
                {rule.ativo ? 'Ativo' : 'Inativo'}
              </button>
              <button onClick={() => startEdit(rule)} style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', cursor: 'pointer', fontSize: 12 }}>
                Editar
              </button>
              <button onClick={() => deleteRule(rule.id)} style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 6, color: 'var(--danger)', cursor: 'pointer', fontSize: 12 }}>
                Excluir
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verificar link "Alertas" no sidebar em `frontend/app/layout.tsx`**

Se o link não existir, adicione-o na lista de navegação:
```tsx
{ href: '/alertas', label: 'Alertas' }
```

- [ ] **Step 3: Verificar TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

Esperado: zero erros

- [ ] **Step 4: Commit**

```bash
git add frontend/app/alertas/page.tsx frontend/app/layout.tsx
git commit -m "feat: página de alertas — abas ativas/histórico/regras com CRUD"
```

---

## Resumo dos Entregáveis

| Task | Arquivo(s) | O que entrega |
|------|------------|---------------|
| 1 | `api/health.py`, `main.py`, `README.md` | `GET /api/health` público + docs UptimeRobot |
| 2 | `notifications/alert_engine.py`, `tests/test_alert_engine.py` | Motor de avaliação + ciclo create/resolve |
| 3 | `api/alerts.py`, `main.py`, `tests/test_alerts_api.py` | CRUD regras + alertas ativos/histórico |
| 4 | `components/AlertBadge.tsx` | Badge de severidade reutilizável |
| 5 | `app/alertas/page.tsx`, `app/layout.tsx` | Página de alertas 3 abas + link sidebar |
