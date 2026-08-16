# Alertas Sustentados, Aviso na Subida e Histórico com Contexto — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o monitor avisar quando o load sobe de verdade (e só então), parar de mandar "resolvido" de alertas que nunca foram anunciados, e dar ao usuário como analisar os picos na página `/historico`.

**Architecture:** O motor de alertas passa a confirmar a condição consultando a janela de `duracao_minutos` no `metrics_history` antes de abrir um alerta — sem estado novo em memória nem no schema, porque `collect_and_store` já faz `commit()` da amostra atual (`scheduler.py:65`) antes de chamar `evaluate()` (`scheduler.py:67`). Como o alerta só nasce depois de confirmado, ele já notifica a subida no mesmo ciclo em que é criado. A notificação de resolução ganha uma guarda para nunca falar de um alerta que ficou calado.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy 2 / SQLite (WAL) / pytest — backend. Next.js 16 / React 18 / recharts 2 — frontend.

## Global Constraints

- Spec de referência: `docs/superpowers/specs/2026-08-16-alertas-load-historico-design.md`.
- Trabalhar direto na `main`. Não criar worktree nem branch por tarefa.
- Rodar testes com `py -m pytest` a partir de `monitor/backend` (Windows; `python`/`python3` no bash são stubs da Microsoft Store e não funcionam). Suíte completa leva ~6-7 min.
- Baseline antes de começar: **293 testes passando**. Qualquer teste que ficar vermelho tem que ser explicado, nunca silenciado.
- Não alterar limites de CPU/RAM/Swap/Temperatura. Fora de escopo.
- Não alterar retenção de dados.
- Mensagens de commit em português, sem acento (padrão do repositório).

---

## File Structure

| Arquivo | Responsabilidade | Tarefa |
|---|---|---|
| `backend/notifications/alert_engine.py` | Confirmação de janela + guarda de resolução | 1, 2, 3 |
| `backend/tests/test_alert_engine.py` | Testes do motor | 1, 2, 3 |
| `backend/models/database.py` | Default e migração da regra "Load Alto" | 4 |
| `backend/tests/test_database.py` | Testes da migração | 4 |
| `backend/api/metrics.py` | Série companheira + endpoint de anotações | 5 |
| `backend/tests/test_metrics_api.py` | Testes dos endpoints | 5 |
| `frontend/components/LineChart.tsx` | Threshold, marcadores de alerta, 2ª série | 6 |
| `frontend/app/historico/page.tsx` | Busca as anotações e repassa | 6 |

---

### Task 1: `_condicao_sustentada` — a confirmação de janela

**Files:**
- Modify: `backend/notifications/alert_engine.py` (imports na linha 9; novas constantes e função após o bloco `_OPERATORS`, linha ~19)
- Test: `backend/tests/test_alert_engine.py`

**Interfaces:**
- Consumes: `_OPERATORS` (já existe em `alert_engine.py:13`), `MetricsHistory` (de `models.database`)
- Produces: `_condicao_sustentada(session: Session, rule: AlertRule, now: datetime) -> bool` e a constante `_TOLERANCIA_JANELA_S: int`. A Task 2 chama essa função.

- [ ] **Step 1: Escrever o helper de seed e os testes que falham**

Adicionar no topo de `backend/tests/test_alert_engine.py`, junto dos imports existentes:

```python
from datetime import timedelta
from models.database import MetricsHistory
```

E depois de `count_open()` (linha ~47), o helper e os testes:

```python
def seed_history(engine, valores, *, coluna="load_1m", now=None, intervalo_s=30):
    """Grava amostras espacadas de intervalo_s terminando em `now`.

    valores[0] eh a amostra MAIS ANTIGA, valores[-1] a mais recente.
    Com intervalo de 30s (o real do scheduler), N valores cobrem
    (N-1)*30 segundos de janela.
    """
    now = now or datetime.utcnow()
    with Session(engine) as s:
        for i, v in enumerate(reversed(valores)):
            s.add(MetricsHistory(
                collected_at=now - timedelta(seconds=i * intervalo_s),
                **{coluna: v},
            ))
        s.commit()
    return now


def load_rule(engine, **kwargs):
    defaults = dict(metrica="load_1m", operador=">", threshold=6.0, duracao_minutos=3)
    defaults.update(kwargs)
    return add_rule(engine, **defaults)


def sustentada(engine, rule_id, now):
    from notifications.alert_engine import _condicao_sustentada
    from models.database import AlertRule
    with Session(engine) as s:
        rule = s.get(AlertRule, rule_id)
        return _condicao_sustentada(s, rule, now)


def test_sustentada_quando_janela_toda_acima(fresh_db):
    # 7 amostras x 30s = 180s = os 3 min exigidos
    now = seed_history(fresh_db, [7.0] * 7)
    rule_id = load_rule(fresh_db)
    assert sustentada(fresh_db, rule_id, now) is True


def test_nao_sustentada_com_queda_no_meio(fresh_db):
    now = seed_history(fresh_db, [7.0, 7.0, 5.0, 7.0, 7.0, 7.0, 7.0])
    rule_id = load_rule(fresh_db)
    assert sustentada(fresh_db, rule_id, now) is False


def test_nao_sustentada_sem_amostras(fresh_db):
    rule_id = load_rule(fresh_db)
    assert sustentada(fresh_db, rule_id, datetime.utcnow()) is False


def test_nao_sustentada_quando_janela_mal_coberta(fresh_db):
    # Backend recem-subido: so 1 min de dados, todos acima. Nao confirma.
    now = seed_history(fresh_db, [7.0, 7.0, 7.0])
    rule_id = load_rule(fresh_db)
    assert sustentada(fresh_db, rule_id, now) is False


def test_sustentada_no_limite_da_tolerancia(fresh_db):
    # 6 amostras x 30s = 150s (2min30s). A coleta e a cada 30s, entao a
    # amostra mais antiga dentro de uma janela de 3 min NUNCA tem 180s
    # exatos — sem tolerancia isso reprovaria sempre.
    now = seed_history(fresh_db, [7.0] * 6)
    rule_id = load_rule(fresh_db)
    assert sustentada(fresh_db, rule_id, now) is True


def test_nao_sustentada_com_valor_nulo(fresh_db):
    now = seed_history(fresh_db, [7.0, 7.0, None, 7.0, 7.0, 7.0, 7.0])
    rule_id = load_rule(fresh_db)
    assert sustentada(fresh_db, rule_id, now) is False


def test_duracao_zero_dispensa_a_janela(fresh_db):
    rule_id = load_rule(fresh_db, duracao_minutos=0)
    assert sustentada(fresh_db, rule_id, datetime.utcnow()) is True


def test_metrica_fora_do_metrics_history_dispensa_a_janela(fresh_db):
    # docker_reclaimable_mb vem do scheduler, access_log_stale_minutos vem do
    # tailer — nenhum dos dois e gravado no metrics_history. Sem este
    # fallback, essas regras nunca disparariam.
    rule_id = load_rule(fresh_db, metrica="docker_reclaimable_mb", duracao_minutos=5)
    assert sustentada(fresh_db, rule_id, datetime.utcnow()) is True
```

