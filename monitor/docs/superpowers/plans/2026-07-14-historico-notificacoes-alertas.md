# Histórico de Notificações de Alertas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir o bug que faz alertas com `duracao_minutos = 0` (Disco Alto/Crítico, Container Parado) nunca notificarem quando resolvem rápido, e substituir os campos soltos de notificação por um histórico real de tentativas de envio (e-mail/WhatsApp), visível na UI.

**Architecture:** Nova tabela `AlertNotification` (uma linha por tentativa de canal). O motor de alertas (`alert_engine.py`) passa a notificar já no ciclo em que o alerta é criado (quando a duração mínima já está satisfeita) em vez de esperar o próximo ciclo, e grava toda tentativa — sucesso, falha ou canal desabilitado — na nova tabela. A API embute essa lista em cada alerta retornado; o frontend renderiza um resumo compacto na aba Ativas e uma lista detalhada na aba Histórico.

**Tech Stack:** FastAPI + SQLAlchemy (backend, SQLite), pytest, Next.js/React + TypeScript (frontend).

## Global Constraints

- Valores de `status` em `AlertNotification`: exatamente `"enviado"`, `"falhou"`, `"desabilitado"` (strings, sem outras variantes).
- Valores de `canal`: exatamente `"email"`, `"whatsapp"`.
- Valores de `tipo`: exatamente `"disparo"`, `"resolucao"`.
- Não gravar linha em `AlertNotification` quando a regra não marca o canal (`canal_email = 0` ou `canal_whatsapp = 0`) — só quando o canal está marcado na regra.
- Os campos legados `notificado_email`, `notificado_whatsapp`, `erro_email`, `erro_whatsapp` em `AlertLog` NÃO são removidos do schema (evita `DROP COLUMN` em SQLite); simplesmente deixam de ser escritos.
- `AlertNotification` é tabela nova → criada automaticamente por `Base.metadata.create_all(engine)` em `init_db()`. Não escrever `ALTER TABLE` manual para ela.
- Retenção de `AlertNotification` usa o mesmo `retention_aggregated_days` já usado por `ContainerDiskUsage`/`ContainerMetrics`.
- Frontend deste projeto não tem framework de teste automatizado; a verificação de cada task de frontend é `cd frontend && npm run build` (compilação TypeScript sem erros).

---

### Task 1: Modelo `AlertNotification`

**Files:**
- Modify: `backend/models/database.py:101-103` (logo após a classe `AlertLog`, antes de `class ContainerActionLog`)
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Produces: `AlertNotification` (SQLAlchemy model) com colunas `id`, `alert_log_id` (FK `alert_log.id`), `canal` (String), `tipo` (String), `status` (String), `erro` (Text, nullable), `tentativa_em` (DateTime).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `backend/tests/test_database.py`:

