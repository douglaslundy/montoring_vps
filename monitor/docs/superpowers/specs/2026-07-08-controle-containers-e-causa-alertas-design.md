# Controle de containers e causa provável dos alertas

## Contexto

O VPS Monitor hoje só observa: mostra métricas, dispara alertas e permite ver
logs de containers, mas não permite agir sobre eles (start/stop/restart tem
que ser feito via SSH). Além disso, quando um alerta dispara e resolve (ex.
"Load Alto: 7.5 > 6.0"), a única informação registrada é a métrica, o valor e
o threshold — não há nenhum indício de *o que* causou o problema, o que
dificulta identificar padrões e planejar melhorias de infraestrutura.

Esta entrega cobre duas features independentes, mas relacionadas (ambas
tocam a página de Containers e o motor de alertas), entregues juntas:

- **A. Controle de containers** — botões de iniciar/parar/reiniciar.
- **B. Causa provável dos alertas** — snapshot de contexto capturado no
  momento do disparo de cada alerta, cobrindo todos os 9 tipos de regra
  padrão.

## Feature A — Controle de containers

### A1. `DockerClient` (`backend/collector/docker_client.py`)

Três métodos novos, seguindo o mesmo padrão dos já existentes (UDS para
`/var/run/docker.sock`):

```python
async def start_container(self, container_id: str) -> None
async def stop_container(self, container_id: str, timeout: int = 10) -> None
async def restart_container(self, container_id: str, timeout: int = 10) -> None
```

- `POST /containers/{id}/start`, `/stop?t={timeout}`, `/restart?t={timeout}`.
- HTTP 304 (ação já é o estado atual, ex. start em container já rodando) é
  tratado como sucesso silencioso, não erro.
- HTTP 404 levanta exceção específica (`ContainerNotFoundError`) tratada na
  API como 404.

### A2. API (`backend/api/containers.py`)

```
POST /api/containers/{container_id}/start
POST /api/containers/{container_id}/stop
POST /api/containers/{container_id}/restart
```

- Herdam a proteção JWT já aplicada ao `containers_router` em `main.py`
  (`_protected`), nenhuma mudança de auth necessária.
- Cada chamada grava uma linha em `ContainerActionLog` (ver A4), com
  `username` extraído do token (via `get_token_data`), resultado e mensagem
  de erro se houver.
- Resposta: `{"ok": true}` ou HTTP 4xx/5xx com detalhe do erro do Docker.

### A3. Frontend (`frontend/components/ContainerRow.tsx`, `frontend/app/containers/page.tsx`)

- Três botões de ícone na coluna "Ações", ao lado de "Ver Logs":
  - ▶ **Iniciar** — habilitado quando `status !== 'running'`.
  - ⟳ **Reiniciar** — habilitado quando `status === 'running'`.
  - ⏹ **Parar** — habilitado quando `status === 'running'`.
- Clique em parar/reiniciar abre modal de confirmação (mesmo padrão visual
  do modal de logs já existente). Texto padrão: "Tem certeza que deseja
  parar/reiniciar o container `<nome>`?".
- Para `monitor-backend`, `monitor-frontend`, `monitor-nginx`: o modal
  troca o texto por um aviso mais forte — "Este é um container do próprio
  VPS Monitor. Pará-lo/reiniciá-lo pode derrubar o painel de monitoramento
  temporariamente. Deseja continuar?" — mas a ação **não é bloqueada**, só
  exige o mesmo clique de confirmação com texto diferente.
- Botão "Iniciar" não precisa de confirmação (ação não destrutiva).
- Durante a chamada, o botão clicado mostra spinner e fica desabilitado;
  erros aparecem como toast/mensagem inline na linha do container.
- Atualização de status: nenhum polling extra — o WebSocket de métricas já
  atualiza a cada 30s: o status vai refletir a mudança no próximo ciclo.

### A4. Log de ações (`backend/models/database.py`)

Nova tabela:

```python
class ContainerActionLog(Base):
    __tablename__ = "container_action_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    performed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    username = Column(String, nullable=False)
    container_id = Column(String, nullable=False)
    container_name = Column(String, nullable=False)
    acao = Column(String, nullable=False)  # start | stop | restart
    sucesso = Column(Integer, default=1)
    erro = Column(Text, nullable=True)
```

Sem tela dedicada nesta entrega — existe para auditoria/depuração futura
(consulta direta ao banco ou endpoint de leitura numa fase posterior).

### A5. `docker-compose.yml`

Remove `:ro` do mount `/var/run/docker.sock:/var/run/docker.sock` — reflete
que o backend agora tem controle real sobre os containers do host (o `:ro`
nunca bloqueou isso, só dava falsa sensação de restrição). Requer
`docker compose up -d` para recriar `monitor-backend` após o deploy.

## Feature B — Causa provável dos alertas

### B1. Nova coleta: uso de disco por container

`DockerClient.list_containers_with_size()` — `GET /containers/json?all=true&size=true`,
que já retorna `SizeRw` (camada gravável — o que mais indica crescimento
recente) e `SizeRootFs` (tamanho total incluindo imagem) por container, sem
precisar rodar `du` manualmente.

Job novo em `scheduler.py`, rodando a cada **10 minutos** (mais espaçado que
a coleta de métricas de 30s, porque calcular tamanho em disco é uma operação
mais pesada para o daemon Docker):

```python
scheduler.add_job(collect_disk_usage, "interval", minutes=10, id="disk_usage")
```

Nova tabela:

```python
class ContainerDiskUsage(Base):
    __tablename__ = "container_disk_usage"
    id = Column(Integer, primary_key=True, autoincrement=True)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    container_id = Column(String, nullable=False)
    container_name = Column(String, nullable=False)
    size_rw_mb = Column(Float)
    size_rootfs_mb = Column(Float)
```

Retenção: reaproveita `retention_aggregated_days` (mesma política já usada
para `ContainerMetrics`), limpo no job `_cleanup` existente.

Trade-off assumido: como a coleta é a cada 10 min, o contexto de um alerta
de Disco pode refletir dados com até 10 min de atraso — aceitável porque uso
de disco cresce devagar comparado a CPU/RAM/Load.

### B2. Captura de contexto (`backend/notifications/alert_engine.py`)

Nova coluna em `AlertLog`:

```python
contexto = Column(Text, nullable=True)  # JSON
```

Migração leve em `init_db()`, mesmo padrão de `vps_name`/`last_notified_at`:
`ALTER TABLE alert_log ADD COLUMN contexto TEXT` dentro de `try/except`.
Sem backfill — alertas antigos ficam com `contexto = None` (frontend trata
como "sem dados de contexto disponíveis para este alerta").

`evaluate()` passa a receber a instância de `docker_client` (já existe em
`scheduler.py`, só precisa ser passada como parâmetro):

```python
async def evaluate(metrics: dict, containers: list, docker_client) -> list
```

Ao criar um `AlertLog` novo (dentro de `_evaluate_rule`, no branch
`condition_true and open_log is None`, e em `_evaluate_container_stopped`),
monta o `contexto` conforme a métrica:

| Métrica (`rule.metrica`) | Contexto capturado |
|---|---|
| `cpu_percent`, `load_1m` | top 3 containers por `cpu_percent`, top 3 por tráfego de rede (`net_rx_mb + net_tx_mb`) |
| `ram_percent` | top 3 containers por `mem_percent`, top 3 por tráfego de rede |
| `disk_percent` | top 3 containers por `size_rw_mb` → chave `top_disco` (última leitura de `ContainerDiskUsage`, consultada via `session`) |
| `temperature_c` | nenhum — não há como atribuir a um container (limitação de hardware); `contexto = None` |
| `container_stopped` | resultado de `docker_client.container_inspect(container_id)`: `exit_code` (`State.ExitCode`), `oom_killed` (`State.OOMKilled`), `erro` (`State.Error`), `finalizado_em` (`State.FinishedAt`) |