- [ ] **Step 2: Rodar para confirmar que falham**

```
cd C:/Users/dougl/workspace9/monitor/backend
py -m pytest tests/test_alert_engine.py -k sustentada -v
```

Esperado: FAIL — `ImportError: cannot import name '_condicao_sustentada'`.

- [ ] **Step 3: Implementar**

Em `backend/notifications/alert_engine.py`, alterar o import da linha 9 para incluir `MetricsHistory`:

```python
from models.database import AlertLog, AlertNotification, AlertRule, ContainerDiskUsage, ContainerMetrics, MetricsHistory, engine
```

E acrescentar logo depois do dicionário `_OPERATORS` (linha ~19):

```python
# Colunas do metrics_history por metrica de regra. Metricas ausentes daqui
# (docker_reclaimable_mb, access_log_stale_minutos, container_*) nao sao
# gravadas nessa tabela e portanto nao tem janela para confirmar.
_METRICA_COLUNA = {
    "cpu_percent": MetricsHistory.cpu_percent,
    "ram_percent": MetricsHistory.ram_percent,
    "disk_percent": MetricsHistory.disk_percent,
    "swap_percent": MetricsHistory.swap_percent,
    "temperature_c": MetricsHistory.temperature_c,
    "load_1m": MetricsHistory.load_1m,
}

# A coleta roda a cada 30s, entao a amostra mais antiga dentro de uma janela
# de N minutos tem tipicamente N*60-30 segundos, nunca N*60 exatos. Sem esta
# folga (~2 ciclos) a checagem de cobertura reprovaria toda janela, sempre.
_TOLERANCIA_JANELA_S = 60


def _condicao_sustentada(session: Session, rule: AlertRule, now: datetime) -> bool:
    """True se a condicao da regra valeu em TODAS as amostras da janela.

    Substitui o antigo `duracao_minutos`, que era inatingivel: o alerta abria
    no primeiro cruzamento e resolvia no primeiro nao-cruzamento, entao a
    janela quase nunca vencia antes do alerta fechar. Ver a spec
    2026-08-16-alertas-load-historico-design.md.
    """
    if rule.duracao_minutos == 0:
        return True
    coluna = _METRICA_COLUNA.get(rule.metrica)
    op = _OPERATORS.get(rule.operador)
    if coluna is None or op is None:
        return True

    inicio = now - timedelta(minutes=rule.duracao_minutos)
    linhas = (
        session.query(MetricsHistory.collected_at, coluna)
        .filter(MetricsHistory.collected_at >= inicio)
        .order_by(MetricsHistory.collected_at)
        .all()
    )
    if not linhas:
        return False

    # A janela precisa estar coberta por dados: sem isto, um backend que
    # subiu ha 1 minuto confirmaria com uma amostra so.
    idade_mais_antiga = (now - linhas[0][0]).total_seconds()
    if idade_mais_antiga < rule.duracao_minutos * 60 - _TOLERANCIA_JANELA_S:
        return False

    return all(valor is not None and op(valor, rule.threshold) for _, valor in linhas)
```

- [ ] **Step 4: Rodar para confirmar que passam**

```
py -m pytest tests/test_alert_engine.py -k sustentada -v
```

Esperado: 8 passed.

- [ ] **Step 5: Rodar o arquivo inteiro (nada pode ter quebrado ainda)**

```
py -m pytest tests/test_alert_engine.py -v
```

Esperado: todos passam. A função ainda não está ligada em lugar nenhum.

- [ ] **Step 6: Commit**

```bash
git add backend/notifications/alert_engine.py backend/tests/test_alert_engine.py
git commit -m "feat: adiciona _condicao_sustentada, confirmacao de janela via metrics_history"
```

---

### Task 2: Ligar a confirmação no `_evaluate_rule` e guardar a resolução

**Files:**
- Modify: `backend/notifications/alert_engine.py:124-169` (`_evaluate_rule`)
- Test: `backend/tests/test_alert_engine.py`

**Interfaces:**
- Consumes: `_condicao_sustentada(session, rule, now) -> bool` (Task 1), `seed_history`, `load_rule` (helpers de teste da Task 1)
- Produces: nada novo; muda o comportamento de `_evaluate_rule`, que é chamado por `evaluate()`, `scheduler.check_docker_cleanup()` e `access_log_tailer._evaluate_log_stale()`.

- [ ] **Step 1: Escrever os testes que falham**

Acrescentar em `backend/tests/test_alert_engine.py`:

```python
def get_notifications(engine, alert_log_id):
    with Session(engine) as s:
        return s.query(AlertNotification).filter_by(alert_log_id=alert_log_id).all()


def test_pico_de_um_ciclo_nao_cria_alerta(fresh_db):
    # O bug original: um blip de 30s virava AlertLog e, no ciclo seguinte,
    # uma notificacao de "resolvido". 109 dos 111 alertas de load em 8 dias
    # de producao foram exatamente isto.
    from notifications.alert_engine import evaluate
    seed_history(fresh_db, [7.0])  # so a amostra do pico
    load_rule(fresh_db)
    asyncio.run(evaluate(make_metrics(load=7.0), []))
    assert count_open(fresh_db) == 0


def test_condicao_sustentada_cria_alerta_e_notifica_a_subida(fresh_db):
    from notifications.alert_engine import evaluate
    seed_history(fresh_db, [7.0] * 7)
    load_rule(fresh_db, canal_whatsapp=1, canal_email=0)
    asyncio.run(evaluate(make_metrics(load=7.0), []))
    assert count_open(fresh_db) == 1
    with Session(fresh_db) as s:
        log = s.query(AlertLog).filter(AlertLog.resolved_at.is_(None)).first()
        assert log.last_notified_at is not None
    disparos = [n for n in get_notifications(fresh_db, log.id) if n.tipo == "disparo"]
    assert len(disparos) == 1


def test_resolucao_de_alerta_notificado_notifica(fresh_db):
    from notifications.alert_engine import evaluate
    seed_history(fresh_db, [7.0] * 7)
    load_rule(fresh_db, canal_whatsapp=1, canal_email=0)
    asyncio.run(evaluate(make_metrics(load=7.0), []))
    with Session(fresh_db) as s:
        log_id = s.query(AlertLog).first().id
    asyncio.run(evaluate(make_metrics(load=2.0), []))
    resolucoes = [n for n in get_notifications(fresh_db, log_id) if n.tipo == "resolucao"]
    assert len(resolucoes) == 1


def test_resolucao_de_alerta_nunca_notificado_e_silenciosa_mas_grava_resolved_at(fresh_db):
    """Alerta deixado aberto pelo codigo ANTIGO (last_notified_at NULL) nao
    pode gerar um 'resolvido' no deploy. resolved_at ainda tem que ser
    gravado — a tela de historico nao pode mentir sobre o estado."""
    from notifications.alert_engine import evaluate
    rule_id = load_rule(fresh_db, canal_whatsapp=1, canal_email=0)
    with Session(fresh_db) as s:
        s.add(AlertLog(
            rule_id=rule_id, triggered_at=datetime.utcnow(), severidade="aviso",
            metrica="load_1m", valor_no_disparo=7.0, threshold=6.0,
            mensagem="Load Alto: 7.0 > 6.0", last_notified_at=None,
        ))
        s.commit()
        log_id = s.query(AlertLog).first().id

    asyncio.run(evaluate(make_metrics(load=2.0), []))

    with Session(fresh_db) as s:
        log = s.get(AlertLog, log_id)
        assert log.resolved_at is not None
    resolucoes = [n for n in get_notifications(fresh_db, log_id) if n.tipo == "resolucao"]
    assert resolucoes == []
```

> Se `get_notifications` já existir no arquivo, não duplicar — reaproveitar a existente.

- [ ] **Step 2: Rodar para confirmar que falham**

```
py -m pytest tests/test_alert_engine.py -k "pico_de_um_ciclo or sustentada_cria_alerta or resolucao_de_alerta" -v
```

Esperado: `test_pico_de_um_ciclo_nao_cria_alerta` FAIL (`assert 1 == 0`) e `test_resolucao_de_alerta_nunca_notificado...` FAIL (`assert [<AlertNotification>] == []`).

- [ ] **Step 3: Implementar**

Substituir o corpo de `_evaluate_rule` a partir de `if condition_true and open_log is None:` (linha 137) até o fim da função (linha 169) por:

```python
    if condition_true and open_log is None:
        # A janela e confirmada ANTES de abrir o alerta. Assim o alerta so
        # existe quando a duracao ja foi cumprida — e por isso ele pode
        # notificar a subida no mesmo ciclo em que nasce.
        if not _condicao_sustentada(session, rule, now):
            return
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

    if condition_true and open_log is not None:
        cooldown_ok = (
            open_log.last_notified_at is None or
            (now - open_log.last_notified_at).total_seconds() / 60 >= rule.cooldown_minutos
        )
        if cooldown_ok:
            _notify_alert(session, open_log, rule, now)
    elif not condition_true and open_log is not None:
        open_log.resolved_at = now
        # So fala da resolucao se chegou a falar da subida. Sem esta guarda,
        # todo blip virava uma mensagem "ALERTA RESOLVIDO — Load Alto: 7.3 >
        # 6.0" para um alerta que nunca foi anunciado.
        if open_log.last_notified_at is not None:
            _notify_resolution(session, open_log, rule)
```

O bloco `duration_ok` (linhas 154-160 do original) sai: quando o alerta existe, a duração já foi satisfeita por construção.

- [ ] **Step 4: Rodar para confirmar que passam**

```
py -m pytest tests/test_alert_engine.py -k "pico_de_um_ciclo or sustentada_cria_alerta or resolucao_de_alerta" -v
```

Esperado: 4 passed.

- [ ] **Step 5: Rodar o arquivo inteiro**

```
py -m pytest tests/test_alert_engine.py -v
```

Esperado: todos passam. Os testes antigos usam `duracao_minutos=0` (default de `add_rule`, linha 34), que cai no atalho de `_condicao_sustentada` — por isso não quebram. Se algum quebrar, **parar e relatar** em vez de ajustar o teste no reflexo.

- [ ] **Step 6: Commit**

```bash
git add backend/notifications/alert_engine.py backend/tests/test_alert_engine.py
git commit -m "fix: alerta so abre apos janela confirmada e resolucao so notifica se a subida foi notificada"
```

---

### Task 3: Mesma guarda nas regras especiais

