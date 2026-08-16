# Progresso do Projeto

## Última atualização
2026-08-16 — **Investigação: "alertas de load alto desde sexta" — causa raiz encontrada, 4 camadas. Nenhuma mudança feita ainda (aguardando autorização).**

## Tarefa em andamento — Flapping de alerta de Load (2026-08-16)

### Sintoma real (medido, não relatado)
O usuário NÃO está recebendo alertas de disparo. Está recebendo mensagens de **"✅ ALERTA RESOLVIDO"** no WhatsApp cujo corpo contém o texto `Load Alto: 7.3 > 6.0` — lê-se como alerta de load.
Evidência (`alert_notification` × `alert_log`, prod): nos últimos 8 dias, **111 alertas de `load_1m`; 109 nunca notificaram disparo, mas 100% notificaram resolução**. Volume por dia: 13/08=3, 14/08(sex)=15, 15/08=24, 16/08=29. Bate exatamente com "desde sexta, ontem o dia inteiro e hoje".

### Camada 1 — BUG no motor de alertas (`notifications/alert_engine.py:167-169`)
`_evaluate_rule()` resolve e notifica resolução **incondicionalmente**, sem checar se o disparo chegou a ser notificado.
- `duracao_minutos` (=5 na regra "Load Alto") só filtra a notificação de DISPARO (linha 158-166), não a de resolução.
- `cooldown_minutos` (=30) é gravado em `AlertLog.last_notified_at`, ou seja, é **por AlertLog, não por regra** — cada blip cria um `AlertLog` novo, então o cooldown nunca se aplica.
- Resultado: todo pico de 30s acima de 6.0 → cria AlertLog (silencioso) → próximo ciclo cai abaixo → **manda "resolvido"**. Ruído puro.
- Mesmo padrão incondicional existe em `_evaluate_container_stopped` (linha 320) e `_evaluate_restart_loop` (linha 413).

### Camada 2 — `duracao_minutos` é inatingível por construção
O `AlertLog` é aberto no 1º cruzamento e resolvido no 1º não-cruzamento, então a janela de 5 min quase nunca é satisfeita. Medido em prod (4 dias): **73 episódios acima de 6.0, ZERO com ≥5 min; o mais longo durou 3,0 min**. Ou seja, se `duracao_minutos` fosse respeitada ANTES de abrir o alerta, teriam sido **0 alertas** em vez de 73.

### Camada 3 — threshold calibrado em cima do ruído
`load_1m > 6.0` em host de 6 cores = 1.0 load/core. Percentis de `load_1m` em prod:
| dia | p50 | p90 | p99 | max |
|---|---|---|---|---|
| 13/08 | 2.41 | 3.75 | 5.02 | 8.82 |
| 14/08 | 2.40 | 3.95 | 6.08 | 9.99 |
| 15/08 | 2.48 | 4.12 | 6.16 | 9.88 |
| 16/08 | 2.72 | 4.44 | 6.87 | 9.36 |
O threshold está **exatamente no p99**. Uma deriva mínima da cauda (p99 5.0→6.9) multiplicou os alertas por 10.
**A carga do host NÃO mudou** — confirmado por fonte independente (`sar`, sysstat, não pelo próprio monitor): médias diárias de `ldavg-1` = 2.44 (13/08), 2.60 (14/08), 2.56 (15/08), 2.90 (16/08); `%idle` 79%; `%iowait` 0.3%; `blocked` 0. Não há problema de capacidade.
Fator agravante: o fix de índice de 12/08 (`a2d8c3d`) fez o coletor voltar a 2880 amostras/dia (era 1480-1900) — ele agora **enxerga** blips que antes perdia.

### Camada 4 — quem gera os picos (fora do repo, na VPS)
Histograma dos picos por minuto da hora (4 dias): minuto **:17 → 29 ocorrências** (maior balde de longe), :18 → 7, :00/:01 → 33.
- **`:17` = `/etc/cron.hourly/free`, que contém literalmente `echo 1 > /proc/sys/vm/drop_caches`.** Roda via `17 * * * * root run-parts /etc/cron.hourly` (`/etc/crontab`). Descarta ~5,4 GB de page cache toda hora → os 48 containers reler tudo do disco → processos em D-state → **load sobe sem CPU subir**.
  - Provado por 3 evidências independentes: (a) `sar -r`, `kbbuffers` faz serrote perfeito toda hora — sobe 26k→32k e despenca pra ~10k na amostra das :20, 24×/dia sem exceção; (b) o balde :17 é o maior do histograma; (c) `container_metrics` mostra CPU dos containers no minuto :17 **igual ou abaixo** da baseline (4.1 vs 4.3) — confirma que é I/O, não CPU.
- **`/etc/cron.hourly/fstrim`** (`fstrim /`) roda no mesmo lote das :17 e é **redundante** — `fstrim.timer` do systemd já está `enabled` (semanal). Discard I/O de hora em hora numa raiz de 193G.
- Ambos os scripts **não pertencem a nenhum pacote** (`dpkg -S` não acha) — são da imagem do provedor, não instalados pelo usuário.
- `:00/:01` = subida difusa de vários containers (kong, minio, redis, meta) — crons de aplicação no topo da hora, sem culpado único. Não é o problema principal.
- `_cleanup()` do scheduler NÃO está envolvido: é `interval, hours=1` a partir do boot do container (13:43 -03), roda no minuto :43, não :00/:17.

### Validação da solução proposta (números de prod)
- Com `duracao_minutos=5` respeitada antes de abrir o alerta: **0 alertas** em 4 dias (nenhum episódio chegou a 5 min).
- Trocando a métrica pra `load_5m > 6`: 0 ocorrências em 13, 14 e 15/08; 1 em 16/08 (pico 6.13).

### Camada 5 — o gatilho de "por que piorou na sexta" (investigado depois, a pedido do usuário)
O threshold funciona como amplificador: entre 13/08 e 16/08 a **mediana** do load subiu só 13% (2.41→2.72) mas os alertas subiram 867% (3→29). Três coisas empilharam, nenhuma delas um defeito:
1. **12/08** — o próprio fix de índice (`a2d8c3d`) devolveu a coleta a 2880 amostras/dia (vinha de ~1700 com o backend atolado). O monitor passou a *enxergar* ~70% mais blips.
2. **13/08 14h UTC — degrau permanente de ~1 GB de RAM.** RAM ficou em 46% o dia todo e então subiu: 14h=48.8 → 17h=51.1 → 23h=53.1 → estabilizou em 53-54%. Coincide no minuto com o restart de `supabase-syscursos-realtime-1` (`2026-08-13T14:04:55Z`). Crescimento por container (13/08→15/08): `syscursos-kong` 522→704 MB, `corridas-app` 304→538 MB, `syscursos-realtime` 130→228 MB, `syscursos-storage` 60→112 MB. Por `sar`, `kbavail` caiu de 4,27 GB (13/08) pra 3,66 GB (16/08), em queda contínua.
   **Mecanismo:** é a memória livre que absorve o `drop_caches` das :17. Com ~600 MB a menos de folga, a mesma faxina horária dói mais → mais containers voltam ao disco juntos → fila maior → load maior. O gerador de picos não mudou; o colchão encolheu.
