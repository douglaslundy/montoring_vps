# Design — Alertas sustentados, aviso na subida e histórico com contexto de picos

Data: 2026-08-16
Status: aprovado pelo usuário

## Problema

O usuário recebia dezenas de mensagens de WhatsApp por dia com o texto `Load Alto: 7.3 > 6.0`. A investigação (registrada em detalhe no `PROGRESSO.md`) encontrou três defeitos empilhados:

1. **Alerta nunca avisa a subida, só a "resolução".** Nos últimos 8 dias, dos 111 alertas de `load_1m`, **109 nunca notificaram o disparo** — mas **100% notificaram a resolução**. O que chega no celular é `✅ ALERTA RESOLVIDO — 📋 Load Alto: 7.3 > 6.0`, que se lê como um alerta de load alto.

2. **`duracao_minutos` é inatingível por construção.** O `AlertLog` abre no primeiro cruzamento e resolve no primeiro não-cruzamento; a janela de 5 minutos quase nunca é satisfeita antes do alerta fechar. Medido em produção (4 dias): 73 episódios acima de 6.0, **zero** com duração ≥5 min, o mais longo com 3,0 min.

3. **O bug é do motor, não da regra de Load.** Em 30 dias: `load_1m` 630/648 alertas sem notificação de disparo (97%), `cpu_percent` 84/89 (94%), `swap_percent` 32/94 (34%). 8 das 14 regras têm `duracao_minutos > 0` e sofrem do mesmo problema.

Contexto agravante (não é defeito de código): o threshold `6.0` fica exatamente no p99 do sinal. Entre 13/08 e 16/08 a mediana do load subiu 13% (2.41→2.72) e os alertas subiram 867% (3→29).

## Objetivos

- Parar o ruído sem tornar o monitor mudo.
- **Avisar quando o load sobe**, não só quando passa.
- Dar ao usuário como analisar os picos por conta própria na página `/historico`.
- Remover, no host, a causa física dos picos.

## Não-objetivos

- Recalibrar limites de CPU/RAM/Swap/Temperatura. O fix do motor já corrige o flapping deles; mudar limite sem evidência de incômodo é especulação.
- Alterar a retenção de dados (7 dias detalhado / 30 agregado).
- Refatorar o `_evaluate_rule` além do necessário para este fim.

---

## 1. Motor de alertas — janela consultada do banco

Arquivo: `backend/notifications/alert_engine.py`

### Por que esta abordagem

`collect_and_store` faz `session.commit()` do `MetricsHistory` (`scheduler.py:65`) **antes** de chamar `evaluate()` (`scheduler.py:67`). Logo, no momento da avaliação a amostra atual já está na tabela, e a janela pode ser consultada em vez de mantida em memória.

Alternativas descartadas:
- **Estado "pendente" em memória:** perde o progresso a cada deploy/restart e é inauditável.
- **Coluna `pending_since` no `AlertLog`:** exige migração e polui a tela de histórico de alertas com linhas que não são alertas.

A opção escolhida não acrescenta estado nenhum ao sistema — e estado mal gerenciado (o `cooldown` guardado por-`AlertLog` em vez de por-regra) é justamente a origem do bug.

### Nova função

```python
_METRICA_COLUNA = {
    "cpu_percent": MetricsHistory.cpu_percent,
    "ram_percent": MetricsHistory.ram_percent,
    "disk_percent": MetricsHistory.disk_percent,
    "swap_percent": MetricsHistory.swap_percent,
    "temperature_c": MetricsHistory.temperature_c,
    "load_1m": MetricsHistory.load_1m,
}

def _condicao_sustentada(session, rule, now) -> bool:
    """True se a condição da regra foi verdadeira em TODAS as amostras da janela."""
```

Regras:
- `duracao_minutos == 0` → devolve `True` (comportamento atual preservado).
- Métrica ausente de `_METRICA_COLUNA` → devolve `True` (fallback explícito). Cobre `docker_reclaimable_mb` (chamado de `scheduler.check_docker_cleanup`) e `access_log_stale_minutos` (chamado de `access_log_tailer`), que passam valores não gravados no `metrics_history`. Ambos têm `duracao_minutos=0`, então na prática nada muda — o fallback existe para que uma regra futura com métrica externa não quebre silenciosamente.
- Janela vazia (nenhuma amostra) → **`False`**. Sem dados não se afirma que a condição se sustentou.
- Qualquer amostra da janela que não satisfaça o operador → `False`. Valor `NULL` na coluna conta como não-satisfeita.
- **Cobertura da janela.** Não basta "todas as amostras existentes satisfazem": se o backend subiu há 1 minuto, a única amostra existente satisfaz e o alerta dispararia sem confirmação real. Exige-se também que os dados alcancem o começo da janela.

  Cuidado: a coleta é a cada 30s, então a amostra mais antiga dentro de uma janela de 3 min tem tipicamente 2min30s, nunca 3min exatos — exigir `mais_antiga <= now - duracao_minutos` reprovaria **sempre**. Por isso a checagem usa uma tolerância explícita:

  ```python
  _TOLERANCIA_JANELA_S = 60  # ~2 ciclos de coleta (30s cada)
  ```

  A janela é considerada coberta se `(now - mais_antiga).total_seconds() >= duracao_minutos * 60 - _TOLERANCIA_JANELA_S`. Caso contrário → `False`.

  A constante é do módulo, com comentário explicando a origem, para não virar número mágico se o intervalo de coleta mudar.

