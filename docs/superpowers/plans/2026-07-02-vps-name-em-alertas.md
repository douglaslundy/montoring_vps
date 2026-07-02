# Nome da VPS nos Alertas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cada alerta (novo e já existente) passa a carregar o nome da VPS que o gerou, como campo próprio `vps_name`, reaproveitando a config `server_name` já existente, para que seja possível distinguir de qual VPS um alerta veio quando há mais de uma instância do sistema rodando.

**Architecture:** Nova coluna `vps_name` em `AlertLog`, populada pelo motor de alertas (`notifications/alert_engine.py`) a partir da config `server_name` no momento da criação de cada alerta. Uma migração leve (`ALTER TABLE` + backfill) em `init_db()` preenche o campo para alertas já existentes na próxima subida do backend. O campo é exposto pela API (`/api/alerts/active`, `/api/alerts/history`, payload do WebSocket) e exibido no frontend como um badge separado — sem alterar o texto de `mensagem`.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite), pytest, Next.js/React (TypeScript).

## Global Constraints

- Reaproveitar a config `server_name` existente — não criar um campo de configuração novo.
- Não alterar o conteúdo de `AlertLog.mensagem` nem o texto das notificações de e-mail/WhatsApp (já mostram o nome no rodapé).
- `vps_name` é um snapshot no momento da criação do alerta, não um lookup ao vivo — preserva o valor histórico correto mesmo que `server_name` mude depois.
- Migração/backfill deve ser idempotente e automática (roda dentro de `init_db()`, sem passo manual).

---

### Task 1: Schema — coluna `vps_name` + migração/backfill

**Files:**
- Modify: `backend/models/database.py`
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Produces: `AlertLog.vps_name` (String, nullable) — usado pelas Tasks 2 e 3.
- Consumes: `Config` model e `_DEFAULT_CONFIG["server_name"]`, já existentes em `backend/models/database.py`.

- [ ] **Step 1: Escrever os testes que falham**

Adicione ao final de `backend/tests/test_database.py`:

```python
def test_alert_log_tem_coluna_vps_name(test_db):
    from sqlalchemy import inspect
    cols = {c["name"] for c in inspect(test_db.engine).get_columns("alert_log")}
    assert "vps_name" in cols


def test_backfill_preenche_vps_name_com_server_name_padrao(test_db):
    from datetime import datetime
    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        session.add(test_db.AlertLog(
            rule_id=1, triggered_at=datetime.utcnow(), severidade="critico",
            metrica="cpu_percent", mensagem="alerta antigo sem vps_name",
        ))
        session.commit()

    # Simula reinício do backend, que roda a migração/backfill de novo
    test_db.init_db()

    with Session(test_db.engine) as session:
        fetched = session.query(test_db.AlertLog).filter_by(mensagem="alerta antigo sem vps_name").first()
    assert fetched.vps_name == "VPS Monitor"


def test_backfill_usa_server_name_customizado(test_db):
    from datetime import datetime
    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        cfg = session.get(test_db.Config, "server_name")
        cfg.value = "VPS-SP1"
        session.add(test_db.AlertLog(
            rule_id=1, triggered_at=datetime.utcnow(), severidade="critico",
            metrica="cpu_percent", mensagem="outro alerta antigo",
        ))
        session.commit()

    test_db.init_db()

    with Session(test_db.engine) as session:
        fetched = session.query(test_db.AlertLog).filter_by(mensagem="outro alerta antigo").first()
    assert fetched.vps_name == "VPS-SP1"
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && py -m pytest tests/test_database.py -k "vps_name" -v`
Expected: FAIL — `AttributeError: 'AlertLog' object has no attribute 'vps_name'` (ou coluna inexistente).

- [ ] **Step 3: Adicionar a coluna ao model `AlertLog`**

Em `backend/models/database.py`, no bloco da classe `AlertLog` (logo após o campo `mensagem`, antes de `notificado_email`):

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

- [ ] **Step 4: Adicionar migração + backfill em `init_db()`**

Em `backend/models/database.py`, a função `init_db()` atual é:

```python
def init_db():
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.commit()
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE alert_log ADD COLUMN last_notified_at DATETIME"))
            conn.commit()
        except Exception:
            pass  # Coluna já existe
    with Session(engine) as session:
        if session.query(AlertRule).count() == 0:
            for rule in _DEFAULT_RULES:
                session.add(AlertRule(**rule))
        for key, value in _DEFAULT_CONFIG.items():
            if not session.get(Config, key):
                session.add(Config(key=key, value=value))
        session.commit()
```

Substitua por:

```python
def init_db():
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.commit()
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
    with Session(engine) as session:
        if session.query(AlertRule).count() == 0:
            for rule in _DEFAULT_RULES:
                session.add(AlertRule(**rule))
        for key, value in _DEFAULT_CONFIG.items():
            if not session.get(Config, key):
                session.add(Config(key=key, value=value))
        session.commit()

        server_name_row = session.get(Config, "server_name")
        server_name = server_name_row.value if server_name_row else _DEFAULT_CONFIG["server_name"]
        session.query(AlertLog).filter(AlertLog.vps_name.is_(None)).update(
            {AlertLog.vps_name: server_name}, synchronize_session=False
        )
        session.commit()
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `cd backend && py -m pytest tests/test_database.py -v`
Expected: todos os testes de `test_database.py` passam, incluindo os 3 novos.

- [ ] **Step 6: Commit**

```bash
git add backend/models/database.py backend/tests/test_database.py
git commit -m "feat: adiciona vps_name ao AlertLog com backfill automatico"
```

---

### Task 2: Motor de alertas grava `vps_name`

**Files:**
- Modify: `backend/notifications/alert_engine.py`
- Test: `backend/tests/test_alert_engine.py`

**Interfaces:**
- Consumes: `AlertLog.vps_name` (Task 1); `get_config(session, key, default)` de `backend/api/config.py` (já usado em `_notify_alert`/`_notify_resolution` via import local).
- Produces: `evaluate(metrics, containers) -> list[dict]` onde cada dict agora inclui `"vps_name"`. Usado pelo payload do WebSocket (`collector/scheduler.py`, sem mudança necessária lá) e pela Task 3.

- [ ] **Step 1: Escrever os testes que falham**

Adicione a `backend/tests/test_alert_engine.py`, junto aos imports do topo, `Config`:

```python
from models.database import AlertLog, AlertRule, Config, engine, init_db
```

E adicione estes dois testes (por exemplo, logo após `test_container_stopped_resolves_when_removed`):

```python
def test_container_stopped_alert_grava_vps_name_padrao(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1)
    asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "exited"}]))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
    assert log.vps_name == "VPS Monitor"


def test_metric_alert_grava_vps_name_configurado(fresh_db):
    from notifications.alert_engine import evaluate
    rule_id = add_rule(fresh_db, threshold=80.0, metrica="cpu_percent", operador=">")
    with Session(fresh_db) as s:
        s.add(Config(key="server_name", value="VPS-SP1"))
        s.commit()
    asyncio.run(evaluate(make_metrics(cpu=90.0), []))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.rule_id == rule_id).first()
    assert log.vps_name == "VPS-SP1"
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -k vps_name -v`
Expected: FAIL — `assert None == "VPS Monitor"` (ou `AssertionError` equivalente), pois `vps_name` ainda não é preenchido.

- [ ] **Step 3: Passar `vps_name` para `_evaluate_rule` e gravar no `AlertLog`**

Em `backend/notifications/alert_engine.py`, altere a assinatura e o `AlertLog(...)` dentro de `_evaluate_rule`:

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
    elif condition_true and open_log is not None:
        # Verifica se deve notificar (duracao_minutos atingida e cooldown passou)
        duration_ok = rule.duracao_minutos == 0 or (
            (now - open_log.triggered_at).total_seconds() / 60 >= rule.duracao_minutos
        )
        cooldown_ok = (
            open_log.last_notified_at is None or
            (now - open_log.last_notified_at).total_seconds() / 60 >= rule.cooldown_minutos
        )
        if duration_ok and cooldown_ok:
            _notify_alert(session, open_log, rule, now)
    elif not condition_true and open_log is not None:
        open_log.resolved_at = now
        _notify_resolution(session, open_log, rule)
```

- [ ] **Step 4: Passar `vps_name` para `_evaluate_container_stopped` e gravar no `AlertLog`**

Na mesma arquivo, altere a assinatura e o `AlertLog(...)` dentro de `_evaluate_container_stopped`:

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

    # Resolve containers que voltaram a running OU que foram removidos
    # (recriados com outro nome/ID em vez de reiniciados — nesse caso o nome
    # antigo nunca mais vai reaparecer na lista, então o alerta ficaria preso)
    running_names = {c["name"] for c in containers if c.get("status") == "running"}
    known_names = {c["name"] for c in containers}
    open_container_logs = (
        session.query(AlertLog)
        .filter(AlertLog.rule_id == rule.id, AlertLog.resolved_at.is_(None))
        .all()
    )
    for log in open_container_logs:
        m = re.search(r"Container '(.+)' parado", log.mensagem or "")
        if not m:
            continue
        container_name = m.group(1)
        if container_name in running_names or container_name not in known_names:
            log.resolved_at = now
```

- [ ] **Step 5: Ler `server_name` uma vez por ciclo em `evaluate()` e repassar; incluir `vps_name` no retorno**

Substitua a função `evaluate()` inteira por:

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
                        value = _get_metric_value(rule.metrica, metrics, containers)
                        if value is None:
                            continue
                        mensagem = f"{rule.nome}: {value:.1f} {rule.operador} {rule.threshold}"
                        _evaluate_rule(session, rule, value, mensagem, now, vps_name)
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
                    "vps_name": a.vps_name,
                }
                for a in active
            ]
    except Exception:
        logger.exception("Erro no motor de alertas")
        return []
```