3. **Fim de semana — tráfego dobrou.** Requisições/dia (`access_log`): 13.536 (13/08) → 17.128 (14/08) → 28.690 (15/08) → 20.831 (16/08).

### Camada 6 — o bug é do motor, não da regra de Load (30 dias)
| métrica | alertas | nunca notificaram disparo |
|---|---|---|
| `load_1m` | 648 | 630 (97%) |
| `cpu_percent` | 89 | 84 (94%) |
| `swap_percent` | 94 | 32 (34%) |

8 das 14 regras têm `duracao_minutos > 0` e portanto sofrem do mesmo problema. O fix tem que ser no `_evaluate_rule`, não na regra.

### Decisões fechadas com o usuário (2026-08-16)
- **Abordagem A aprovada** — janela consultada do `metrics_history` a cada ciclo ("a condição foi verdadeira em todas as amostras dos últimos N min?"). Sem estado em memória, sem migração de schema, sobrevive a restart. Viável porque `collect_and_store` faz `session.commit()` (scheduler.py:65) **antes** de chamar `evaluate()` (:67) — a amostra atual já está na tabela.
  - Descartado: estado em memória (perde no deploy, não auditável) e coluna `pending_since` no `AlertLog` (migração + polui a tela de histórico de alertas).
- **Regra "Load Alto": limite 6.0, confirmação 3 minutos.** Base medida (7 dias, episódios acima de 6.0): 109 sem confirmação → 47 com 1min → 12 com 2min → **4 com 3min** → 0 com 5min. 3min corta 96% do ruído sem virar silêncio total.
- **Avisar na subida** = a notificação de disparo, que hoje nunca é enviada. Volta a funcionar assim que a janela é respeitada.
- **`/historico`**: a página já existe com gráfico de linha e métrica "Load Avg". Acrescentar: linha do threshold, marcadores nos momentos de alerta disparado, e `load_5m` junto do `load_1m`. (O usuário escreveu "download" mas queria dizer "load".)
- **Host autorizado:** remover `/etc/cron.hourly/free` e `/etc/cron.hourly/fstrim`, com backup dos dois antes.

### Estado
Nenhuma mudança de código nem de host aplicada ainda. Próximo passo: escrever a spec em `docs/superpowers/specs/2026-08-16-alertas-load-historico-design.md`.

### Backlog acordado para DEPOIS desta tarefa
Registrado em `TAREFAS.md`: (1) syscursos engordando e recebendo requisições sem ninguém acessar, e login não funciona; (2) container do xadrez parado; (3) requisições diárias a subdomínios inexistentes (ex: `zap.dlsistemas.com.br`) e como bloquear de vez.

---

## Última atualização (histórico)
2026-08-12 — Investigação em andamento: (1) causa raiz do log rotation do Traefik de 20/07 — investigada a fundo via SSH read-only na VPS, sem prova forense conclusiva (evidência direta já expirou), mas alerta de mitigação implementado; (2) CPU alto do monitor-backend (~55% médio) mesmo após fix do DockerClient — em andamento.

## Tarefa em andamento — Investigação Traefik + CPU alto (2026-08-12)
**Contexto:** sessão anterior (10/08) corrigiu `collector/docker_client.py` (commit `200f517`, já em produção) mas CPU continuou alto. Nesta sessão: reiniciado manualmente o container Traefik na VPS (fora deste repo) após achar ele escrevendo ~445MB num arquivo deletado desde 20/07 (rotação de log falhou); confirmado resolvido.

### Item 1a — Causa raiz do log rotation do Traefik (20/07): investigado, sem conclusão forense definitiva
Investigação via SSH read-only na VPS (144.91.92.70). Descartado com confiança: nenhum script de `monitor/scripts/` toca no container/volume do Traefik; `/etc/logrotate.d/traefik-access-log` está correto (copytruncate, sem stanza conflitante); sem reboot do host (`journalctl --list-boots`: mesmo boot desde 12/07); container Traefik nunca foi recriado (`Created: 2026-07-13`, `RestartCount: 0`); sem mudança em `/opt/traefik/dynamic/` naquela noite.
Achado não conclusivo: o inode atual do `access.log` tem birth time **2026-07-19 20:46:08 -03:00**, coincidindo no segundo com uma recriação dos containers do próprio `vps-monitor` (deploy via `deploy.sh`, sessão SSH interativa ativa desde 20:24 — padrão de uma sessão anterior do Claude Code). Esse deploy não mexe no container/volume do Traefik, então a ligação causal não pôde ser provada — evidência direta (logs do container Traefik daquela hora, histórico de shell) já expirou. **Conclusão: causa raiz específica do dia 20/07 não é mais recuperável forense.**

### Item 1b — Alerta "Access Log Parado": ✅ implementado, testado, não deployado ainda
- Nova regra padrão em `models/database.py` (`_DEFAULT_RULES` + bloco de migração em `init_db()`): `metrica="access_log_stale_minutos"`, `operador=">"`, `threshold=360` (6h), `severidade="aviso"`, `cooldown_minutos=360`.
- `collector/access_log_tailer.py`: nova função `_evaluate_log_stale()`, chamada a cada ciclo de `tail_access_log()` (15s) usando `st_mtime` do arquivo (não escrita nova = mtime parado). Reusa `_evaluate_rule()` de `notifications/alert_engine.py` (mesmo padrão de `check_docker_cleanup` em `scheduler.py` pro `docker_reclaimable_mb`).
- Frontend: `frontend/app/alertas/page.tsx` — adicionado `access_log_stale_minutos` em `METRICAS`/`METRICA_LABELS` pra aparecer no dropdown de criação/edição de regra.
- Testes novos em `tests/test_access_log_tailer.py`: `test_access_log_parado_dispara_alerta`, `test_access_log_recente_nao_dispara_alerta`. Testes existentes ajustados: `tests/test_alerts_api.py` e `tests/test_database.py` tinham contagem hardcoded de regras padrão (13→14).
- **Decisão não-óbvia:** a nova função em `access_log_tailer.py` importa `api.config.get_config`, que importa transitivamente `api.auth` — este levanta `RuntimeError` se `JWT_SECRET` não estiver setado. `tests/test_access_log_tailer.py` não tinha essa dependência antes; corrigido com fixture `autouse` setando `JWT_SECRET` (mesmo padrão já usado em `test_projects_api.py`, documentado ali).
- **Bug encontrado e corrigido durante TDD (só no teste, não no código de produção):** `datetime.utcnow().timestamp()` num teste inicial calculava o epoch errado — `.timestamp()` em datetime naive assume horário LOCAL, não UTC, introduzindo um offset igual ao fuso da máquina. Corrigido pra `time.time()`. O código de produção usa `datetime.utcfromtimestamp(mtime)` (mtime já é epoch UTC via `os.stat()`), que está correto.
- Suíte completa rodada após a mudança: **290/290 passed** (~391s). Baseline antes desta sessão não foi capturado isoladamente, mas a suíte já estava verde antes das mudanças (só as 2 contagens hardcoded quebraram, ambas corrigidas).
- **Ainda não deployado em produção** — combinar com o deploy do item 2 (CPU) quando confirmado com o usuário.