**Files:**
- Modify: `backend/notifications/alert_engine.py:319-320` (`_evaluate_container_stopped`) e `:412-413` (`_evaluate_restart_loop`)
- Test: `backend/tests/test_alert_engine.py`

**Interfaces:**
- Consumes: nada da Task 2 além do padrão já estabelecido.
- Produces: nada novo.

**Contexto para quem implementa:** em `_evaluate_container_stopped` isto é um bug vivo — a notificação de disparo é condicionada a `duration_ok` (linha 300), então uma regra `container_stopped` com `duracao_minutos > 0` cria o alerta calado e depois manda "resolvido". Em `_evaluate_restart_loop` a notificação de disparo sempre acontece na criação, então a guarda é defesa em profundidade e cobre alertas deixados abertos pelo código antigo.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_container_parado_nao_notifica_resolucao_se_nao_notificou_queda(fresh_db):
    from notifications.alert_engine import evaluate
    # duracao_minutos=2 faz duration_ok ser falso na criacao: o alerta nasce
    # calado. Sem a guarda, a volta do container mandaria um "resolvido"
    # de um alerta que nunca foi anunciado.
    add_rule(fresh_db, metrica="container_stopped", operador="==", threshold=1,
             duracao_minutos=2, canal_whatsapp=1, canal_email=0)
    asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "exited"}]))
    with Session(fresh_db) as s:
        log = s.query(AlertLog).first()
        assert log.last_notified_at is None
        log_id = log.id

    asyncio.run(evaluate(make_metrics(), [{"name": "nginx", "status": "running"}]))

    with Session(fresh_db) as s:
        assert s.get(AlertLog, log_id).resolved_at is not None
    resolucoes = [n for n in get_notifications(fresh_db, log_id) if n.tipo == "resolucao"]
    assert resolucoes == []


def test_restart_loop_nao_notifica_resolucao_se_nao_notificou_disparo(fresh_db):
    from notifications.alert_engine import evaluate
    rule_id = add_rule(fresh_db, metrica="container_restart_loop", operador=">=",
                       threshold=3, duracao_minutos=10, cooldown_minutos=30,
                       canal_whatsapp=1, canal_email=0)
    with Session(fresh_db) as s:
        s.add(AlertLog(
            rule_id=rule_id, triggered_at=datetime.utcnow(), severidade="critico",
            metrica="container_restart_loop", valor_no_disparo=3, threshold=3,
            mensagem="Container 'web' em restart loop (3 reinicios em 10min)",
            last_notified_at=None,
        ))
        s.commit()
        log_id = s.query(AlertLog).first().id

    # Nenhum container em loop agora -> o alerta aberto deve resolver
    asyncio.run(evaluate(make_metrics(), []))

    with Session(fresh_db) as s:
        assert s.get(AlertLog, log_id).resolved_at is not None
    resolucoes = [n for n in get_notifications(fresh_db, log_id) if n.tipo == "resolucao"]
    assert resolucoes == []
```

- [ ] **Step 2: Rodar para confirmar que falham**

```
py -m pytest tests/test_alert_engine.py -k "nao_notifica_resolucao" -v
```

Esperado: 2 FAIL, ambos em `assert resolucoes == []`.

- [ ] **Step 3: Implementar**

Em `_evaluate_container_stopped`, no laço final (linha ~318-320), trocar:

```python
        if container_name in running_names or container_name not in known_names:
            log.resolved_at = now
            _notify_resolution(session, log, rule)
```

por:

```python
        if container_name in running_names or container_name not in known_names:
            log.resolved_at = now
            # Nao anuncia a volta de um alerta cuja queda nunca foi anunciada.
            if log.last_notified_at is not None:
                _notify_resolution(session, log, rule)
```

Em `_evaluate_restart_loop`, no laço final (linha ~411-413), trocar:

```python
        if m.group(1) not in containers_em_loop:
            log.resolved_at = now
            _notify_resolution(session, log, rule)
```

por:

```python
        if m.group(1) not in containers_em_loop:
            log.resolved_at = now
            # Idem: resolucao so fala se houve disparo falado.
            if log.last_notified_at is not None:
                _notify_resolution(session, log, rule)
```

- [ ] **Step 4: Rodar para confirmar que passam**

```
py -m pytest tests/test_alert_engine.py -k "nao_notifica_resolucao" -v
```

Esperado: 2 passed.

- [ ] **Step 5: Rodar o arquivo inteiro**

```
py -m pytest tests/test_alert_engine.py -v
```

Esperado: todos passam, incluindo `test_container_parado_notifica_resolucao` (linha ~536), que usa `duracao_minutos=0` e portanto notifica a queda antes de resolver.

- [ ] **Step 6: Commit**

```bash
git add backend/notifications/alert_engine.py backend/tests/test_alert_engine.py
git commit -m "fix: resolucao de container parado e restart loop so notifica se houve disparo notificado"
```

---

### Task 4: Regra "Load Alto" com confirmação de 3 minutos

**Files:**
- Modify: `backend/models/database.py:295` (`_DEFAULT_RULES`) e o bloco `with Session(engine)` de `init_db()` (após a inserção da regra "Access Log Parado")
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Consumes: nada.
- Produces: nada. Só muda dados.

- [ ] **Step 1: Escrever os testes que falham**

Acrescentar em `backend/tests/test_database.py`:

```python
def test_regra_load_alto_usa_confirmacao_de_3_minutos(test_db):
    with Session(test_db.engine) as session:
        regra = session.query(test_db.AlertRule).filter_by(nome="Load Alto").first()
    assert regra.duracao_minutos == 3


def test_migracao_ajusta_load_alto_de_5_para_3(test_db):
    # Banco de producao nasceu com 5, que na pratica nunca era atingido.
    with Session(test_db.engine) as session:
        regra = session.query(test_db.AlertRule).filter_by(nome="Load Alto").first()
        regra.duracao_minutos = 5
        session.commit()

    test_db.init_db()

    with Session(test_db.engine) as session:
        regra = session.query(test_db.AlertRule).filter_by(nome="Load Alto").first()
    assert regra.duracao_minutos == 3