```python
def test_tabela_alert_notification_criada(test_db):
    with test_db.engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result}
    assert "alert_notification" in tables


def test_insert_alert_notification(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        log = test_db.AlertLog(
            rule_id=1, triggered_at=datetime.utcnow(), severidade="critico",
            metrica="disk_percent", mensagem="teste",
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        session.add(test_db.AlertNotification(
            alert_log_id=log.id, canal="whatsapp", tipo="disparo",
            status="enviado", tentativa_em=datetime.utcnow(),
        ))
        session.commit()
        fetched = session.query(test_db.AlertNotification).first()
    assert fetched.canal == "whatsapp"
    assert fetched.status == "enviado"
    assert fetched.alert_log_id == log.id
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && python -m pytest tests/test_database.py -k alert_notification -v`
Expected: FAIL (`alert_notification` table doesn't exist / `AttributeError: module 'models.database' has no attribute 'AlertNotification'`)

- [ ] **Step 3: Adicionar o modelo**

Em `backend/models/database.py`, imediatamente após o fim da classe `AlertLog` (linha 101, campo `last_notified_at`) e antes da linha em branco que precede `class ContainerActionLog`:

```python
class AlertNotification(Base):
    __tablename__ = "alert_notification"
    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_log_id = Column(Integer, ForeignKey("alert_log.id"), nullable=False)
    canal = Column(String, nullable=False)       # "email" | "whatsapp"
    tipo = Column(String, nullable=False)        # "disparo" | "resolucao"
    status = Column(String, nullable=False)      # "enviado" | "falhou" | "desabilitado"
    erro = Column(Text, nullable=True)
    tentativa_em = Column(DateTime, nullable=False, default=datetime.utcnow)


Index("ix_alert_notification_alert_log_id", AlertNotification.alert_log_id)
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd backend && python -m pytest tests/test_database.py -v`
Expected: PASS (todos, incluindo os dois novos)

- [ ] **Step 5: Commit**

```bash
git add backend/models/database.py backend/tests/test_database.py
git commit -m "feat: adiciona modelo AlertNotification para histórico de envios"
```

---

### Task 2: Notificar já no ciclo de criação + gravar tentativas (regras de métrica)

**Files:**
- Modify: `backend/notifications/alert_engine.py:9` (import), `:99-191` (`_evaluate_rule`, `_notify_alert`, `_notify_resolution`)
- Test: `backend/tests/test_alert_engine.py`

**Interfaces:**
- Consumes: `AlertNotification` (Task 1).
- Produces: `_record_notification(session, alert_log_id: int, canal: str, tipo: str, status: str, erro: str | None = None) -> None` — usado também pela Task 3.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `backend/tests/test_alert_engine.py` (o arquivo já importa `asyncio`, `Session`, `AlertLog`, `AlertRule`, `Config`, `engine`, `init_db` no topo — adicionar `AlertNotification` a esse import):

Alterar a linha de import no topo do arquivo:

```python
from models.database import AlertLog, AlertNotification, AlertRule, Config, engine, init_db
```

E adicionar ao final do arquivo:

```python
from unittest.mock import patch


def enable_channels(engine):
    with Session(engine) as s:
        s.merge(Config(key="smtp_enabled", value="1"))
        s.merge(Config(key="evolution_enabled", value="1"))
        s.commit()


def get_notifications(engine, alert_log_id=None):
    with Session(engine) as s:
        q = s.query(AlertNotification)
        if alert_log_id is not None:
            q = q.filter(AlertNotification.alert_log_id == alert_log_id)
        return q.all()


def test_alerta_flapping_ainda_notifica_antes_de_resolver(fresh_db):
    """Bug original: duracao_minutos=0 só notificava a partir do 2º ciclo
    do alerta aberto; se ele resolvesse no ciclo seguinte, nunca notificava."""
    from notifications.alert_engine import evaluate
    enable_channels(fresh_db)
    add_rule(fresh_db, threshold=80.0, metrica="disk_percent", operador=">",
             duracao_minutos=0, cooldown_minutos=120, canal_whatsapp=1, canal_email=0)
    with patch("notifications.whatsapp_service.send_alert") as mock_send:
        asyncio.run(evaluate(make_metrics(disk=90.0), []))
        with Session(fresh_db) as s:
            log = s.query(AlertLog).first()
        asyncio.run(evaluate(make_metrics(disk=70.0), []))  # resolve no ciclo seguinte
    disparos = [n for n in get_notifications(fresh_db, log.id) if n.tipo == "disparo"]
    assert len(disparos) == 1
    assert disparos[0].status == "enviado"
    assert disparos[0].canal == "whatsapp"
    mock_send.assert_called_once()


def test_canal_marcado_mas_desabilitado_globalmente_grava_status_desabilitado(fresh_db):
    from notifications.alert_engine import evaluate
    add_rule(fresh_db, threshold=80.0, metrica="disk_percent", operador=">",
             duracao_minutos=0, canal_whatsapp=1, canal_email=0)
    asyncio.run(evaluate(make_metrics(disk=90.0), []))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).first()
    notifs = get_notifications(fresh_db, log.id)
    assert len(notifs) == 1
    assert notifs[0].canal == "whatsapp"
    assert notifs[0].status == "desabilitado"
    assert notifs[0].erro is None


def test_erro_no_envio_grava_status_falhou_com_mensagem(fresh_db):
    from notifications.alert_engine import evaluate
    enable_channels(fresh_db)
    add_rule(fresh_db, threshold=80.0, metrica="disk_percent", operador=">",
             duracao_minutos=0, canal_whatsapp=1, canal_email=0)
    with patch("notifications.whatsapp_service.send_alert", side_effect=Exception("evolution indisponivel")):
        asyncio.run(evaluate(make_metrics(disk=90.0), []))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).first()
    notifs = get_notifications(fresh_db, log.id)
    assert notifs[0].status == "falhou"
    assert "evolution indisponivel" in notifs[0].erro


def test_canal_nao_marcado_na_regra_nao_gera_notificacao(fresh_db):
    from notifications.alert_engine import evaluate
    enable_channels(fresh_db)
    add_rule(fresh_db, threshold=80.0, metrica="disk_percent", operador=">",
             duracao_minutos=0, canal_whatsapp=0, canal_email=0)
    asyncio.run(evaluate(make_metrics(disk=90.0), []))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).first()
    assert get_notifications(fresh_db, log.id) == []


def test_resolucao_grava_notificacao_enviada(fresh_db):
    from notifications.alert_engine import evaluate
    enable_channels(fresh_db)
    add_rule(fresh_db, threshold=80.0, metrica="disk_percent", operador=">",
             duracao_minutos=0, cooldown_minutos=0, canal_whatsapp=1, canal_email=0)
    with patch("notifications.whatsapp_service.send_alert"):
        asyncio.run(evaluate(make_metrics(disk=90.0), []))
        with Session(fresh_db) as s:
            log = s.query(AlertLog).first()
        with patch("notifications.whatsapp_service.send_resolution") as mock_res:
            asyncio.run(evaluate(make_metrics(disk=70.0), []))
    resolucoes = [n for n in get_notifications(fresh_db, log.id) if n.tipo == "resolucao"]
    assert len(resolucoes) == 1
    assert resolucoes[0].status == "enviado"
    mock_res.assert_called_once()
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && python -m pytest tests/test_alert_engine.py -k "flapping or desabilitado or falhou_com_mensagem or nao_marcado_na_regra or resolucao_grava" -v`
Expected: FAIL (nenhuma notificação é gravada com o código atual — `assert len(disparos) == 1` falha com 0, etc.)

- [ ] **Step 3: Implementar o fix**

Em `backend/notifications/alert_engine.py`, linha 9, alterar o import:

```python
from models.database import AlertLog, AlertNotification, AlertRule, ContainerDiskUsage, engine
```

Substituir por completo as funções `_evaluate_rule`, `_notify_alert` e `_notify_resolution` (linhas 99–191) pelo bloco abaixo (inclui a nova função `_record_notification`):

```python
def _record_notification(session: Session, alert_log_id: int, canal: str, tipo: str, status: str, erro: Optional[str] = None) -> None:
    session.add(AlertNotification(
        alert_log_id=alert_log_id, canal=canal, tipo=tipo,
        status=status, erro=erro, tentativa_em=datetime.utcnow(),
    ))


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

    if condition_true and open_log is not None:
        # Verifica se deve notificar (duracao_minutos atingida e cooldown passou).
        # Avaliado também na criação: duracao_minutos=0 já satisfaz duration_ok
        # de imediato, então o alerta notifica no mesmo ciclo em que é criado
        # (antes bug: só notificava a partir do 2º ciclo do alerta aberto).
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


def _notify_alert(session: Session, log: AlertLog, rule: AlertRule, now: datetime):
    """Dispara notificação de alerta (email e/ou whatsapp) e grava cada tentativa em AlertNotification."""
    from api.config import get_config
    alert_dict = {
        "id": log.id, "severidade": log.severidade, "metrica": log.metrica,
        "mensagem": log.mensagem, "triggered_at": log.triggered_at.isoformat() + "Z",
        "valor_no_disparo": log.valor_no_disparo, "threshold": log.threshold,
    }
    if rule.canal_email:
        if get_config(session, "smtp_enabled") == "1":
            try:
                from notifications.email_service import send_alert
                send_alert(alert_dict, session)
                _record_notification(session, log.id, "email", "disparo", "enviado")
            except Exception as e:
                _record_notification(session, log.id, "email", "disparo", "falhou", str(e))
                logger.exception("Erro ao enviar e-mail de alerta")
        else:
            _record_notification(session, log.id, "email", "disparo", "desabilitado")
    if rule.canal_whatsapp:
        if get_config(session, "evolution_enabled") == "1":
            try:
                from notifications.whatsapp_service import send_alert as wa_send
                wa_send(alert_dict, session)
                _record_notification(session, log.id, "whatsapp", "disparo", "enviado")
            except ImportError:
                pass
            except Exception as e:
                _record_notification(session, log.id, "whatsapp", "disparo", "falhou", str(e))
                logger.exception("Erro ao enviar WhatsApp de alerta")
        else:
            _record_notification(session, log.id, "whatsapp", "disparo", "desabilitado")
    log.last_notified_at = now


def _notify_resolution(session: Session, log: AlertLog, rule: AlertRule):
    """Dispara notificação de resolução e grava cada tentativa em AlertNotification."""
    from api.config import get_config
    alert_dict = {
        "id": log.id, "severidade": log.severidade, "metrica": log.metrica,
        "mensagem": log.mensagem, "triggered_at": log.triggered_at.isoformat() + "Z",
        "resolved_at": log.resolved_at.isoformat() + "Z" if log.resolved_at else None,
    }
    if rule.canal_email:
        if get_config(session, "smtp_enabled") == "1":
            try:
                from notifications.email_service import send_resolution
                send_resolution(alert_dict, session)
                _record_notification(session, log.id, "email", "resolucao", "enviado")
            except Exception as e:
                _record_notification(session, log.id, "email", "resolucao", "falhou", str(e))
                logger.exception("Erro ao enviar e-mail de resolução")
        else:
            _record_notification(session, log.id, "email", "resolucao", "desabilitado")
    if rule.canal_whatsapp:
        if get_config(session, "evolution_enabled") == "1":
            try:
                from notifications.whatsapp_service import send_resolution as wa_res
                wa_res(alert_dict, session)
                _record_notification(session, log.id, "whatsapp", "resolucao", "enviado")
            except ImportError:
                pass
            except Exception as e:
                _record_notification(session, log.id, "whatsapp", "resolucao", "falhou", str(e))
                logger.exception("Erro ao enviar WhatsApp de resolução")
        else:
            _record_notification(session, log.id, "whatsapp", "resolucao", "desabilitado")
```

- [ ] **Step 4: Rodar a suíte inteira de alert_engine e confirmar que passa**

Run: `cd backend && python -m pytest tests/test_alert_engine.py -v`
Expected: PASS (todos, incluindo os testes pré-existentes — `add_rule` já usa `duracao_minutos=0` e `canal_email=0, canal_whatsapp=0` como default, então nenhum teste antigo dispara envio real)

- [ ] **Step 5: Commit**

```bash
git add backend/notifications/alert_engine.py backend/tests/test_alert_engine.py
git commit -m "fix: notifica alerta já no ciclo de criação e grava histórico de tentativas"
```

---

### Task 3: Fix "Container Parado" nunca notifica

**Files:**
- Modify: `backend/notifications/alert_engine.py:194-259` (`_evaluate_container_stopped`)
- Test: `backend/tests/test_alert_engine.py`

**Interfaces:**
- Consumes: `_notify_alert`, `_notify_resolution` (Task 2, assinaturas inalteradas).

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `backend/tests/test_alert_engine.py`:

```python
def test_container_parado_notifica_ao_criar_alerta(fresh_db):
    from notifications.alert_engine import evaluate
    enable_channels(fresh_db)
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1,
             canal_whatsapp=1, canal_email=0)
    with patch("notifications.whatsapp_service.send_alert") as mock_send:
        asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "exited"}]))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.metrica == "container_stopped").first()
    notifs = get_notifications(fresh_db, log.id)
    assert len(notifs) == 1
    assert notifs[0].status == "enviado"
    assert notifs[0].tipo == "disparo"
    mock_send.assert_called_once()


def test_container_parado_notifica_resolucao(fresh_db):
    from notifications.alert_engine import evaluate
    enable_channels(fresh_db)
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1,
             canal_whatsapp=1, canal_email=0)
    with patch("notifications.whatsapp_service.send_alert"):
        asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "exited"}]))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.metrica == "container_stopped").first()
    with patch("notifications.whatsapp_service.send_resolution") as mock_res:
        asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "running"}]))
    resolucoes = [n for n in get_notifications(fresh_db, log.id) if n.tipo == "resolucao"]
    assert len(resolucoes) == 1
    assert resolucoes[0].status == "enviado"
    mock_res.assert_called_once()
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && python -m pytest tests/test_alert_engine.py -k container_parado_notifica -v`
Expected: FAIL (`assert len(notifs) == 1` falha com 0 — `_evaluate_container_stopped` nunca chama `_notify_alert`)

- [ ] **Step 3: Implementar o fix**

Em `backend/notifications/alert_engine.py`, substituir a função `_evaluate_container_stopped` (linhas 194–259) por:

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

            open_log = AlertLog(
                rule_id=rule.id,
                triggered_at=now,
                severidade=rule.severidade,
                metrica="container_stopped",
                valor_no_disparo=1,
                threshold=1,
                mensagem=container_mensagem,
                vps_name=vps_name,
                contexto=json.dumps(contexto) if contexto else None,
            )
            session.add(open_log)
            session.flush()  # garante open_log.id para o FK de AlertNotification

        duration_ok = rule.duracao_minutos == 0 or (
            (now - open_log.triggered_at).total_seconds() / 60 >= rule.duracao_minutos
        )
        cooldown_ok = (
            open_log.last_notified_at is None or
            (now - open_log.last_notified_at).total_seconds() / 60 >= rule.cooldown_minutos
        )
        if duration_ok and cooldown_ok:
            _notify_alert(session, open_log, rule, now)

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
            _notify_resolution(session, log, rule)
```

- [ ] **Step 4: Rodar a suíte inteira de alert_engine e confirmar que passa**

Run: `cd backend && python -m pytest tests/test_alert_engine.py -v`
Expected: PASS (todos — os testes pré-existentes de `container_stopped` usam `canal_email=0, canal_whatsapp=0` via `add_rule` default, então `_notify_alert`/`_notify_resolution` viram no-op e não alteram o comportamento já coberto)

- [ ] **Step 5: Commit**

```bash
git add backend/notifications/alert_engine.py backend/tests/test_alert_engine.py
git commit -m "fix: alerta de container parado passa a notificar disparo e resolução"
```

---

### Task 4: Expor `notificacoes` na API de alertas

**Files:**
- Modify: `backend/api/alerts.py:10` (import), `:84-92` (`active_alerts`), `:95-120` (`alert_history`), `:123-136` (`_log_dict`)
- Test: `backend/tests/test_alerts_api.py`

**Interfaces:**
- Consumes: `AlertNotification` (Task 1).
- Produces: campo `notificacoes: list[{canal, tipo, status, erro, tentativa_em}]` em cada objeto retornado por `GET /api/alerts/active` e `GET /api/alerts/history`.

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao final de `backend/tests/test_alerts_api.py`:

```python
def test_active_alerts_inclui_notificacoes(client):
    import models.database as db_module
    from datetime import datetime
    from api.auth import create_token
    with db_module.Session(db_module.engine) as s:
        log = db_module.AlertLog(
            rule_id=None, triggered_at=datetime.utcnow(), severidade="critico",
            metrica="disk_percent", mensagem="teste",
        )
        s.add(log)
        s.commit()
        s.refresh(log)
        s.add(db_module.AlertNotification(
            alert_log_id=log.id, canal="whatsapp", tipo="disparo",
            status="enviado", tentativa_em=datetime.utcnow(),
        ))
        s.commit()

    headers = {"Authorization": f"Bearer {create_token('admin')}"}
    r = client.get("/api/alerts/active", headers=headers)
    assert r.status_code == 200
    notifs = r.json()[0]["notificacoes"]
    assert len(notifs) == 1
    assert notifs[0]["canal"] == "whatsapp"
    assert notifs[0]["status"] == "enviado"


def test_history_alerta_sem_notificacoes_retorna_lista_vazia(client):
    import models.database as db_module
    from datetime import datetime
    from api.auth import create_token
    with db_module.Session(db_module.engine) as s:
        s.add(db_module.AlertLog(
            rule_id=None, triggered_at=datetime.utcnow(), severidade="aviso",
            metrica="temperature_c", mensagem="sem notificacao",
        ))
        s.commit()

    headers = {"Authorization": f"Bearer {create_token('admin')}"}
    r = client.get("/api/alerts/history", headers=headers)
    assert r.status_code == 200
    assert r.json()[0]["notificacoes"] == []
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && python -m pytest tests/test_alerts_api.py -k notificacoes -v`
Expected: FAIL (`KeyError: 'notificacoes'`)

- [ ] **Step 3: Implementar**

Em `backend/api/alerts.py`, linha 10, alterar o import:

```python
from models.database import AlertLog, AlertNotification, AlertRule, get_session
```

Substituir a função `_log_dict` (linhas 123–136) por:

```python
def _log_dict(session: Session, a: AlertLog) -> dict:
    notifs = (
        session.query(AlertNotification)
        .filter(AlertNotification.alert_log_id == a.id)
        .order_by(AlertNotification.tentativa_em.desc())
        .all()
    )
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
        "notificacoes": [
            {
                "canal": n.canal, "tipo": n.tipo, "status": n.status,
                "erro": n.erro, "tentativa_em": n.tentativa_em.isoformat() + "Z",
            }
            for n in notifs
        ],
    }