### Item 2 — CPU alto: causa raiz real encontrada (diferente da hipótese original) — ✅ fix implementado, testado, NÃO deployado
Em vez de instrumentar com `time.monotonic()` e fazer deploy pra coletar timing (arriscado, exige deploy só pra medir), investiguei direto em produção via SSH (read-only): `EXPLAIN QUERY PLAN` nas queries reais + contagem de linhas por tabela.

**Achado real:** `container_metrics` tem **2.548.266 linhas** em produção (cresce ~1 linha/container a cada 30s, 48 containers ativos, retenção de 30 dias) e **não tinha nenhum índice** além da PK. Duas consultas que rodam sobre essa tabela faziam table scan completo, confirmado via `EXPLAIN QUERY PLAN` (`SCAN container_metrics`):
1. `_evaluate_restart_loop()` em `notifications/alert_engine.py` — roda **a cada ciclo do scheduler (30s), uma vez por container** (48x por ciclo). Medido em produção: ~0,5s a ~8s por consulta dependendo do cache do SO/disco — com 48 containers, isso sozinho pode consumir dezenas de segundos de CPU/I/O dentro de um ciclo de 30s.
2. `_cleanup()` em `collector/scheduler.py` — `DELETE FROM container_metrics WHERE collected_at < cutoff`, roda 1x/hora, também table scan completo.

Isso é uma causa raiz mais específica e mais impactante que a hipótese original (sessão síncrona bloqueando o event loop) — explica o CPU sustentado em ~55% e o atraso de 14-15s no `tail_access_log` (mesmo event loop, saturado pelos scans).

**Fix (TDD, commit ainda não feito):**
- `models/database.py`: adicionados índices em `container_metrics` (`collected_at`; `container_id, collected_at`; `container_name, collected_at`), `container_disk_usage` (`collected_at`; `container_name, collected_at`) e `metrics_history` (`collected_at`) — cobrindo todo padrão de consulta encontrado via grep em `alert_engine.py`, `scheduler.py`, `api/metrics.py`, `api/containers.py`.
- Migração via `CREATE INDEX IF NOT EXISTS` em `init_db()` (mesmo padrão dos `ALTER TABLE` já existentes) — necessário pra bancos já existentes em produção, já que `Base.metadata.create_all()` não adiciona índices em tabelas que já existem.
- Testes novos em `tests/test_database.py`: confirmam os índices existem e que as duas queries acima usam `SEARCH`, não `SCAN` (`EXPLAIN QUERY PLAN`). Confirmado ciclo vermelho→verde (revertido temporariamente via `git stash`, testes falharam como esperado, depois passaram com o fix).
- Suíte completa: **293/293 passed** (~396s).

**Deployado em produção em 2026-08-12** (commit `a2d8c3d`, após rebase em cima de `200f517` que já estava em produção). Usuário optou por deployar já e medir, em vez de fazer o `run_in_executor` antes.

**Resultado medido em produção pós-deploy:**
- Mesma query que antes levava 0,5-8s (dependendo do cache) agora leva **0,010s** — confirmado via `time sqlite3`.
- CPU do `monitor-backend` (`docker stats`, 6 amostras em ~1min): **0,26%-0,93%**, contra os ~55% médio reportado antes do fix.
- Índices confirmados criados via migração automática (`sqlite_master`); regra "Access Log Parado" confirmada inserida (`ativo=1`, `threshold=360`).
- Containers saudáveis pós-deploy (`monitor-backend`, `monitor-frontend`, `monitor-nginx` up, `Application startup complete`).

**Conclusão:** fix de índice sozinho resolveu o problema — CPU caiu de ~55% pra <1%. A refatoração `run_in_executor` (pedido original) fica descartada por ora — o gargalo real era o índice faltante, não o bloqueio síncrono do event loop em si (esse bloqueio ainda existe arquiteturalmente, mas agora é da ordem de milissegundos por chamada, não segundos).

### Itens finais (8/9/10) — investigados, sem mudança de código (fora do escopo do repo ou requer autorização)

**(8) Eficiência de logging dos containers:** OK, sem ação necessária.
- Docker daemon (`/etc/docker/daemon.json`) já tem rotação configurada (`max-size: 10m`, `max-file: 3`) — logs de containers limitados a 30MB cada, total de 150MB pros 48 containers da VPS. Saudável.
- `monitor-backend` loga pouco (só startup, sem access log verboso por request) — eficiente.
- `traefik-socket-proxy` é o maior log individual (~9.6MB) por causa do provider Docker do Traefik fazendo polling frequente (`GET /containers/{id}/json` em rajada pra cada container) — comportamento normal do Traefik, fora do escopo deste repo (gerenciado em `/opt/traefik`).
- Nota menor (não corrigida, fora do repo): `accessLog` do Traefik (`/opt/traefik/traefik.yml`) não tem `bufferingSize` configurado — grava sincronamente por request. Não é um problema no volume atual de tráfego, mas fica registrado como possível otimização futura se o tráfego crescer.

**(9) Eficiência do gerenciamento geral:** achado real e relevante, corrobora a causa raiz do item 2.
- `docker system df`: 33 imagens (23.57GB), 138 build caches (19.94GB, 10.7GB reclamável mas dentro da janela de proteção de 7 dias do cron semanal já existente — normal, não é acúmulo indevido).
- RAM: 5.7/11GB usado, 6.2GB em buff/cache — saudável. Swap: 3/8GB (37,5%) — moderado, não crítico.
- **Achado real:** os 3 workers do host que escrevem no SQLite do monitor via CLI (`backup-worker.sh`, `firewall-worker.sh`, `project-delete-worker.sh`, todos com `.timeout 5000`) acumularam **1000+ ocorrências cada** de `Error: stepping, database is locked (5)` em `/var/log/*-worker.log`, ao longo do tempo — consistente com o mesmo root cause do item 2 (queries lentas de table scan seguravam transação de escrita por mais que os 5s de busy-timeout do sqlite3 CLI). Não há mais erros novos desde antes do deploy de hoje (log parado desde 13:14, cron seguiu rodando normalmente depois, sem novas linhas de erro) — sinal de que o fix de índice também deve ter resolvido isso, mas vale monitorar por alguns dias antes de considerar encerrado.

