# Fechar Lacunas de Monitoramento de Risco de Instabilidade

## Contexto

Usuário relatou ter subido vários projetos novos na VPS (confirmado em produção: containers rodando pularam de ~30 pra 50 durante esta sessão, incluindo uma nova stack `fotosaas-*`) e perguntou se o monitor está preparado pra identificar uso excessivo e reportar riscos de falha antes que virem instabilidade real.

Levantamento do que já existe hoje (alertas automáticos por email/WhatsApp, via `AlertRule`/`notifications/alert_engine.py`): CPU, RAM, disco, load average, temperatura, container parado, espaço reaproveitável do Docker — todos por limiar simples (threshold cruzado).

Levantamento em produção durante o brainstorming (2026-07-20) encontrou sinais reais already presentes:
- **Swap em 52% de uso** (2.1GB de 4GB) — não monitorado hoje. RAM% sozinho (50% na hora do teste) não deixa isso visível.
- **Disco saltou de 17% pra 45%** em poucos dias, refletindo os projetos novos — ainda dentro do limiar de alerta (80%), mas mostra a velocidade de crescimento.
- `_evaluate_container_stopped` (já existente) **já lê `State.OOMKilled`** do Docker quando um container para — mas só aparece no contexto (JSON) desse alerta específico, não é usado em mais nada.

## Objetivo

Fechar 4 lacunas de monitoramento identificadas, sem adicionar infraestrutura nova (nenhum script no host) — só extensões ao `collector`/`notifications/alert_engine` que já existem:
1. Alerta de uso de swap.
2. Alerta de container em "restart loop" (reinícios repetidos numa janela curta), sinalizando quando o motivo mais provável foi OOM.
3. Atribuição por projeto nos alertas de CPU/RAM já existentes (qual projeto está consumindo mais).

## Fora de escopo

- Detecção de OOM killer via log de kernel (`journalctl -k`/`dmesg`) — descartada nesta rodada. O `Docker.State.OOMKilled` (já lido por `_evaluate_container_stopped`, acessível via o `docker-socket-proxy` já existente) cobre o caso prático relevante (containers), sem precisar de um script novo no host lendo logs do kernel.
- Regra de alerta dedicada por projeto (ex: "projeto X sozinho > 40% da RAM") — decidido enriquecer os alertas já existentes com o top projeto consumidor, em vez de criar um tipo de regra novo.
- Tendência/projeção (ex: "disco vai encher em N dias no ritmo atual") — avaliado como lacuna real, mas fica pra uma rodada futura, por ser mais complexo (precisa de análise de série histórica, não só limiar).
- Qualquer mudança na forma como notificações são enviadas (email/WhatsApp) — as regras novas usam exatamente o mesmo pipeline (`_notify_alert`) já existente.

## Design

### Arquitetura geral

Diferente das últimas 3 features (fail2ban, Traefik, backup/restore), **esta não precisa de nenhum script novo no host**. As 4 lacunas são fechadas inteiramente dentro do container `monitor-backend` já existente, porque os dados necessários já são acessíveis de lá:
- Swap: `/proc/meminfo` (já montado via `PROC_BASE=/host/proc`).
- OOM de container: `docker_client.container_inspect()` (já usado por `_evaluate_container_stopped`, via `docker-socket-proxy`).
- Restart loop: histórico de `ContainerMetrics.restart_count`, já coletado a cada 30s.
- Atribuição por projeto: `agrupar_por_projeto` (`api/_project_grouping.py`, já extraído na feature de backup/restore) sobre os mesmos dados de containers já coletados.

### Swap

`backend/collector/host.py` — nova função, mesmo padrão de `_read_ram`:

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

Incluída no retorno de `collect_host_metrics()` como chave `"swap"`, mesmo nível de `"ram"`.

`backend/models/database.py` — `MetricsHistory` ganha `swap_used_mb` (Float) e `swap_percent` (Float), adicionadas via `ALTER TABLE` dentro de `init_db()` (mesmo padrão try/except já usado pras colunas de `alert_log` — não quebra bancos já existentes em produção).

Duas novas linhas em `_DEFAULT_RULES`: `"Swap Alto"` (`swap_percent`, `>`, `70`, aviso) e `"Swap Crítico"` (`swap_percent`, `>`, `90`, crítico) — mesmo formato das regras de CPU/RAM já existentes, sem lógica nova de avaliação (`_get_metric_value` só precisa de mais um `if metrica == "swap_percent": return metrics.get("swap", {}).get("percent")`).

### Restart loop (com sinalização de OOM)

Nova linha em `_DEFAULT_RULES`: `{"nome": "Container em Restart Loop", "metrica": "container_restart_loop", "operador": ">=", "threshold": 3, "duracao_minutos": 10, "severidade": "critico", "cooldown_minutos": 30}`. Os campos `threshold` (nº de reinícios) e `duracao_minutos` (janela em minutos) são usados como parâmetros reais pela avaliação — editáveis pelo usuário na tela de regras de alerta já existente, sem precisar de deploy pra ajustar sensibilidade.