def test_migracao_preserva_ajuste_manual_do_usuario(test_db):
    with Session(test_db.engine) as session:
        regra = session.query(test_db.AlertRule).filter_by(nome="Load Alto").first()
        regra.duracao_minutos = 7
        session.commit()

    test_db.init_db()

    with Session(test_db.engine) as session:
        regra = session.query(test_db.AlertRule).filter_by(nome="Load Alto").first()
    assert regra.duracao_minutos == 7
```

- [ ] **Step 2: Rodar para confirmar que falham**

```
py -m pytest tests/test_database.py -k "load_alto or migracao_ajusta or migracao_preserva" -v
```

Esperado: `test_regra_load_alto_usa_confirmacao_de_3_minutos` FAIL (`assert 5 == 3`) e `test_migracao_ajusta_load_alto_de_5_para_3` FAIL.

- [ ] **Step 3: Implementar**

Em `backend/models/database.py`, linha 295, trocar `"duracao_minutos": 5` por `"duracao_minutos": 3` na regra "Load Alto":

```python
    {"nome": "Load Alto", "metrica": "load_1m", "operador": ">", "threshold": 6.0, "duracao_minutos": 3, "severidade": "aviso", "cooldown_minutos": 30},
```

E acrescentar no bloco `with Session(engine) as session:` de `init_db()`, logo antes do `session.commit()` final:

```python
        # "Load Alto" nasceu com duracao_minutos=5, inatingivel na pratica:
        # em 7 dias de producao nenhum episodio acima do threshold passou de
        # 3 minutos, entao a regra nunca notificava a subida. 3 min corta 96%
        # do ruido (109 -> 4 alertas na mesma janela) sem virar silencio.
        # So aplica se ainda estiver no default antigo, para nao sobrescrever
        # ajuste feito pelo usuario na tela de regras.
        regra_load = session.query(AlertRule).filter_by(nome="Load Alto").first()
        if regra_load is not None and regra_load.duracao_minutos == 5:
            regra_load.duracao_minutos = 3
```

- [ ] **Step 4: Rodar para confirmar que passam**

```
py -m pytest tests/test_database.py -k "load_alto or migracao_ajusta or migracao_preserva" -v
```

Esperado: 3 passed.

- [ ] **Step 5: Rodar o arquivo inteiro**

```
py -m pytest tests/test_database.py -v
```

Esperado: todos passam, incluindo `test_regras_padrao_inseridas` (conta 14 regras — a contagem não muda, só um campo).

- [ ] **Step 6: Commit**

```bash
git add backend/models/database.py backend/tests/test_database.py
git commit -m "fix: regra Load Alto passa a confirmar por 3 minutos em vez de 5"
```

---

### Task 5: API — série companheira e endpoint de anotações

**Files:**
- Modify: `backend/api/metrics.py:1-54`
- Test: `backend/tests/test_metrics_api.py`

**Interfaces:**
- Consumes: `METRIC_MAP` (já existe em `api/metrics.py:13`), `AlertLog` e `AlertRule` (de `models.database`)
- Produces:
  - `GET /metrics/history` passa a incluir `value2` em cada ponto quando a métrica tem companheira, e `companion` na raiz.
  - `GET /metrics/history/annotations?metric=&hours=` → `{"metric": str, "regra": str|None, "threshold": float|None, "alertas": [{"triggered_at": str, "resolved_at": str|None, "valor": float, "severidade": str}]}`
  - `_regra_primeira_linha(regras: list) -> AlertRule | None`

A Task 6 consome esses dois formatos.

- [ ] **Step 1: Verificar como os testes de API existentes montam o cliente**

```
py -m pytest tests/test_metrics_api.py -v --collect-only
```

Ler `backend/tests/test_metrics_api.py` e reaproveitar a fixture de cliente autenticado que já existir ali. Não inventar fixture nova.

- [ ] **Step 2: Escrever os testes que falham**

Acrescentar em `backend/tests/test_metrics_api.py`, usando a fixture de cliente autenticado do arquivo (chamada aqui de `auth_client` — ajustar para o nome real):

```python
def test_history_de_load_inclui_serie_companheira(auth_client, ...):
    from models.database import MetricsHistory, engine
    from sqlalchemy.orm import Session
    from datetime import datetime
    with Session(engine) as s:
        s.add(MetricsHistory(collected_at=datetime.utcnow(), load_1m=7.0, load_5m=5.5))
        s.commit()
    r = auth_client.get("/api/metrics/history?metric=load&hours=1")
    assert r.status_code == 200
    body = r.json()
    assert body["companion"] == "load_5m"
    assert body["data"][0]["value"] == 7.0
    assert body["data"][0]["value2"] == 5.5


def test_history_de_cpu_nao_tem_companheira(auth_client, ...):
    r = auth_client.get("/api/metrics/history?metric=cpu&hours=1")
    assert r.json()["companion"] is None


def test_annotations_devolve_threshold_e_alertas(auth_client, ...):
    from models.database import AlertLog, AlertRule, engine
    from sqlalchemy.orm import Session
    from datetime import datetime
    with Session(engine) as s:
        s.add(AlertRule(nome="Load Alto", metrica="load_1m", operador=">",
                        threshold=6.0, duracao_minutos=3, severidade="aviso",
                        cooldown_minutos=30, ativo=1))
        s.add(AlertLog(triggered_at=datetime.utcnow(), severidade="aviso",
                       metrica="load_1m", valor_no_disparo=7.3, threshold=6.0,
                       mensagem="Load Alto: 7.3 > 6.0"))
        s.commit()
    body = auth_client.get("/api/metrics/history/annotations?metric=load&hours=24").json()
    assert body["threshold"] == 6.0
    assert body["regra"] == "Load Alto"
    assert len(body["alertas"]) == 1
    assert body["alertas"][0]["valor"] == 7.3