**(10) Revisão de segurança:** dentro do repo, sem achados. **Fora do repo, achado CRÍTICO real na VPS compartilhada — reportado ao usuário, nenhuma ação tomada (requer autorização, afeta bancos de outros clientes).**
- Repo: sem segredos hardcoded (`git grep` limpo), `.env` corretamente fora do git, `JWT_SECRET` forte (64 chars). Nota menor: `.env` em produção está `644` (deveria ser `600` — só root tem acesso ao host hoje, risco prático baixo, mas fácil de corrigir).
- **CRÍTICO:** `supabase-corridas-supavisor-1` e `supabase-syscursos-supavisor-1` (Postgres poolers de OUTROS projetos na mesma VPS) publicam as portas 5432/6543/55432/6643 em `0.0.0.0`. O UFW mostra "deny incoming" por padrão, mas **Docker escreve regras DNAT direto no iptables que contornam o UFW** — confirmado via `iptables -t nat -L DOCKER`: essas portas têm DNAT ativo pros containers internos, e a chain `DOCKER-USER` (que intercepta esse tráfego antes do DOCKER-forward) só tem regra de proteção pra porta 9000 (Portainer, já corretamente restrito a `127.0.0.1` por alguém antes). **As portas de Postgres não têm essa proteção — provavelmente acessíveis da internet pública agora, dependendo só da senha do Postgres como defesa.** Mitigação padrão: `ufw-docker` (não instalado) ou regras manuais em `DOCKER-USER` iguais ao padrão já usado pra Portainer. Fora do escopo deste repo (não é infra do vps-monitor) — só reportado, nenhuma mudança feita.

## Correções aplicadas após autorização do usuário ("vamos corrigir tudo que tiver")

**Segurança — portas Postgres expostas (CRÍTICO, fora do repo, autorizado pelo usuário):**
- Adicionadas regras em `/etc/ufw/after.rules` na VPS restringindo as portas 5432, 6543 (`supabase-corridas-supavisor-1`) e 55432, 6643 (`supabase-syscursos-supavisor-1`) a `127.0.0.1`, mesmo padrão já usado pra Portainer (porta 9000). Aplicado via `ufw reload`, confirmado ativo em `iptables -L DOCKER-USER`, sobrevive reboot (`ufw` habilitado no boot).
- **Bug introduzido e corrigido na hora:** a primeira tentativa adicionou um SEGUNDO bloco `*filter ... COMMIT` separado no `after.rules` — descobri que dois blocos `*filter` pra mesma chain (`DOCKER-USER`) se SOBRESCREVEM um ao outro no reload do ufw (não somam), o que apagou silenciosamente a proteção da porta 9000 (Portainer) por alguns instantes. Corrigido unindo todas as regras (Portainer + Postgres) num único bloco `*filter`. Backup do arquivo original preservado em `/etc/ufw/after.rules.bak-20260712191702`. Confirmado depois do fix: as 10 regras (5 portas × ACCEPT/DROP) coexistem corretamente, containers afetados (`supabase-corridas-supavisor-1`, `supabase-syscursos-supavisor-1`, `portainer`) continuam saudáveis (não são reiniciados por essa mudança — é só regra de rede).
- **Nota pra próxima vez que mexer em `/etc/ufw/after.rules` nesta VPS:** sempre um único bloco `*filter`/`:DOCKER-USER - [0:0]`/`COMMIT` pra chain `DOCKER-USER`, nunca blocos separados por serviço.

**Permissão do `.env` em produção:** `644` → `600` (`/opt/vps-monitor/monitor/.env`). Sem impacto funcional, só reduz superfície de leitura (embora só root tenha acesso ao host hoje).

**Timeout do SQLite nos workers do host (defesa em profundidade):** `.timeout 5000` → `.timeout 20000` em `backup-worker.sh`, `firewall-worker.sh`, `project-delete-worker.sh` (commit `143d113`). Deployado via `git pull` no host (scripts rodam direto do filesystem via cron, sem rebuild de container) + `chmod +x` reaplicado (mesmo problema recorrente de bit executável perdido no `git pull`, já visto nesta feature antes — o `git pull` na VPS falhou na primeira tentativa por causa de um diff de MODO de arquivo — 644 vs 755 — de um `chmod +x` manual anterior; resolvido com `git checkout --` nesses arquivos antes do pull, já que o conteúdo era idêntico, só o modo divergia).

**Não corrigido (intencionalmente, não é um problema real):** `bufferingSize` do `accessLog` do Traefik — não configurado, mas o volume de tráfego atual não justifica a mudança; fica registrado como possível otimização futura se o tráfego crescer.

## Segunda rodada de segurança (2026-08-12, sessão seguinte) — achado adicional + bug real no fix anterior

Usuário pediu pra verificar outras portas expostas encontradas na sondagem original (fora do escopo do Postgres já corrigido): `xadrez-essencial-*` (3210/4210/4211), `mecanicapro-nginx` (8080), Kong do Supabase (8000/8100/8143).

- **Kong do Supabase:** testado sem API key → `401 "No API key found in request"`. **Não é falha, é a arquitetura padrão do Supabase auto-hospedado** (Kong é a porta pública da API, protegida por chave, não por firewall). Nenhuma ação.
- **Mailpit (`xadrez-essencial-mailpit-1`, imagem `axllent/mailpit`):** testado o painel web sem credencial nenhuma → **HTTP 200 com a lista de e-mails capturados, sem autenticação nenhuma.** Requisição exata usada: `curl -s http://127.0.0.1:8030/api/v1/messages` (via SSH, simulando o que a porta pública 0.0.0.0:8030 responde). Mailpit é ferramenta de captura de e-mail de dev/teste — se exposta, qualquer um na internet pode ler e-mails que o app enviaria (reset de senha, links de confirmação). **Achado real, corrigido.**
- **`xadrez-essencial-web/api/worker` (3210/4210/4211) e `mecanicapro-nginx` (8080):** investigados, sem confirmação de falha real (podem ser acesso direto intencional). **Não mexidos** — recomendado avisar quem administra esses projetos, não é claramente uma falha de segurança.

### Correção aplicada: restringir Mailpit a localhost — e um bug real encontrado e corrigido no processo

Usuário autorizou corrigir e pediu teste real de fora + relatório da requisição usada.

1. Adicionadas regras `DOCKER-USER` pro Mailpit (portas 8030 web / 1030 SMTP) no mesmo bloco único do `/etc/ufw/after.rules`, mesmo padrão das correções anteriores.
2. **Teste real de fora (não só de dentro da VPS) revelou um bug sério nas regras anteriores**: o `DOCKER-USER` roda DEPOIS do DNAT (PREROUTING), então o pacote já chega com a **porta INTERNA do container**, não a porta externa publicada — sempre que elas são diferentes. As regras originais usavam `--dport <porta externa>` (ex: `--dport 8030`, `--dport 55432`, `--dport 6643`), que **nunca batiam** quando a porta interna era diferente (Mailpit 8030→8025 e 1030→1025; Supabase syscursos 55432→5432 e 6643→6543). Resultado: essas 3 portas continuavam **completamente abertas pra internet** mesmo com a regra "aplicada" — só descobri porque testei de um ambiente genuinamente externo à VPS (não de dentro via SSH), como o usuário pediu.
   - Curiosamente, a regra do Postgres do `supabase-corridas` (5432/6543, sem remapeamento de porta) e do Portainer (9000, idem) funcionavam por **coincidência** (porta externa = porta interna nesses casos).