### Mudanças em `_evaluate_rule`

| hoje | depois |
|---|---|
| Abre `AlertLog` no 1º cruzamento | Abre só se `condição E _condicao_sustentada(...)` |
| Notifica disparo só quando a janela vence com o alerta ainda aberto (quase nunca) | Notifica o disparo **no mesmo ciclo em que abre** — a janela já está cumprida por construção |
| `_notify_resolution` incondicional | `_notify_resolution` só se `open_log.last_notified_at is not None` |

`resolved_at` continua sendo gravado **sempre**, inclusive quando a notificação é suprimida — a tela de histórico de alertas não pode mentir sobre o estado.

O bloco `duration_ok` atual (linhas 158-160) deixa de ser necessário: quando o alerta existe, a duração já foi satisfeita. O `cooldown_ok` permanece, governando renotificação de um alerta que continua aberto.

### Mesma trava nas regras especiais

`_evaluate_container_stopped` (linha ~320) e `_evaluate_restart_loop` (linha ~413) também chamam `_notify_resolution` incondicionalmente. Recebem a mesma guarda `last_notified_at is not None`.

> `container_stopped` tem `duracao_minutos=0` e notifica na queda, então na prática já tinha par disparo/resolução — a guarda é defesa em profundidade e consistência, não correção de sintoma observado.

---

## 2. Regra "Load Alto": confirmação de 3 minutos

`backend/models/database.py`: `_DEFAULT_RULES` passa `duracao_minutos` de `5` para `3` na regra `load_1m`, mais um bloco de migração em `init_db()` (mesmo padrão dos `ALTER TABLE`/`CREATE INDEX` existentes).

**A migração só aplica se o valor atual ainda for `5`** — se o usuário tiver ajustado pela tela de regras, o ajuste dele é preservado.

Base da escolha (episódios acima de 6.0 em 7 dias de produção):

| confirmação | alertas em 7 dias |
|---|---|
| nenhuma (hoje) | 109 |
| 1 min | 47 |
| 2 min | 12 |
| **3 min** | **4** |
| 5 min | 0 |

3 minutos corta 96% do ruído sem virar silêncio total. Nenhum pico transitório dos últimos 7 dias durou 3 minutos, e nenhum problema real se resolve sozinho nesse tempo.

---

## 3. Página `/historico` — contexto para analisar picos

A página já existe com gráfico de linha, métrica "Load Avg" e períodos 1h/6h/24h/7d. Falta o que transforma um número solto em diagnóstico: onde está a linha d'água e onde o alarme tocou.

### Backend — `backend/api/metrics.py`

**a) Série companheira.** Novo mapa `METRIC_COMPANION = {"load": "load_5m"}`. Quando existe companheira, cada ponto de `/metrics/history` ganha `value2`. Uma requisição só, sem endpoint novo para a série.

**b) Novo endpoint** `GET /metrics/history/annotations?metric=&hours=`:

```json
{
  "metric": "load",
  "threshold": 6.0,
  "regra": "Load Alto",
  "alertas": [
    {"triggered_at": "...Z", "resolved_at": "...Z", "valor": 7.3, "severidade": "aviso"}
  ]
}
```

`threshold` vem da regra ativa cuja `metrica` bate com `METRIC_MAP[metric]` (os nomes de coluna já coincidem com os nomes de métrica das regras). Sem regra ativa → `threshold: null` e `alertas: []`.

Havendo mais de uma regra ativa para a métrica (ex.: "CPU Alta" 80 e "CPU Crítica" 95), mostra-se a **primeira linha que o usuário cruza**: o menor threshold quando o operador é `>`/`>=`, o maior quando é `<`/`<=`. Empate ou operadores misturados → a de menor `id` (a mais antiga), que é determinístico.