def test_annotations_sem_regra_devolve_null(auth_client, ...):
    body = auth_client.get("/api/metrics/history/annotations?metric=net_rx&hours=24").json()
    assert body["threshold"] is None
    assert body["alertas"] == []


def test_annotations_escolhe_a_primeira_linha_que_o_usuario_cruza(auth_client, ...):
    from models.database import AlertRule, engine
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        s.add(AlertRule(nome="CPU Critica", metrica="cpu_percent", operador=">",
                        threshold=95.0, duracao_minutos=2, severidade="critico",
                        cooldown_minutos=15, ativo=1))
        s.add(AlertRule(nome="CPU Alta", metrica="cpu_percent", operador=">",
                        threshold=80.0, duracao_minutos=5, severidade="aviso",
                        cooldown_minutos=30, ativo=1))
        s.commit()
    body = auth_client.get("/api/metrics/history/annotations?metric=cpu&hours=24").json()
    assert body["threshold"] == 80.0  # a menor, para operador ">"
```

- [ ] **Step 3: Rodar para confirmar que falham**

```
py -m pytest tests/test_metrics_api.py -k "companheira or annotations" -v
```

Esperado: FAIL — `KeyError: 'companion'` e `404` no endpoint de annotations.

- [ ] **Step 4: Implementar**

Em `backend/api/metrics.py`, acrescentar `AlertLog, AlertRule` ao import da linha 6:

```python
from models.database import AlertLog, AlertRule, ContainerMetrics, MetricsHistory, get_session
```

Depois de `METRIC_MAP` (linha 21):

```python
# Metricas que ganham uma segunda serie no grafico. load_5m ao lado de
# load_1m deixa visivel a diferenca entre um pico curto e uma tendencia real.
METRIC_COMPANION = {"load": "load_5m"}
```

Trocar o `return` de `metrics_history` (linhas 47-54) por:

```python
    companion = METRIC_COMPANION.get(metric)

    def ponto(r):
        p = {"ts": r.collected_at.isoformat() + "Z", "value": getattr(r, col)}
        if companion:
            p["value2"] = getattr(r, companion)
        return p

    return {
        "metric": metric,
        "hours": hours,
        "companion": companion,
        "data": [ponto(r) for r in rows],
    }
```

E acrescentar, logo abaixo:

```python
def _regra_primeira_linha(regras: list):
    """A primeira linha que o usuario cruza: menor threshold para >/>=,
    maior para </<=. Empate ou operadores misturados -> menor id."""
    if not regras:
        return None
    maiores = [r for r in regras if r.operador in (">", ">=")]
    menores = [r for r in regras if r.operador in ("<", "<=")]
    if maiores and not menores:
        return min(maiores, key=lambda r: (r.threshold, r.id))
    if menores and not maiores:
        return max(menores, key=lambda r: (r.threshold, -r.id))
    return min(regras, key=lambda r: r.id)


@metrics_router.get("/metrics/history/annotations")
def metrics_history_annotations(
    metric: str = Query("cpu"),
    hours: int = Query(24),
    auth=Depends(verify_token_header),
    session: Session = Depends(get_session),
):
    """Threshold da regra ativa e disparos do periodo, para desenhar a linha
    d'agua e os marcadores no grafico de /historico."""
    hours = min(hours, 168)
    col = METRIC_MAP.get(metric)
    if col is None:
        return {"metric": metric, "regra": None, "threshold": None, "alertas": []}

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    regras = session.query(AlertRule).filter(
        AlertRule.ativo == 1, AlertRule.metrica == col
    ).all()
    regra = _regra_primeira_linha(regras)

    alertas = (
        session.query(AlertLog)
        .filter(AlertLog.metrica == col, AlertLog.triggered_at >= cutoff)
        .order_by(AlertLog.triggered_at.asc())
        .all()
    )
    return {
        "metric": metric,
        "regra": regra.nome if regra else None,
        "threshold": regra.threshold if regra else None,
        "alertas": [
            {
                "triggered_at": a.triggered_at.isoformat() + "Z",
                "resolved_at": a.resolved_at.isoformat() + "Z" if a.resolved_at else None,
                "valor": a.valor_no_disparo,
                "severidade": a.severidade,
            }
            for a in alertas
        ],
    }
```

- [ ] **Step 5: Rodar para confirmar que passam**

```
py -m pytest tests/test_metrics_api.py -v
```

Esperado: todos passam, incluindo os testes antigos de `/metrics/history` (o campo `value` não mudou; `companion` e `value2` são acréscimos).

- [ ] **Step 6: Commit**

```bash
git add backend/api/metrics.py backend/tests/test_metrics_api.py
git commit -m "feat: history com serie load_5m e endpoint de anotacoes com threshold e disparos"
```

---

### Task 6: Gráfico com linha d'água, marcadores e segunda série

**Files:**
- Modify: `frontend/components/LineChart.tsx`
- Modify: `frontend/app/historico/page.tsx`

**Interfaces:**
- Consumes: `GET /api/metrics/history` (com `value2`/`companion`) e `GET /api/metrics/history/annotations` (Task 5)
- Produces: nada consumido por outras tarefas.

- [ ] **Step 1: Estender o `LineChart`**

Substituir o conteúdo de `frontend/components/LineChart.tsx` por:

```tsx
'use client';
import { useId } from 'react';
import {
  AreaChart, Area, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer,
} from 'recharts';

interface Point { ts: string; value: number | null; value2?: number | null; }
interface AlertRange { start: string; end: string | null; }
interface Props {
  data: Point[];
  color?: string;
  unit?: string;
  label?: string;
  height?: number;
  threshold?: number | null;
  thresholdLabel?: string;
  alertRanges?: AlertRange[];
  series2Label?: string;
}