Nova função em `notifications/alert_engine.py`, mesmo formato de `_evaluate_container_stopped` (uma instância de alerta aberta por container, resolvida quando o padrão para):

```python
async def _evaluate_restart_loop(session: Session, rule: AlertRule, containers: list, now: datetime, vps_name: str, docker_client=None):
    janela_inicio = now - timedelta(minutes=rule.duracao_minutos)
    for c in containers:
        # ContainerMetrics.container_id grava o ID curto (c["id"], 12 chars —
        # ver collector/scheduler.py, `container_id=c["id"]`), não o
        # id_full usado pra inspecionar via API do Docker. Os dois campos
        # têm valores diferentes (id_full tem 64 chars) — usar o errado aqui
        # faz a consulta nunca encontrar nada.
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
```

`evaluate()` ganha um novo `if rule.metrica == "container_restart_loop": await _evaluate_restart_loop(...)`, no mesmo formato do `if` já existente pra `container_stopped`. A resolução (marcar `resolved_at` quando o container parar de reiniciar) **não reaproveita o código de `_evaluate_container_stopped`** (esse já é específico da regra `container_stopped`, filtrado por `rule.id` dela) — `_evaluate_restart_loop` precisa do próprio bloco de resolução, no mesmo formato: ao final da função, busca `AlertLog`s abertos com `rule_id == rule.id` (o da regra de restart loop) cuja mensagem casa com o padrão `"Container '(.+)' em restart loop"`, e resolve os que não bateram mais o critério nesta execução (container não está mais na lista de `aumentos >= rule.threshold`).

### Atribuição por projeto

`_build_metric_context()` ganha uma nova função auxiliar, mesmo formato de `_top_by`:

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

Usada dentro de `_build_metric_context` pra `cpu_percent`/`load_1m` (soma de `cpu_percent` por projeto) e `ram_percent` (soma de `mem_percent` por projeto), adicionando `ctx["top_projetos"]` ao lado do `top_cpu`/`top_mem` (por container) que já existe — os dois níveis (por container e por projeto) ficam disponíveis na mesma mensagem de alerta.

### Frontend

- **Sem UI nova pra configurar as regras** — `Swap Alto/Crítico` e `Container em Restart Loop` aparecem automaticamente na tela de regras de alerta já existente (mesma listagem/edição que já serve CPU/RAM/disco), zero código novo de frontend pra isso.
- **Card de Swap no dashboard** (`frontend/app/page.tsx`): novo `StatCard` (mesmo componente já usado pra RAM/Disco), mostrando `swap.percent` e `used_mb / total_mb`. O payload que alimenta o dashboard (via websocket) é montado em `collector/scheduler.py`, dentro de `collect_and_store()` — precisa incluir `"swap": host["swap"]` no dict `payload` (ao lado de `"ram": host["ram"]`, já existente), e `swap_used_mb`/`swap_percent` no insert de `MetricsHistory` (ao lado de `ram_used_mb`/`ram_percent`, já existentes).

### Testes

Backend (TDD, seguindo os padrões já existentes em `test_host_collector.py`/`test_alert_engine.py` — mock de `/proc/meminfo` via `tmp_path`, mock de `docker_client`/sessão de banco):
- `_read_swap`: parseia `/proc/meminfo` mockado corretamente (total, usado, percentual).
- `MetricsHistory`: novo teste de inserção com as 2 colunas novas (mesmo padrão dos testes de modelo já existentes).
- `_evaluate_restart_loop`: dispara com 3+ aumentos de `restart_count` na janela; não dispara com menos que o threshold; contexto inclui `oom_killed` quando o container inspecionado retorna `OOMKilled: true`; resolve o alerta quando os reinícios param.
- `_top_projetos`: agrupa corretamente por projeto, soma os valores, ordena, ignora `"(sem projeto)"`.
- `_get_metric_value("swap_percent", ...)`: retorna o valor correto do payload de métricas.

Frontend: `npm run build` limpo (card de swap novo). Verificação manual em produção fica por conta do usuário — não há como testar "restart loop de verdade" com segurança sem derrubar um container de propósito, então a confiança vem dos testes unitários + revisão de código, não de um teste end-to-end ao vivo.

## Arquivos afetados

- **Modificado:** `backend/collector/host.py` (`_read_swap`), `backend/models/database.py` (`MetricsHistory` colunas novas + `_DEFAULT_RULES`), `backend/notifications/alert_engine.py` (`_evaluate_restart_loop`, `_top_projetos`, extensão de `_get_metric_value`/`_build_metric_context`/`evaluate`), `backend/collector/scheduler.py` (inclui `swap` no payload e no insert de `MetricsHistory`), `frontend/app/page.tsx` (card de Swap)
- **Novo (testes):** cobertura adicionada aos arquivos de teste já existentes (`test_host_collector.py`, `test_alert_engine.py`, `test_database.py`) — sem novo arquivo de teste dedicado, já que não há endpoint/módulo novo, só extensões de módulos já cobertos.