3. **Corrigido:** todas as regras reescritas pra casar por **IP interno do container + porta interna** (`-d <ip> --dport <porta interna>`), descoberto via `iptables -t nat -L DOCKER` (mapeamento DNAT real) e `docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'`.
4. **Teste final, de um ambiente genuinamente externo à VPS**, contadores de pacote zerados antes de cada rodada: as 7 portas (9000, 5432, 6543, 55432, 6643, 8030, 1030) — todas sem resposta/conexão de fora, todas com exatamente 3 pacotes (retransmissão de SYN típica) batendo na regra `DROP` correspondente. Confirmado que acesso local (via `127.0.0.1`) continua funcionando (Mailpit `HTTP 200` local, Postgres conecta local) e os 4 containers afetados continuam saudáveis (`Up`, sem restart).
5. **Caveat documentado no próprio arquivo `/etc/ufw/after.rules`:** o IP interno do container não é garantido fixo pelo Docker — se qualquer um desses containers for recriado no futuro (rebuild de imagem, `docker compose up -d` após mudança), o IP pode mudar e a regra para de funcionar silenciosamente. Precisa reconferir com `docker inspect` e atualizar as regras se isso acontecer.
6. Backups do `after.rules` preservados: `/etc/ufw/after.rules.bak-20260712191702` (original, pré-sessão) e `/etc/ufw/after.rules.bak-20260812181024` (antes desta correção).

**Lição pra próxima vez que for restringir uma porta publicada via Docker+UFW nesta VPS:** sempre confirmar `iptables -t nat -L DOCKER -n` pra achar a porta INTERNA real, nunca assumir que porta externa = porta interna, e **sempre testar de um ponto genuinamente externo à VPS** (não de dentro via SSH/localhost) antes de considerar corrigido — testar de dentro pode dar falso-positivo (o hairpin NAT do próprio host pra seu IP público segue um caminho diferente do FORWARD/DOCKER-USER e não prova nada sobre acesso externo real).

## Contexto necessário
Sessão de 2026-08-12 encerrada com todos os itens pedidos concluídos, incluindo o achado crítico de segurança corrigido com autorização do usuário. Nenhuma tarefa de código pendente neste repo.

## Contexto necessário
- `collector/scheduler.py` — todas as jobs (`collect_and_store`, `tail_access_log`, `collect_disk_usage`, `_cleanup`, `check_docker_cleanup`) usam `Session(engine)` síncrona do SQLAlchemy direto dentro de `async def`, sem `run_in_executor` — é a hipótese principal do CPU alto.
- `models/database.py` confirmado: banco é **SQLite** (`sqlite:///{DB_PATH}`, WAL mode), não Postgres — reforça a hipótese (SQLite é single-writer).
- `collector/access_log_tailer.py` (`tail_access_log`) é a job mais suspeita: pra cada linha do access log faz 2 queries síncronas (`_upsert_daily`/`_upsert_hourly`) + insert, tudo dentro da mesma sessão bloqueante — potencial N+1 query pattern além do bloqueio do event loop.
- Ambiente de teste local: `py -m pytest` funciona direto (Python 3.14.4 via `py` launcher no Windows; `python`/`python3` no PATH do bash são só stubs da Microsoft Store, não funcionam). Suíte completa leva ~6-7 min.

---

## Última atualização (histórico anterior)
2026-07-23 — **Feature "Excluir Projeto" 100% concluída, testada end-to-end em produção contra um projeto real, e deployada.** Backlog desta sessão (Lacunas de monitoramento + Firewall UFW + Excluir projeto) está inteiramente fechado. Não há próxima tarefa formalmente enfileirada — ver seção "Próxima tarefa" no fim deste arquivo pra decidir com o usuário o que vem a seguir.

## Tarefa concluída — Excluir Projeto (teardown completo)
Plano: `docs/superpowers/plans/2026-07-21-excluir-projeto.md` (commit `9fadeda`), 6 tasks TDD via `superpowers:subagent-driven-development`. Ledger canônico (histórico completo task a task): `../.superpowers/sdd/progress.md`.

- Tasks 1-5 (modelo, preview, endpoint de delete, worker no host, modal no frontend): completas, revisadas, commits `24f8c51`...`2bd1e63`. Task 4 teve 3 rounds de fix (SQL injection em `protocolo`, falhas parciais viravam sucesso, ordem errada na remoção de volume, path traversal em nome de rota Traefik).
- Task 6 (deploy + teste E2E real, 2026-07-23): usuário autorizou testar contra um projeto real (`fotosaas`, não descartável — app real com Postgres/Supabase self-hosted, rota pública `foto.dlsistemas.com.br`).
  - **Incidente real encontrado e corrigido durante o teste:** o primeiro passo do fluxo (criar snapshot) derrubou o site. Causa raiz, **fora do código desta feature**: `scripts/backup-worker.sh` (feature de Backup/Restore já em produção desde antes) rodava `docker compose stop`/`up -d` sem `-f`, então projetos deployados com múltiplos arquivos overlay (caso do fotosaas: `docker-compose.prod.yml`+`docker-compose.traefik.yml`) eram recriados só com o `docker-compose.yml` base — no caso do fotosaas, um arquivo de ambiente de teste local (chaves fake, sem Traefik, com um `mailhog` indevido). Corrigido (commit `3e9faef`, 2 rounds de revisão): novo helper `_definir_compose_flags()` lê o label `com.docker.compose.project.config_files` (Docker Compose grava a lista exata de `-f` usados no deploy original) e usa isso em todo `stop`/`up -d`; no restore, os `-f` usados no `up -d` final vêm de um metadado salvo DENTRO do próprio snapshot (não do container ao vivo, que fica desatualizado depois do rsync sobrescrever os `.yml`). Site restaurado e confirmado saudável antes de prosseguir. **Detalhes completos do incidente na memória auto-memory `project_vps_monitor_backup_worker_compose_files_bug`.**
  - **Exclusão real do fotosaas executada com sucesso** depois do fix: snapshot criado (188 MB), preview correto, `POST /delete` disparado, 10 containers + 3 volumes removidos, job `done` sem erro. Dois gaps operacionais (não de código) corrigidos no caminho: cron do `project-delete-worker.sh` nunca tinha sido instalado na VPS (instalado agora, mesmo padrão dos outros workers) e o script chegou sem bit executável do git (`chmod +x` aplicado, mesmo problema recorrente já visto em outros scripts desta feature).
  - **Revisão final whole-branch** (base `9fadeda`, head `2bd1e63`, 9 commits): achou 1 problema Minor só visível no conjunto — `POST /delete` não validava que as rotas/regras enviadas pertenciam de fato ao projeto-alvo (só validava formato). Corrigido e deployado (commit `4429d5d`): `delete_project` agora recalcula as mesmas candidatas do preview e rejeita qualquer rota/regra que não pertença ao projeto. 2 testes de regressão adicionados. Suíte completa: 288/288.
- **Feature 100% deployada e verificada em produção**, incluindo o caso mais destrutivo (exclusão real de um projeto com banco de dados).

## Próxima tarefa
Nenhuma tarefa está formalmente enfileirada agora — o backlog desta sessão (lacunas de monitoramento, firewall UFW, excluir projeto) está inteiramente concluído. Perguntar ao usuário o que ele quer atacar a seguir antes de iniciar qualquer trabalho novo.