export default function LineChart({
  data, color = 'var(--accent)', unit = '%', label, height = 180,
  threshold = null, thresholdLabel, alertRanges, series2Label,
}: Props) {
  const uid = useId();
  const gradientId = `gradient-${uid.replace(/:/g, '')}`;

  // Intervalos em que havia alerta aberto. Um alerta sem resolved_at
  // continua aberto agora, entao vale ate o fim da serie.
  const ranges = (alertRanges ?? []).map((r) => [
    Date.parse(r.start),
    r.end ? Date.parse(r.end) : Number.POSITIVE_INFINITY,
  ] as const);

  const formatted = data.map((d) => {
    const t = Date.parse(d.ts);
    const emAlerta = ranges.some(([ini, fim]) => t >= ini && t <= fim);
    return {
      ...d,
      time: d.ts.includes('T')
        ? new Date(d.ts).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
        : new Date(d.ts).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' }),
      // Serie so com os pontos que estavam em alerta — vira o marcador.
      marcador: emAlerta ? d.value : null,
    };
  });

  const temSegunda = series2Label != null && data.some((d) => d.value2 != null);

  return (
    <div>
      {label && (
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>{label}</div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={formatted} margin={{ top: 4, right: 4, bottom: 0, left: -10 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={color} stopOpacity={0.25} />
              <stop offset="95%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="time" stroke="var(--muted)" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis
            stroke="var(--muted)"
            tick={{ fontSize: 10 }}
            tickFormatter={(v) => `${v}${unit}`}
            domain={unit === '%' ? [0, 100] : ['auto', 'auto']}
          />
          <Tooltip
            contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: 'var(--muted)' }}
            formatter={(v: number, nome: string) => [`${v?.toFixed(1)}${unit}`, nome]}
          />
          {threshold != null && (
            <ReferenceLine
              y={threshold}
              stroke="var(--warning)"
              strokeDasharray="6 4"
              label={{
                value: thresholdLabel ?? `limite ${threshold}`,
                position: 'insideTopRight',
                fill: 'var(--warning)',
                fontSize: 10,
              }}
            />
          )}
          <Area
            type="monotone" dataKey="value" name={label || 'valor'} stroke={color} strokeWidth={2}
            fill={`url(#${gradientId})`} dot={false} connectNulls
          />
          {temSegunda && (
            <Line
              type="monotone" dataKey="value2" name={series2Label} stroke={color}
              strokeWidth={1} strokeDasharray="4 3" dot={false} opacity={0.6}
              connectNulls isAnimationActive={false}
            />
          )}
          <Line
            type="monotone" dataKey="marcador" name="em alerta" stroke="none"
            dot={{ r: 3, fill: 'var(--danger)', stroke: 'none' }}
            connectNulls={false} isAnimationActive={false} legendType="none"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
```

> `var(--danger)` e `var(--warning)`: conferir em `frontend/app/globals.css` que ambas existem. Se `--danger` não existir, usar o nome real da variável de erro/vermelho já definida ali.

- [ ] **Step 2: Conferir as variáveis CSS**

```
grep -n "\-\-danger\|\-\-warning" frontend/app/globals.css
```

Ajustar os nomes no componente conforme o que existir de fato.

- [ ] **Step 3: Ligar na página `/historico`**

Em `frontend/app/historico/page.tsx`:

Trocar a interface `Point` (linha 9) por:

```tsx
interface Point { ts: string; value: number | null; value2?: number | null; }
interface Alerta { triggered_at: string; resolved_at: string | null; valor: number; severidade: string; }
```

Acrescentar estado junto dos outros (linha ~41):

```tsx
  const [threshold, setThreshold] = useState<number | null>(null);
  const [regraNome, setRegraNome] = useState<string | null>(null);
  const [alertas, setAlertas] = useState<Alerta[]>([]);
  const [companion, setCompanion] = useState<string | null>(null);
```

Trocar o corpo de `load` (linhas 46-55) por:

```tsx
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const hoursMap: Record<string, number> = { '1h': 1, '6h': 6, '24h': 24, '7d': 168 };
      const hours = hoursMap[range] ?? 24;
      const [serie, anot] = await Promise.all([
        api.get(`/metrics/history?metric=${metric}&hours=${hours}`),
        api.get(`/metrics/history/annotations?metric=${metric}&hours=${hours}`),
      ]);
      setData(serie.data.data ?? []);
      setCompanion(serie.data.companion ?? null);
      setThreshold(anot.data.threshold ?? null);
      setRegraNome(anot.data.regra ?? null);
      setAlertas(anot.data.alertas ?? []);
    } catch {
      setData([]); setCompanion(null); setThreshold(null); setRegraNome(null); setAlertas([]);
    }
    finally { setLoading(false); }
  }, [range, metric]);
```

Trocar a chamada do `<LineChart>` (linha 115) por:

```tsx
          <LineChart
            data={data}
            color={current.color}
            unit={current.unit}
            height={300}
            threshold={threshold}
            thresholdLabel={regraNome ? `${regraNome} (${threshold})` : undefined}
            alertRanges={alertas.map(a => ({ start: a.triggered_at, end: a.resolved_at }))}
            series2Label={companion === 'load_5m' ? 'média 5 min' : undefined}
          />
```

E acrescentar, logo abaixo do bloco do gráfico (depois da `</div>` que fecha o card, linha ~121), uma legenda curta:

```tsx
      {(threshold != null || alertas.length > 0) && (
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 16 }}>
          {threshold != null && <>Linha tracejada = limite do alerta. </>}
          {alertas.length > 0 && <>Pontos vermelhos = alerta disparado ({alertas.length} no período).</>}
        </div>
      )}
```

- [ ] **Step 4: Build do frontend**

```
cd C:/Users/dougl/workspace9/monitor/frontend
npm run build
```

Esperado: build limpo, sem erro de TypeScript.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/LineChart.tsx frontend/app/historico/page.tsx
git commit -m "feat: grafico de historico com linha do limite, marcadores de alerta e media de 5 min"
```

---