```

Atualizar as duas chamadas a `_log_dict` para passar `session`:

Em `active_alerts` (linha 92): trocar `return [_log_dict(a) for a in logs]` por `return [_log_dict(session, a) for a in logs]`.

Em `alert_history` (linha 120): trocar `return [_log_dict(a) for a in logs]` por `return [_log_dict(session, a) for a in logs]`.

- [ ] **Step 4: Rodar a suíte inteira de alerts_api e confirmar que passa**

Run: `cd backend && python -m pytest tests/test_alerts_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/alerts.py backend/tests/test_alerts_api.py
git commit -m "feat: API de alertas passa a retornar histórico de notificações"
```

---

### Task 5: Retenção de `AlertNotification`

**Files:**
- Modify: `backend/collector/scheduler.py:11` (import), `:117-124` (`_cleanup`)
- Test: `backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `AlertNotification` (Task 1).

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao final de `backend/tests/test_scheduler.py`:

```python
@pytest.mark.asyncio
async def test_cleanup_remove_alert_notification_antigo(test_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    from datetime import datetime, timedelta
    from sqlalchemy.orm import Session

    with Session(test_db.engine) as session:
        log = test_db.AlertLog(
            rule_id=None, triggered_at=datetime.utcnow(), severidade="aviso",
            metrica="disk_percent", mensagem="teste",
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        session.add(test_db.AlertNotification(
            alert_log_id=log.id, canal="whatsapp", tipo="disparo",
            status="enviado", tentativa_em=datetime.utcnow() - timedelta(days=40),
        ))
        session.add(test_db.AlertNotification(
            alert_log_id=log.id, canal="whatsapp", tipo="disparo",
            status="enviado", tentativa_em=datetime.utcnow(),
        ))
        session.commit()

    import importlib
    import collector.scheduler as sched
    importlib.reload(sched)
    await sched._cleanup()

    with Session(test_db.engine) as session:
        rows = session.query(test_db.AlertNotification).all()
    assert len(rows) == 1
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd backend && python -m pytest tests/test_scheduler.py -k alert_notification_antigo -v`
Expected: FAIL (`assert len(rows) == 1` falha com 2 — nada limpa a tabela ainda)