`alertas` vem do `alert_log` filtrado pela janela. O `alert_log` não é limpo pelo `_cleanup` (só `alert_notification` é), então há dado para toda a janela de 7 dias.

### Frontend

`frontend/components/LineChart.tsx` ganha três props **opcionais**, mantendo todos os usos atuais funcionando sem alteração:

- `threshold?: number` → `<ReferenceLine>` tracejada.
- `markers?: {ts: string; label: string}[]` → `<ReferenceDot>` em cada disparo.
- `series2?: {ts: string; value: number|null}[]` + `series2Label?: string` → segunda `<Area>`/`<Line>` com traço mais fino.

`frontend/app/historico/page.tsx` busca as anotações junto dos dados e repassa as props. Sem regra para a métrica, nada muda visualmente.

---

## 4. Host — remover a causa física dos picos

Fora deste repositório, autorizado explicitamente pelo usuário. Executado **depois** do deploy do código, como passo separado.

- `/etc/cron.hourly/free` contém `echo 1 > /proc/sys/vm/drop_caches`. Descarta o page cache toda hora no minuto `:17` (via `17 * * * * root run-parts /etc/cron.hourly` no `/etc/crontab`), forçando os 48 containers a reler do disco ao mesmo tempo → processos em D-state → load sobe sem CPU subir. Provado por: serrote perfeito do `kbbuffers` no `sar` (26k→32k e queda para ~10k na amostra das `:20`, 24×/dia sem exceção); `:17` ser o maior balde do histograma de picos (29 de 4 dias, contra 7 no `:18`); e a CPU dos containers no minuto `:17` estar **igual ou abaixo** da baseline (4.1 vs 4.3), confirmando I/O e não CPU.
- `/etc/cron.hourly/fstrim` roda `fstrim /` no mesmo lote. Redundante: `fstrim.timer` do systemd já está `enabled` (semanal).
- Nenhum dos dois pertence a pacote (`dpkg -S` não encontra) — são da imagem do provedor.

**Procedimento:** copiar ambos para `/root/backup-cron-hourly-<timestamp>/` antes de remover.

**Verificação objetiva:** no `:17` seguinte, `sar -r` não deve mais mostrar a queda de `kbbuffers`.

---

## Testes

TDD, ciclo vermelho→verde confirmado em cada item.

**`_condicao_sustentada`** (semeia linhas reais em `metrics_history`, sem mock de relógio):
- janela inteira acima do threshold → `True`
- uma amostra abaixo no meio → `False`
- nenhuma amostra na janela → `False`
- janela mal coberta: backend recém-subido, só 1 min de dados, todos acima → `False`
- cobertura no limite da tolerância: amostra mais antiga com 2min30s numa janela de 3min (caso real da coleta de 30s) → `True`
- amostra com valor `NULL` → `False`
- `duracao_minutos == 0` → `True` sem consultar o banco
- métrica fora do mapa → `True` (fallback)

**`_evaluate_rule`:**
- pico de um ciclo só → **nenhum `AlertLog` criado**, nenhuma notificação
- condição sustentada pela janela → cria `AlertLog` **e notifica o disparo no mesmo ciclo**
- alerta notificado que resolve → notifica resolução, grava `resolved_at`
- alerta não notificado que resolve → **não** notifica, mas **grava** `resolved_at`

**Regras especiais:** `_evaluate_container_stopped` e `_evaluate_restart_loop` não notificam resolução de alerta que nunca notificou disparo.

**API:** `/metrics/history` com companheira devolve `value2`; `/metrics/history/annotations` devolve threshold e disparos; métrica sem regra devolve `threshold: null`; duas regras ativas → escolhe a de menor threshold.

**Regressão:** parte dos 293 testes atuais afirma o comportamento antigo (alerta criado no primeiro cruzamento). Atualizar esses testes faz parte do trabalho e deve ser declarado explicitamente no relato — não é regressão acidental.

## Riscos

| risco | mitigação |
|---|---|
| Uma query a mais por regra por ciclo (~5 regras / 30s) | Índice `ix_metrics_history_collected_at` já existe (criado em `a2d8c3d`); as queries dessa tabela medem ~10ms em produção. Medir CPU do `monitor-backend` após o deploy, como foi feito em 12/08. |
| Ficar mudo demais e não perceber | 3 min gera ~4 avisos/semana pelos dados reais, não zero. E agora o aviso chega **na subida**, que é o sinal que faltava. |
| Migração alterar regra ajustada à mão | Migração condicionada a `duracao_minutos == 5`. |
| Remoção no host afetar outro projeto | Ambos os scripts são globais e prejudiciais a todos; backup preservado; reversível em segundos. |