Pendências que dependem só do usuário (não bloqueiam trabalho novo):
- Confirmar visualmente/funcionalmente as páginas mais antigas do backlog original em produção (listagem de projetos, gestão de rotas do Traefik).
- Autorizar o primeiro uso do backup/restore num projeto de cliente real além do fotosaas (o backup/restore em si já foi validado ao vivo durante o incidente acima, mas isoladamente do restore).

## Contexto necessário
Nenhum arquivo específico — próxima tarefa ainda não definida. Quando o usuário decidir o próximo passo, reescrever esta seção com os arquivos relevantes a ela.

Recomendação: como esta tarefa está 100% documentada aqui e no ledger, é um bom momento pra rodar `/compact` ou `/clear` antes de começar algo novo.

## Tarefa concluída (2026-07-21)
**Fechar lacunas de monitoramento de risco** (swap, restart-loop de container com sinalização de OOM, atribuição de alertas por projeto) — **100% implementado, revisado e deployado em produção**. Executado via subagent-driven-development, 4 tasks + revisão final whole-branch (Ready to merge: Yes, sem achados Critical/Important). Commits: `6190ec6` (swap), `ad0dc22`→`62c8766` (restart-loop, 2 rodadas de fix — dedupe mutável + wildcard SQL não escapado), `1302c80` (atribuição por projeto), `159f70f` (card de swap no dashboard).

**Fix urgente no meio do plano (2026-07-21, já deployado):** você reportou que o monitor ficava reenviando notificação de "container parado" a cada ~30s pro mesmo container em vez de notificar só na queda e só na resolução. Causa raiz confirmada ao vivo: a regra "Container Parado" tem `cooldown_minutos=0`, o que fazia a checagem de cooldown ser sempre verdadeira. Corrigido (commit `2978141`, TDD, revisado, 258/258 testes), deploy feito e confirmado funcionando em produção (alerta do `fotosaas-worker` parou de reenviar).

**Interrupção nesta sessão:** configurada limpeza automática de Docker (cron semanal + GC nativo do builder) em 2 VPS de produção diferentes — a principal (`144.91.92.70`, registrada normalmente na memória) e uma segunda (`192.168.0.233`, cujo registro foi explicitamente apagado da memória a pedido do usuário — status fica em `C:\Users\dougl\Desktop\docker-cleanup-status.md`). Sem relação com o código deste repositório.

- Spec: `docs/superpowers/specs/2026-07-20-lacunas-monitoramento-design.md`.
- Motivação real: usuário subiu vários projetos novos (containers na VPS pularam de ~30 pra 50 durante esta sessão, incluindo uma stack nova `fotosaas-*`) e perguntou se o monitor identifica uso excessivo. Levantamento em produção confirmou sinais reais: swap em 52% de uso (não monitorado hoje), disco saltou de 17%→45% em poucos dias.
- Decisões-chave do design:
  - **Sem script novo no host** (diferente das últimas 3 features) — swap vem de `/proc/meminfo` (já montado), OOM de container vem de `docker inspect` (`State.OOMKilled`, já lido por `_evaluate_container_stopped` existente), restart-loop vem do histórico já coletado (`ContainerMetrics.restart_count`), atribuição por projeto reaproveita `agrupar_por_projeto` (já extraído na feature de backup).
  - Restart-loop: 3+ reinícios em 10 min (mas configurável depois via a tela de regras de alerta já existente — usa os campos genéricos `threshold`/`duracao_minutos` do `AlertRule`, não hardcoded).
  - Swap: aviso 70% / crítico 90%.
  - Atribuição por projeto: só enriquece o contexto dos alertas de CPU/RAM já existentes (top projeto consumidor) — não é uma regra nova.
  - OOM killer via log de kernel (`journalctl`) foi descartado — `docker inspect` já resolve o caso prático (containers) sem precisar de script novo.
  - Card de Swap novo no dashboard principal (`StatCard`, mesmo padrão de RAM/Disco).

## Tarefa concluída — Firewall (UFW)
**Gestão de regras de firewall (UFW)** — motivado por sugestão externa do usuário sobre nftables/firewalld/CrowdSec. **100% implementado, revisado, deployado e testado ponta a ponta em produção.**
- Spec: `docs/superpowers/specs/2026-07-20-firewall-ufw-design.md`.
- Plano: `docs/superpowers/plans/2026-07-21-firewall-ufw.md` (commit `b3a897a`), 6 tasks TDD — todas concluídas via `superpowers:subagent-driven-development`. Ledger canônico em `../.superpowers/sdd/progress.md`.
- Commits: `74b8e89` (modelo), `bc59aae` (API + 13 testes), `c747a04` (mount inicial, depois corrigido), `e3effdf`→`f4b6fd8` (worker no host, 1 fix — typo `regulas`→`regras` causava NameError em toda execução), `4366d74` (frontend `/firewall`), `782ca13` (fix crítico pós-deploy, ver abaixo), `92503cf` (fix da revisão final).
- **Bug real encontrado só na verificação manual de deploy** (invisível em qualquer diff/revisão de código, só aparece em runtime do Docker): o mount original de `FIREWALL_STATE_FILE` era de **arquivo único**. O `mv` atômico do worker troca o inode do arquivo, e o bind mount de arquivo único do Docker fica preso no inode antigo — confirmado ao vivo via `ls -li` mostrando inodes diferentes entre host e container. Corrigido (`782ca13`): mount de **diretório** (`/opt/vps-monitor-firewall/`) em vez de arquivo único, igual ao padrão já usado nas outras 3 features (fail2ban, Traefik, backups). Reverificado ao vivo: mesmo inode logo após redeploy, inode muda após novo `mv` mas continua igual em ambos os lados — bug genuinely resolvido.
- Teste ponta a ponta real em produção: criar regra (porta 8081/tcp/allow) via API → confirmada no `ufw status` real → refletida no `GET /rules` após o cron rodar → removida via API → sumida do `ufw status` real. Tentativa de remover a porta 22 via API confirmada bloqueada (400).
- Revisão final whole-branch: **Ready to merge: Yes**. 1 achado Important corrigido (`_ler_estado()` não tratava JSON corrompido → 500; agora degrada pra lista vazia, igual ao caso de arquivo ausente). 273/273 testes passando.
- Descoberta importante: a VPS usa **UFW** (não nftables puro) — UFW por baixo traduz pra nftables via `iptables-nft`. Já existe uma tabela nftables separada gerenciada pelo fail2ban (`f2b-table`), fora de escopo aqui (já coberta por `/seguranca`).
- Firewalld e CrowdSec **descartados** nesta rodada (ver decisão abaixo).
- Decisões-chave: portas 22/80/443 travadas no código, sem exceção nem confirmação que sobrescreva; mesmo padrão de worker no host (`scripts/firewall-worker.sh`) + fila de jobs no SQLite; remoção de regra por especificação (porta+protocolo+ação+origem), nunca por número de posição; formulário estruturado, não comando UFW livre.
- Nota pra próxima feature com esse padrão: **sempre montar diretório, nunca arquivo único**, quando o worker do host faz escrita atômica via `mv` — é exatamente o bug encontrado aqui.