- [ ] **Step 3: Implementar**

Em `backend/collector/scheduler.py`, linha 11, alterar o import:

```python
from models.database import AccessLog, AccessLogDaily, AccessLogHourly, AlertNotification, ContainerDiskUsage, ContainerMetrics, MetricsHistory, engine
```

Em `_cleanup()`, logo após a linha `session.query(AccessLogDaily).filter(AccessLogDaily.day < aggregated_cutoff.strftime("%Y-%m-%d")).delete()` (linha 123) e antes de `session.commit()`, adicionar:

```python
        session.query(AlertNotification).filter(AlertNotification.tentativa_em < aggregated_cutoff).delete()
```

- [ ] **Step 4: Rodar a suíte inteira de scheduler e confirmar que passa**

Run: `cd backend && python -m pytest tests/test_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa do backend**

Run: `cd backend && python -m pytest -v`
Expected: PASS (todos os testes do backend, confirmando que nada quebrou nas tasks 1–5)

- [ ] **Step 6: Commit**

```bash
git add backend/collector/scheduler.py backend/tests/test_scheduler.py
git commit -m "feat: retenção automática de AlertNotification (retention_aggregated_days)"
```

---

### Task 6: Componente de notificações no frontend

**Files:**
- Create: `frontend/components/AlertNotifications.tsx`

**Interfaces:**
- Produces: `interface AlertNotificacao { canal: 'email' | 'whatsapp'; tipo: 'disparo' | 'resolucao'; status: 'enviado' | 'falhou' | 'desabilitado'; erro: string | null; tentativa_em: string }`, `AlertNotificationsCompact({ notificacoes }: { notificacoes: AlertNotificacao[] })`, `AlertNotificationsDetailed({ notificacoes }: { notificacoes: AlertNotificacao[] })`.

- [ ] **Step 1: Criar o componente**

Criar `frontend/components/AlertNotifications.tsx`:

```tsx
'use client'