### Task 7: Suíte completa, deploy e limpeza do host

**Files:** nenhum arquivo de código. Verificação e operação.

**Interfaces:**
- Consumes: tudo das Tasks 1-6.
- Produces: sistema em produção com o comportamento novo.

- [ ] **Step 1: Rodar a suíte completa**

```
cd C:/Users/dougl/workspace9/monitor/backend
py -m pytest -q
```

Esperado: 293 testes anteriores + ~22 novos, **todos verdes**. Leva ~6-7 min. Se algo falhar, **parar e relatar o output real** — não seguir para o deploy.

- [ ] **Step 2: Push**

```bash
cd C:/Users/dougl/workspace9
git push origin main
```

- [ ] **Step 3: Deploy**

```bash
ssh root@144.91.92.70 "cd /opt/vps-monitor && git pull --ff-only && bash monitor/deploy.sh"
```

- [ ] **Step 4: Confirmar que a migração da regra aplicou**

```bash
ssh root@144.91.92.70 "sqlite3 /var/lib/docker/volumes/vps-monitor_vps_monitor_data/_data/monitor.db \
  \"SELECT nome, threshold, duracao_minutos FROM alert_rules WHERE nome='Load Alto';\""
```

Esperado: `Load Alto|6.0|3`.

- [ ] **Step 5: Confirmar containers saudáveis e CPU normal**

```bash
ssh root@144.91.92.70 "docker ps --filter name=monitor- --format '{{.Names}} {{.Status}}'; docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' | grep monitor-backend"
```

Esperado: os três containers `Up`; CPU do `monitor-backend` abaixo de ~2% (a referência medida em 12/08 foi 0,26%-0,93%). A confirmação de janela acrescenta ~5 queries indexadas por ciclo de 30s; se a CPU subir de forma perceptível, relatar.

- [ ] **Step 6: Backup e remoção dos scripts do host**

```bash
ssh root@144.91.92.70 "set -e
  D=/root/backup-cron-hourly-\$(date +%Y%m%d%H%M%S)
  mkdir -p \$D
  cp -a /etc/cron.hourly/free /etc/cron.hourly/fstrim \$D/
  ls -la \$D
  rm -f /etc/cron.hourly/free /etc/cron.hourly/fstrim
  ls -la /etc/cron.hourly/"
```

Esperado: backup com os dois arquivos; `/etc/cron.hourly/` só com `.placeholder`.

- [ ] **Step 7: Confirmar que o fstrim semanal continua ativo**

```bash
ssh root@144.91.92.70 "systemctl is-enabled fstrim.timer; systemctl list-timers fstrim.timer --no-pager"
```

Esperado: `enabled` e um próximo disparo agendado. O `fstrim` horário era redundante com este, não um substituto.

- [ ] **Step 8: Verificar objetivamente que o drop_caches parou**

Esperar passar o próximo minuto `:17` e então:

```bash
ssh root@144.91.92.70 "LC_ALL=C sar -r | tail -12"
```

Esperado: `kbbuffers` **não** despenca na amostra seguinte ao `:17`. Antes da mudança o valor caía de ~32.000 para ~10.000 toda hora, sem exceção.

- [ ] **Step 9: Confirmar o silêncio e o funcionamento após 24h**

```bash
ssh root@144.91.92.70 "sqlite3 -header -column /var/lib/docker/volumes/vps-monitor_vps_monitor_data/_data/monitor.db \
  \"SELECT date(triggered_at) d, metrica, count(*) n, sum(last_notified_at IS NOT NULL) com_disparo
    FROM alert_log WHERE triggered_at > datetime('now','-2 days') GROUP BY d, metrica;\""
```

Esperado: pouquíssimos alertas de `load_1m` (a projeção pelos dados históricos é ~4 por semana), e **todo alerta criado com `com_disparo = 1`** — nenhum alerta silencioso pode mais existir. Se aparecer alerta com `com_disparo = 0`, há caminho não coberto: investigar antes de encerrar.

- [ ] **Step 10: Atualizar `PROGRESSO.md` e `TAREFAS.md`**

Marcar a tarefa 0 do `TAREFAS.md` como concluída, registrar em `PROGRESSO.md` o resultado medido (alertas por dia antes e depois, CPU do backend, confirmação do `kbbuffers`), e reescrever a seção "Contexto necessário" para o item 1 do backlog (syscursos). Commitar.

---

## Self-Review do plano

**Cobertura da spec:**
- §1 motor / `_condicao_sustentada` → Task 1 ✓
- §1 mudanças no `_evaluate_rule` (abrir só se sustentada, notificar subida na criação, guarda de resolução) → Task 2 ✓
- §1 guarda nas regras especiais → Task 3 ✓
- §2 regra Load 5→3 com migração condicional → Task 4 ✓
- §3a série companheira → Task 5 ✓
- §3b endpoint de anotações, incluindo desempate de threshold → Task 5 ✓
- §3 frontend (threshold, marcadores, 2ª série) → Task 6 ✓
- §4 host → Task 7 ✓
- §Testes → distribuídos nas Tasks 1-5 ✓
- §Riscos (CPU pós-deploy, migração preservando ajuste manual) → Task 7 Step 5 e Task 4 ✓

**Consistência de nomes:** `_condicao_sustentada`, `_TOLERANCIA_JANELA_S`, `_METRICA_COLUNA`, `METRIC_COMPANION`, `_regra_primeira_linha` usados com o mesmo nome em todas as tarefas que os citam. `seed_history`/`load_rule`/`sustentada`/`get_notifications` definidos na Task 1/2 e reusados nas Tasks 2 e 3.

**Pontos que exigem leitura do código real na hora (declarados, não são placeholders):** o nome da fixture de cliente autenticado em `test_metrics_api.py` (Task 5 Step 1) e os nomes das variáveis CSS de cor em `globals.css` (Task 6 Step 2). Ambos têm passo explícito de verificação antes do uso.