## Tarefa em andamento — Excluir Projeto (teardown completo)
**Excluir projeto** — pedido pelo usuário (duas vezes, deferido até a conclusão de monitoramento/firewall).
- Spec: `docs/superpowers/specs/2026-07-21-excluir-projeto-design.md` (aprovada, commit `c97d450`).
- Plano: `docs/superpowers/plans/2026-07-21-excluir-projeto.md` (commit `9fadeda`), 6 tasks TDD:
  1. Modelo `ProjectDeleteRequest`.
  2. `GET /api/projects/{projeto}/delete-preview` (containers, volumes via `docker_client.container_inspect`, candidatas Traefik/firewall) + testes.
  3. `POST /api/projects/{projeto}/delete` (validações, fila) + testes.
  4. `scripts/project-delete-worker.sh` (worker no host, reaproveita a fila de firewall e o watcher de commit do Traefik já existentes).
  5. Modal de exclusão em `frontend/app/projetos/page.tsx`.
  6. Deploy — testar com projeto descartável, nunca `vps-monitor` nem cliente real.
- Ainda não executado — próximo passo é escolher subagent-driven-development (recomendado) ou execução inline.
- Decisões-chave: volumes incluídos já na v1 (dado real apagado); snapshot novo obrigatório como primeiro passo (não aceita snapshot antigo); rotas Traefik mostradas como candidatas pré-marcadas (usuário pode desmarcar); regras de firewall mostradas como sugestão **não marcada** (sem vínculo confiável projeto↔regra, usuário decide manualmente); projeto `vps-monitor` travado contra auto-exclusão em 2 camadas (API + worker); worker reaproveita a fila `firewall_rule_request` (não duplica lógica UFW) e o watcher de commit do Traefik já existente (não precisa de mudança nele).

## Decisão registrada: Firewalld/CrowdSec
Usuário recebeu sugestão externa de usar nftables+firewalld+CrowdSec como camada de segurança orquestrada. Avaliado e decidido:
- **nftables**: já em uso (via UFW/iptables-nft, e via tabela própria do fail2ban) — nada novo a fazer.
- **Firewalld**: descartado — não agrega nada que o padrão já estabelecido (script no host chamando o CLI da ferramenta) não resolva; sua vantagem (API D-Bus) não se encaixa no jeito que o container já acessa o host.
- **CrowdSec**: avaliado como possível upgrade futuro de detecção comportamental (fail2ban é só regex por linha de log), mas é infraestrutura nova inteira (mais um container/banco numa VPS já compartilhada) — não decidido, não é escopo de nenhuma tarefa atual.

## Tarefas anteriores (concluídas)
**Interface para editar regras do Traefik** — deployada em produção. Push (`2a33957`), deploy via `deploy.sh` (containers saudáveis, mount `/opt/traefik/dynamic` confirmado `RW=true`), cron do watcher instalado (1x/min, sem afetar outros cron jobs da VPS). Corrigido no meio do deploy: `scripts/traefik-dynamic-commit-watcher.sh` chegou não-executável na VPS (100644 no git, mesmo problema pré-existente do `fail2ban-reload-watcher.sh`) — `chmod +x` aplicado manualmente no host. Não rodei o teste funcional end-to-end (sem credenciais de login) — falta confirmação do usuário em https://monitor.dlsistemas.com.br/traefik.

- Spec: `docs/superpowers/specs/2026-07-18-gestao-traefik-design.md`.
- Plano: `docs/superpowers/plans/2026-07-18-gestao-traefik.md` (7 tasks TDD).
- Executado via `superpowers:subagent-driven-development`. Ledger canônico em `.superpowers/sdd/progress.md`.
- Decisões-chave do design (contexto pra quem for escrever o plano):
  - Escopo: só CRUD de arquivos `vps-monitor-*.yml` em `/opt/traefik/dynamic` (rotas via file provider do Traefik). Fora de escopo: labels Docker de outros projetos, editor estruturado (é YAML bruto), editar `mecanicapro.yml` (manual, só-leitura).
  - **Traefik não precisa de watcher pra aplicar config** — `file.watch: true` já recarrega sozinho; testado ao vivo em produção (arquivo YAML inválido gera só log de erro isolado, resto das rotas continua ok). Diferente do fail2ban.
  - **Watcher no host é só pro auto-commit git** (`scripts/traefik-dynamic-commit-watcher.sh`, cron, mesmo padrão do `fail2ban-reload-watcher.sh`) — evita montar `/opt/traefik/.git` + `certs/acme.json` (chave privada, permissão 600) dentro do container do monitor-backend.
  - Mesma trava de prefixo `vps-monitor-` do fail2ban pra distinguir "gerenciado" (editável) de "manual" (só-leitura).
  - Mount `/opt/traefik/dynamic` no `docker-compose.yml` muda de `:ro` pra `:rw` — DONE, commit `e2d7b78`.
  - Descoberto durante a sessão: git local (`user.name`/`user.email`) não estava configurado neste ambiente — configurado localmente (só este repo, sem `--global`) com autorização do usuário.
  - Decisão reafirmada 2x (Task 3 review + revisão final): `TraefikActionLog` não loga rejeições 403/404/409, só falha-de-validação (400) e sucesso — consistente com o padrão já em produção no fail2ban. O revisor final discordou (403 em `mecanicapro.yml` seria sinal de sondagem), mas usuário manteve a decisão original.
  - Whole-branch review encontrou e corrigiu escritas não-atômicas: `create_route`/`update_route` agora usam `_write_atomic` (tmp file + `os.replace`) pra não deixar o watcher do Traefik ler um arquivo truncado a meio-caminho. Commit `2a33957`.
  - Notas Minor não corrigidas (não bloqueantes): 500 não-logado se `TRAEFIK_DYNAMIC_DIR` não existir nos endpoints de escrita; `git commit` do watcher sem pathspec explícito; nome vazio gera `vps-monitor-.yml`; UI sem estado vazio; `.tmp` órfão fica se a escrita falhar no meio.

## Tarefa anterior (concluída)
**Listagem de Projetos da VPS** — feature completa, deploy em produção, aguardando só confirmação visual do usuário em https://monitor.dlsistemas.com.br/projetos.
- Spec: `docs/superpowers/specs/2026-07-18-listagem-projetos-design.md`
- Plano: `docs/superpowers/plans/2026-07-18-listagem-projetos.md` (4 tasks, TDD, com código completo em cada step)
- Executado via `superpowers:subagent-driven-development`. Ledger canônico em `../.superpowers/sdd/progress.md` (raiz do repo git, `workspace9/`, não `monitor/`).
- **Task 1** (labels em `collect_all()`): concluída, commit `138e6ed`, review clean.
- **Task 2** (endpoint `GET /api/projects`): concluída, commits `138e6ed..2aee797`, review clean após 1 rodada de fix.
  - Achado no review: os 4 testes "puros" de `test_projects_api.py` (`_dominio_por_labels`/`_dominio_por_arquivo_dinamico`), como escritos no plano, falhavam quando o arquivo rodava isolado — `api/projects.py` importa em cadeia até `api/auth.py`, que levanta `RuntimeError` se `JWT_SECRET` não estiver setado, e esses 4 testes não passam pela fixture `auth_client` que seta essa env var. Só passavam dentro da suíte completa (ordem de import mascarava o problema).
  - Usuário optou por fix mínimo, só no arquivo de teste: fixture `autouse` setando `JWT_SECRET` antes desses testes. Nenhum código de produção (`api/auth.py`, `api/projects.py`) foi alterado. Commit `2aee797`.