export interface AlertNotificacao {
  canal: 'email' | 'whatsapp'
  tipo: 'disparo' | 'resolucao'
  status: 'enviado' | 'falhou' | 'desabilitado'
  erro: string | null
  tentativa_em: string
}

const CANAL_ICON: Record<string, string> = { email: '✉️', whatsapp: '📱' }
const CANAL_LABEL: Record<string, string> = { email: 'E-mail', whatsapp: 'WhatsApp' }
const STATUS_COLOR: Record<string, string> = {
  enviado: 'var(--success)', falhou: 'var(--danger)', desabilitado: 'var(--muted)',
}
const STATUS_LABEL: Record<string, string> = {
  enviado: 'Enviado', falhou: 'Falhou', desabilitado: 'Desabilitado',
}

function formatDt(iso: string): string {
  return new Date(iso).toLocaleString('pt-BR')
}

function ultimaPorCanal(notificacoes: AlertNotificacao[]): AlertNotificacao[] {
  const porCanal = new Map<string, AlertNotificacao>()
  for (const n of notificacoes) {
    const atual = porCanal.get(n.canal)
    if (!atual || n.tentativa_em > atual.tentativa_em) porCanal.set(n.canal, n)
  }
  return Array.from(porCanal.values())
}

export function AlertNotificationsCompact({ notificacoes }: { notificacoes: AlertNotificacao[] }) {
  if (notificacoes.length === 0) return null
  return (
    <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
      {ultimaPorCanal(notificacoes).map(n => (
        <span
          key={n.canal}
          title={n.erro ?? STATUS_LABEL[n.status]}
          style={{ fontSize: 12, color: STATUS_COLOR[n.status] ?? 'var(--muted)' }}
        >
          {CANAL_ICON[n.canal] ?? n.canal} {STATUS_LABEL[n.status] ?? n.status}
        </span>
      ))}
    </div>
  )
}