Chaves JSON usadas: `top_cpu`, `top_mem`, `top_rede`, `top_disco` (listas de
`{"nome": str, "valor": float}`, exceto `top_disco` que usa
`{"nome": str, "valor_mb": float}`), e para `container_stopped`:
`exit_code`, `oom_killed`, `erro`, `finalizado_em` (chaves soltas, sem lista).

Formato JSON, exemplo para CPU Alta:

```json
{
  "top_cpu": [{"nome": "api-service", "valor": 87.2}, {"nome": "worker", "valor": 40.1}],
  "top_rede": [{"nome": "api-service", "valor_mb": 320.5}]
}
```

Exemplo para Container Parado:

```json
{"exit_code": 137, "oom_killed": true, "erro": "", "finalizado_em": "2026-07-08T21:58:03Z"}
```

Falhas ao montar contexto (ex. `container_inspect` falha porque o container
já foi removido) são capturadas com `try/except` e resultam em
`contexto = None` — nunca impedem a criação do `AlertLog` em si.

### B3. API (`backend/api/alerts.py`)

`_log_dict()` passa a incluir `"contexto": json.loads(a.contexto) if a.contexto else None`
— propaga automaticamente para `/api/alerts/active` e `/api/alerts/history`.

### B4. Frontend (`frontend/app/alertas/page.tsx`)

Aba **Histórico**: cada linha ganha o mesmo padrão de expandir/recolher já
usado na página de Containers (seta ▶/▼ + linha de detalhe). Conteúdo
formatado por tipo de `contexto`:

- `top_cpu`/`top_mem`/`top_rede`: lista "container — valor".
- `top_disco`: lista "container — X MB (camada gravável)".
- `exit_code`/`oom_killed`: linha única, ex. "Motivo: finalizado por falta de
  memória (OOM Killed)" quando `oom_killed=true`, ou "Código de saída: 1"
  caso contrário.
- `contexto === null`: "Sem dados de contexto disponíveis para este alerta."

Interface TS `AlertLog` ganha `contexto: Record<string, any> | null`.

## Testes

- `test_docker_client.py`: `start_container`/`stop_container`/`restart_container`
  tratam 304 como sucesso e 404 como erro específico;
  `list_containers_with_size` faz parse correto de `SizeRw`/`SizeRootFs`.
- `test_containers_api.py`: endpoints de start/stop/restart exigem token;
  chamam o método certo do `docker_client`; gravam `ContainerActionLog`
  (sucesso e falha).
- `test_alert_engine.py`: para cada tipo de regra (`cpu_percent`,
  `ram_percent`, `disk_percent`, `temperature_c`, `load_1m`,
  `container_stopped`), o `contexto` gravado tem o formato esperado;
  `temperature_c` grava `contexto = None`; falha em `container_inspect`
  não impede criação do `AlertLog`.
- `test_alerts_api.py`: `/alerts/active` e `/alerts/history` retornam
  `contexto` desserializado; alertas antigos (`contexto IS NULL`) retornam
  `None` sem erro.
- `test_scheduler.py` (ou novo `test_disk_usage.py`): job de coleta de disco
  grava linhas corretas em `ContainerDiskUsage`; `_cleanup` remove linhas
  antigas conforme `retention_aggregated_days`.

## Fora de escopo

- Contagem real de conexões/acessos simultâneos por container (exigiria
  instrumentar cada aplicação individualmente, ex. `nginx stub_status` ou
  métricas próprias da app) — o tráfego de rede (RX/TX) serve como proxy
  disponível hoje, sem esse nível de precisão.
- Atribuição de causa a alertas de Temperatura (métrica é do hardware, sem
  relação direta com containers).
- Backfill de `contexto` para alertas já resolvidos antes desta entrega.
- Tela dedicada de auditoria para `ContainerActionLog` (a tabela existe,
  mas sem UI própria nesta entrega).
- RBAC/múltiplos usuários com permissões diferentes para controle de
  containers — o sistema continua com um único usuário administrador.
- Bloqueio automático de start/stop/restart nos containers do próprio
  monitor — apenas aviso reforçado na confirmação.