- **Task 3** (montar volume read-only do Traefik no `docker-compose.yml`): concluída, commit `a7cd23e`. Reviewer inicialmente reportou indentação inconsistente (falso positivo — confirmado via `cat -A` que todas as linhas do bloco `volumes:` usam 6 espaços, incluindo a nova; o reviewer contou o marcador `+` do diff como parte da indentação). Aprovada após override do controller.
- **Task 4** (página `/projetos` no frontend): concluída, commit `867160e`, `npm run build` limpo, review clean. Notas Minor não corrigidas (herdadas do código de exemplo do plano): flicker de 1 frame no loading, sem acessibilidade por teclado no card expansível.
- **Revisão final whole-branch** (base `0e56193`, head `867160e`, 5 commits): **Ready to merge: Yes**. Confirmado alinhamento de contrato entre as 4 tasks (labels → endpoint → volume → frontend, nomes de env var, shape do payload TS vs JSON real). Só notas Minor, nenhuma bloqueante:
  - `_HOST_RE` em `api/projects.py` duplica (com extensão proposital pra `HostRegexp`) a regex de `api/access_logs.py` — considerar constante compartilhada no futuro.
  - Domínio do mecanicapro (via arquivo dinâmico) exibe o padrão bruto `{subdomain:[a-z0-9-]+}.dlsistemas.com.br`, não um domínio real — esperado, dentro do escopo definido na spec.
  - `deploy.sh` já roda `docker compose up -d` (não só restart), então o volume novo do Traefik será aplicado corretamente no deploy — nenhuma ação extra necessária, só confirmar que o deploy usa `deploy.sh`.
- **Feature concluída.** Push feito (`origin/main` em `867160e`), deploy rodado (`git pull --ff-only && bash monitor/deploy.sh` na VPS), containers recriados e saudáveis. Falta só o usuário conferir visualmente em https://monitor.dlsistemas.com.br/projetos.

## Contexto necessário
- Projeto: VPS Monitor (monitoramento de VPS — backend + frontend + docker-compose), raiz do repo em `C:\Users\dougl\workspace9`, código em `monitor/`.
- Preferência do usuário: trabalhar sempre na `main`, não criar worktrees para novas tarefas.
- A VPS de produção (144.91.92.70) é compartilhada com ~30 containers de múltiplos projetos/clientes (ver memória `project_vps_monitor_deploy`).
- **Padrão importante descoberto em sessão anterior**: comandos que precisam enxergar o filesystem "inteiro" do host (não só caminhos específicos montados) — como `fail2ban-client reload`, que valida os logpaths de TODOS os jails configurados, não só o alvo — não funcionam de forma confiável de dentro de um container, mesmo com mounts pontuais. A solução usada foi um script rodando via cron **no host** (fora de qualquer container) que detecta mudanças feitas pelo monitor e aplica a ação real por lá. Esse padrão já existe duas vezes no projeto: `scripts/fail2ban-reload-watcher.sh` (este repo) e `sync-tenant-allowlist.sh` (mecanicapro, sessão anterior). Vale considerar esse padrão de novo se a gestão de Traefik (próximo item do backlog) esbarrar em problema parecido.

## Concluído (histórico resumido)
- Fase 1: coleta de métricas, containers, alertas básicos, acessos por IP/sistema/projeto.
- Histórico de notificações de alertas, modal de regras de alerta, bloqueio de subdomínios fantasmas no mecanicapro.
- Gestão de containers "lixo" via UI, fixes em /acessos (abas, clique no sistema, filtro "Todos").
- Bug do access log (rotação copytruncate) corrigido e verificado em produção.
- Limpeza de disco na VPS (81% → 13%) + alerta automatizado de limpeza de disco (build cache + imagens órfãs, via sistema de alertas já existente).
- **Gestão de fail2ban via UI (`/seguranca`)** — criar/editar/excluir jails próprios (prefixo `vps-monitor-`, com validação real via `fail2ban-regex` antes de aplicar), jails manuais em modo leitura, desbanir IP disponível pra qualquer jail.
  - **Problema real encontrado e corrigido durante a verificação de deploy**: o design original chamava `fail2ban-client reload`/`stop` direto de dentro do container do monitor. Descobri que isso falha silenciosamente (ou com erro genérico) porque o fail2ban-client valida do lado do cliente os logpaths de TODOS os jails configurados (não só o alvo), e o container não enxerga logs de outros projetos (ex: mecanicapro, `/var/log/auth.log`). Testei direto no host (funcionou) vs. via container (falhou) pra confirmar a causa raiz.
  - Fix: `create_jail`/`update_jail`/`delete_jail` só escrevem/removem arquivos de config (validados via dry-run, que não tem esse problema). Um script novo, `scripts/fail2ban-reload-watcher.sh`, roda via cron **no host** (1x/minuto) e aplica o reload real de lá. `unban_ip` continua direto do container (confirmado sem esse problema).
  - Verificado end-to-end em produção: criar via API → watcher detecta e ativa (confirmado em `fail2ban-client status`) → excluir via API → watcher detecta e desativa — sem afetar os jails já existentes (`sshd`, `mecanicapro-ghost-subdomain`) em nenhum momento do ciclo.
  - Commits: `6ed71e6`, `a52338e`, `ef3f2ad`, `3a37039`, `90e349f` (implementação inicial) + `a858e7e`, `7e7b046` (correções pós-deploy).

## Backlog original — 100% com código em produção
1. Listagem de projetos da VPS — ✅ deployado, aguardando confirmação visual do usuário.
2. Interface para editar regras do Traefik — ✅ deployado, aguardando confirmação funcional do usuário.
3. Backup/restore de projetos da VPS — ✅ deployado e testado end-to-end pelo agente no próprio `vps-monitor`; aguardando ok do usuário antes do primeiro uso em projeto de cliente real.

## Próxima tarefa (ordem desta sessão)
1. **Fechar lacunas de monitoramento** — em andamento, indo pro plano agora (ver "Tarefa em andamento" acima).
2. **Firewall (UFW)** — spec pronta, esperando revisão do usuário, depois vira plano.
3. **Excluir projeto (teardown completo)** — ainda sem brainstorming. Envolve: parar/remover containers do projeto, remover volumes, remover regras de firewall associadas (depende da feature de firewall acima existir?), remover rotas do Traefik (reaproveita `vps-monitor-*.yml` do `/traefik`), com confirmação forte (mesmo padrão de digitar o nome do projeto usado no restore de backup). Considerar se deve exigir/oferecer um snapshot de backup antes de excluir.

Pendências que dependem só do usuário (não bloqueiam trabalho novo):
- Confirmar visualmente/funcionalmente as 3 páginas do backlog original em produção.
- Autorizar o primeiro uso do backup/restore num projeto de cliente real.

## Contexto necessário
Ver seções "Tarefa em andamento" e "Tarefa em fila" acima.