export function AlertNotificationsDetailed({ notificacoes }: { notificacoes: AlertNotificacao[] }) {
  if (notificacoes.length === 0) {
    return <span style={{ color: 'var(--muted)' }}>Nenhuma notificação configurada para esta regra.</span>
  }
  return (
    <div style={{ display: 'grid', gap: 6 }}>
      <strong>Notificações</strong>
      {notificacoes.map((n, i) => (
        <div key={i} style={{ fontSize: 12, display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <span>{CANAL_ICON[n.canal] ?? n.canal}</span>
          <span>{CANAL_LABEL[n.canal] ?? n.canal}</span>
          <span style={{ color: 'var(--muted)' }}>({n.tipo === 'disparo' ? 'disparo' : 'resolução'})</span>
          <span style={{ color: STATUS_COLOR[n.status] ?? 'var(--muted)', fontWeight: 600 }}>
            {STATUS_LABEL[n.status] ?? n.status}
          </span>
          <span style={{ color: 'var(--muted)' }}>{formatDt(n.tentativa_em)}</span>
          {n.erro && <span style={{ color: 'var(--danger)' }}>— {n.erro}</span>}
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 2: Verificar que compila**

Run: `cd frontend && npm run build`
Expected: build sem erros (o componente ainda não é importado em lugar nenhum, então só valida a sintaxe/tipos do próprio arquivo)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/AlertNotifications.tsx
git commit -m "feat: componente de exibição do histórico de notificações de alerta"
```

---

### Task 7: Integrar na página de Alertas

**Files:**
- Modify: `frontend/app/alertas/page.tsx:1-21` (imports e interface), `:258-269` (card da aba Ativas), `:320-327` (linha expandida da aba Histórico)

**Interfaces:**
- Consumes: `AlertNotificacao`, `AlertNotificationsCompact`, `AlertNotificationsDetailed` (Task 6); campo `notificacoes` retornado por `/api/alerts/active` e `/api/alerts/history` (Task 4).

- [ ] **Step 1: Importar o componente e estender o tipo**

Em `frontend/app/alertas/page.tsx`, linha 7, adicionar o import (logo abaixo do import de `Toast`):

```tsx
import { AlertNotificationsCompact, AlertNotificationsDetailed, type AlertNotificacao } from '../../components/AlertNotifications'
```

Na `interface AlertLog` (linhas 9–21), adicionar o campo antes do fechamento `}`:

```tsx
  notificacoes: AlertNotificacao[]
```

- [ ] **Step 2: Exibir badges compactos na aba Ativas**

No bloco `{tab === 'ativas' && (...)}`, dentro do `.map(a => ...)` (linhas 258–269), o card atual é:

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

Substituir por:

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
              <AlertNotificationsCompact notificacoes={a.notificacoes} />
            </div>
          ))}
```

- [ ] **Step 3: Exibir lista detalhada na aba Histórico**

No bloco `{tab === 'historico' && (...)}`, a linha expandida atual é:

```tsx
                  {expandedAlert === a.id && (
                    <tr>
                      <td colSpan={7} style={{ background: 'var(--surface)', padding: 16, borderBottom: '1px solid var(--border)', fontSize: 12 }}>
                        {renderContexto(a.contexto)}
                      </td>
                    </tr>
                  )}
```

Substituir por:

```tsx
                  {expandedAlert === a.id && (
                    <tr>
                      <td colSpan={7} style={{ background: 'var(--surface)', padding: 16, borderBottom: '1px solid var(--border)', fontSize: 12 }}>
                        {renderContexto(a.contexto)}
                        <div style={{ marginTop: 12 }}>
                          <AlertNotificationsDetailed notificacoes={a.notificacoes} />
                        </div>
                      </td>
                    </tr>
                  )}
```

- [ ] **Step 4: Verificar que compila**

Run: `cd frontend && npm run build`
Expected: build sem erros

- [ ] **Step 5: Testar manualmente**

Run: `cd frontend && npm run dev` (ou o fluxo de dev já usado no projeto), abrir `/alertas`, checar:
- Aba Ativas: se houver um alerta ativo com `notificacoes`, aparecem badges de canal abaixo da linha "Iniciado".
- Aba Histórico: expandir uma linha mostra a seção "Notificações" com canal, tipo, status e horário; alerta sem notificações mostra a mensagem "Nenhuma notificação configurada para esta regra."

- [ ] **Step 6: Commit**

```bash
git add frontend/app/alertas/page.tsx
git commit -m "feat: exibe histórico de notificações de alerta na tela Alertas"
```

---

## Self-Review

**Cobertura do spec:**
- Bug de notificação perdida (duração 0) → Task 2.
- Bug "Container Parado" nunca notifica → Task 3 (addendum do spec).
- Tabela de histórico de tentativas → Task 1.
- Estados enviado/falhou/desabilitado → Task 2/3.
- API expõe `notificacoes` → Task 4.
- Retenção → Task 5.
- UI (Ativas compacta, Histórico detalhada) → Tasks 6–7.
- Colunas legadas mantidas sem escrita → coberto implicitamente (nenhuma task volta a escrever `notificado_email`/`erro_email`/etc.).

**Sem placeholders, tipos consistentes** entre `_record_notification` (Task 2), `AlertNotification` (Task 1), `_log_dict` (Task 4) e `AlertNotificacao`/componentes (Tasks 6–7) — todos usam os mesmos nomes de campo (`canal`, `tipo`, `status`, `erro`, `tentativa_em`) e os mesmos três valores de `status`.