- [ ] **Step 6: Rodar toda a suíte de `test_alert_engine.py` e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_alert_engine.py -v`
Expected: todos os 10 testes passam (8 anteriores + 2 novos).

- [ ] **Step 7: Commit**

```bash
git add backend/notifications/alert_engine.py backend/tests/test_alert_engine.py
git commit -m "feat: motor de alertas grava vps_name em cada AlertLog"
```

---

### Task 3: API expõe `vps_name`

**Files:**
- Modify: `backend/api/alerts.py`
- Test: `backend/tests/test_alerts_api.py`

**Interfaces:**
- Consumes: `AlertLog.vps_name` (Task 1).
- Produces: `/api/alerts/active` e `/api/alerts/history` retornam `"vps_name"` em cada item — consumido pelo frontend nas Tasks 4 e 5.

- [ ] **Step 1: Escrever o teste que falha**

Adicione a `backend/tests/test_alerts_api.py`:

```python
def test_active_alerts_inclui_vps_name(client):
    import models.database as db_module
    from datetime import datetime
    with db_module.Session(db_module.engine) as s:
        s.add(db_module.AlertLog(
            rule_id=None, triggered_at=datetime.utcnow(), severidade="critico",
            metrica="cpu_percent", mensagem="teste", vps_name="VPS-SP1",
        ))
        s.commit()

    r = client.get("/api/alerts/active", headers=auth(client))
    assert r.status_code == 200
    assert r.json()[0]["vps_name"] == "VPS-SP1"
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_alerts_api.py -k vps_name -v`
Expected: FAIL — `KeyError: 'vps_name'`.

- [ ] **Step 3: Adicionar `vps_name` ao `_log_dict`**

Em `backend/api/alerts.py`, altere a função `_log_dict`:

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

- [ ] **Step 4: Rodar toda a suíte de `test_alerts_api.py` e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_alerts_api.py -v`
Expected: todos os testes passam, incluindo o novo.

- [ ] **Step 5: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest`
Expected: mesma quantidade de falhas pré-existentes de antes desta feature (relacionadas a `JWT_SECRET` ausente em `test_metrics_api.py`, `test_scheduler.py`, `test_websocket.py` — não relacionadas a este trabalho) e nenhuma falha nova.

- [ ] **Step 6: Commit**

```bash
git add backend/api/alerts.py backend/tests/test_alerts_api.py
git commit -m "feat: API de alertas expoe vps_name"
```

---

### Task 4: Frontend — tipos e componente `VpsBadge`

**Files:**
- Modify: `frontend/lib/ws.ts`
- Create: `frontend/components/VpsBadge.tsx`
- Modify: `frontend/app/alertas/page.tsx` (apenas a interface `AlertLog`, sem uso ainda)

**Interfaces:**
- Produces: `ActiveAlert.vps_name: string | null` (tipo); componente `<VpsBadge name={string | null | undefined} />` — usado pela Task 5.

- [ ] **Step 1: Adicionar `vps_name` à interface `ActiveAlert`**

Em `frontend/lib/ws.ts`, altere:

```typescript
export interface ActiveAlert {
  id: number;
  severidade: 'aviso' | 'critico';
  metrica: string;
  mensagem: string;
  triggered_at: string;
  vps_name: string | null;
}
```

- [ ] **Step 2: Adicionar `vps_name` à interface `AlertLog` da página de alertas**

Em `frontend/app/alertas/page.tsx`, altere a interface `AlertLog`:

```typescript
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

- [ ] **Step 3: Criar o componente `VpsBadge`**

Crie `frontend/components/VpsBadge.tsx`:

```tsx
'use client'

interface Props {
  name?: string | null
}

export default function VpsBadge({ name }: Props) {
  if (!name) return null
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 600,
      color: 'var(--muted)',
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      whiteSpace: 'nowrap',
    }}>
      🖥️ {name}
    </span>
  )
}
```

- [ ] **Step 4: Verificar que o projeto compila**

Run: `cd frontend && npm run build`
Expected: build conclui sem erros de tipo (o componente novo ainda não é usado em lugar nenhum, então isso só confirma que a sintaxe/tipos estão corretos).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/ws.ts frontend/app/alertas/page.tsx frontend/components/VpsBadge.tsx
git commit -m "feat: adiciona tipo vps_name e componente VpsBadge"
```

---

### Task 5: Frontend — exibir o nome da VPS nos alertas

**Files:**
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/app/alertas/page.tsx`

**Interfaces:**
- Consumes: `VpsBadge` e tipos com `vps_name` (Task 4).

- [ ] **Step 1: Exibir o badge no bloco "Alertas Ativos" do dashboard**

Em `frontend/app/page.tsx`, adicione o import no topo:

```typescript
import VpsBadge from '../components/VpsBadge';
```

E altere o bloco (por volta da linha 261-277):

```tsx
      {/* Alertas ativos */}
      {alertCount > 0 && (
        <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 20 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 16 }}>Alertas Ativos</div>
          {effectiveData!.active_alerts.map((a: any) => (
            <div key={a.id} style={{ display: 'flex', gap: 12, padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
              <span style={{ fontSize: 16 }}>{a.severidade === 'critico' ? '🔴' : '⚠️'}</span>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ fontSize: 13 }}>{a.mensagem}</div>
                  <VpsBadge name={a.vps_name} />
                </div>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>
                  {new Date(a.triggered_at).toLocaleString('pt-BR')}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
```

- [ ] **Step 2: Exibir o badge na aba "Ativas" de `alertas/page.tsx`**

Em `frontend/app/alertas/page.tsx`, adicione o import no topo (junto aos demais imports):

```typescript
import VpsBadge from '../../components/VpsBadge'
```

E altere o card da aba "Ativas" (por volta da linha 202-212):

```tsx
          {active.map(a => (
            <div key={a.id} style={{ ...card, borderLeft: `4px solid ${a.severidade === 'critico' ? 'var(--danger)' : 'var(--warning)'}` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
                <AlertBadge severidade={a.severidade} />
                <span style={{ color: 'var(--text)', fontWeight: 600 }}>{a.mensagem}</span>
                <VpsBadge name={a.vps_name} />
              </div>
              <div style={{ color: 'var(--muted)', fontSize: 13 }}>
                Iniciado: {formatDt(a.triggered_at)} · Duração: {elapsed(a.triggered_at)}
              </div>
            </div>
          ))}
```

- [ ] **Step 3: Adicionar coluna "VPS" na tabela da aba "Histórico"**

Ainda em `frontend/app/alertas/page.tsx`, altere o cabeçalho da tabela (por volta da linha 238-244):

```tsx
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ color: 'var(--muted)', borderBottom: '1px solid var(--border)' }}>
                {['Severidade', 'Métrica', 'Mensagem', 'VPS', 'Disparado em', 'Resolvido em'].map(h => (
                  <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontWeight: 600 }}>{h}</th>
                ))}
              </tr>
            </thead>
```

E o corpo da tabela (por volta da linha 246-258):

```tsx
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
```

- [ ] **Step 4: Verificar que o projeto compila**

Run: `cd frontend && npm run build`
Expected: build conclui sem erros de tipo.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/page.tsx frontend/app/alertas/page.tsx
git commit -m "feat: exibe nome da VPS nos alertas do dashboard e da aba Alertas"
```

---

## Verificação manual (pós-implementação)

Depois de todas as tasks:

1. Rodar o backend localmente e confirmar, via `sqlite3` ou endpoint `/api/config`, que `server_name` está definido (ex: "VPS Monitor" ou o nome customizado).
2. Disparar um alerta de teste (ex: parar um container) e conferir que ele aparece:
   - No dashboard, bloco "Alertas Ativos", com o badge `🖥️ <nome>` ao lado da mensagem.
   - Em Alertas > Ativas, com o mesmo badge.
   - Em Alertas > Histórico, na coluna "VPS", depois de resolvido.
3. Alterar `server_name` em Configurações > Geral, disparar um novo alerta, e confirmar que o alerta novo mostra o nome atualizado — enquanto alertas antigos no Histórico continuam mostrando o nome antigo (snapshot histórico preservado).

## Self-Review

**Cobertura do spec:**
- Schema + migração/backfill automático → Task 1.
- Motor de alertas grava `vps_name` (métricas e container parado) → Task 2.
- API expõe `vps_name` → Task 3.
- Frontend exibe como campo/badge separado (dashboard, Ativas, Histórico) → Tasks 4 e 5.
- Reaproveita `server_name`, não cria config nova → Task 2, Step 5 (`get_config(session, "server_name", ...)`).
- Fora de escopo (agregação multi-VPS) → não implementado, conforme spec.

**Placeholders:** nenhum "TBD"/"implementar depois" — todo código está completo em cada step.

**Consistência de tipos:** `vps_name: string | null` usado de forma consistente em `ActiveAlert` (lib/ws.ts), `AlertLog` (alertas/page.tsx) e `VpsBadge({ name?: string | null })`. Backend sempre popula `vps_name` como string (nunca deixa `None` em alertas novos, pois `get_config` sempre retorna um default); `None` só ocorre teoricamente antes do backfill rodar, por isso `VpsBadge` trata `null`/`undefined` retornando `null` (não quebra a UI).
