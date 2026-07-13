# Acessos agrupados por serviço + gráficos de acessos e recursos por projeto

## Contexto

A tela "Acessos" (entregue em [[2026-07-12-acessos-por-ip-design]]) lista hoje
por **IP**, com os sistemas acessados como detalhe secundário. Para quem opera
a VPS, a pergunta mais comum é a inversa: "quanto o `circuitodascorridas`
recebeu de acesso, e quem acessou?". Esta entrega:

1. Reordena a tabela de Acessos para listar por **sistema/serviço** primeiro
   (total de acessos na frente), com um toggle para expandir e ver os IPs
   daquele sistema (contagem por IP + data do acesso).
2. Adiciona, na mesma página, um **gráfico de linhas de acessos por
   projeto**, com filtro Dia (últimas 12h ou um dia específico) e Mês (mês
   selecionado, um ponto por dia).
3. Adiciona, ao lado, um **gráfico de recursos utilizados** (CPU/RAM/rede)
   pelo container por trás do sistema selecionado — quando existir um
   container Docker roteado pelo Traefik para aquele domínio.

Não há mudança de infraestrutura fora deste repositório: tudo reaproveita o
access log do Traefik e a coleta de métricas de containers já existentes.

## 1. Tabela por sistema

### Backend (`backend/api/access_logs.py`)

Novo endpoint, ao lado do `/summary` existente (que continua existindo, sem
uso na página, mas mantido por não ter custo de manutenção e poder servir
outras integrações):

```
GET /api/access-logs/summary-por-sistema?ip=&days=30
```

Agrega `AccessLogDaily` (mesmo filtro de janela e de `ip` por prefixo do
`/summary`), mas agrupando por `sistema` primeiro e por `ip` dentro dele:

```json
[
  {
    "sistema": "circuitodascorridas.dlsistemas.com.br",
    "total_acessos": 530,
    "ips": [
      {"ip": "203.0.113.10", "count": 120, "ultimo_acesso": "2026-07-12"},
      {"ip": "198.51.100.4", "count": 80, "ultimo_acesso": "2026-07-11"}
    ]
  }
]
```

Ordenado por `total_acessos` desc; `ips` por `count` desc. Se `ip` for
informado, o filtro já restringe as linhas de `AccessLogDaily` antes de
agregar — sistemas sem nenhuma linha correspondente somem da resposta, e o
`total_acessos` reflete só o que aquele IP gerou (mesmo comportamento do
`/summary?ip=` já existente, só invertendo o agrupamento).

`GET /api/access-logs/sistemas` continua existindo e passa a alimentar o
seletor de projeto do gráfico (seção 3), não mais um filtro da tabela.

### Frontend (`frontend/app/acessos/page.tsx`)

- Remove o dropdown "Sistema" (sistema agora é a própria linha da tabela).
- Mantém seletor de período (24h/7d/30d) e o campo "Filtrar por IP".
- Tabela: uma linha por sistema — **Sistema** | **Total de acessos** | botão
  de expandir (▸/▾). Estado local `expandido: Set<string>` (sistemas
  abertos).
- Ao expandir, mostra sub-tabela **IP | Acessos | Último acesso** (mesmo
  `fmtRelativeDay` já usado). Clicar num IP abre o `AccessIpModal` já
  existente (mantém geo + acessos recentes daquele IP, sem mudança nele).
- Estado vazio inalterado ("Nenhum acesso registrado no período.").

## 2. Mapeamento sistema → container (labels do Traefik)

O `monitor-backend` já acessa `docker.sock` (usado hoje para listar
containers na tela Containers). O Docker API retorna, por container, o campo
`Labels`. Quando o Traefik usa o provider Docker, cada container da aplicação
carrega labels como:

```
traefik.http.routers.circuitodascorridas.rule = Host(`circuitodascorridas.dlsistemas.com.br`)
```

Novo endpoint:

```
GET /api/access-logs/container-para-sistema?sistema=circuitodascorridas.dlsistemas.com.br
→ {"container_name": "circuitodascorridas-app"}   // ou {"container_name": null}
```

Implementação: chama `docker_client.list_containers()` (raw, com `Labels`),
para cada container varre as labels cuja chave casa
`^traefik\.http\.routers\.[^.]+\.rule$`, e extrai todos os domínios do valor
com a regex:

```python
HOST_RE = re.compile(r"Host\(`([^`]+)`\)")
```

(cobre regras com múltiplos `Host(...)` combinados por `||`, ex.
`` Host(`a.com`) || Host(`b.com`) ``). Retorna o primeiro container cujo
domínio bate com `sistema`. Sem cache: chamada só ocorre quando o usuário
abre o gráfico de recursos de um projeto.

Se nenhum container casar (app não está neste host, ou não usa o provider
Docker do Traefik), retorna `container_name: null` e o frontend mostra
"Recursos não disponíveis para este projeto".

## 3. Série temporal de acessos

### Modelo de dados (`backend/models/database.py`)

Nova tabela, só para o gráfico (sem dimensão de IP — a quebra por IP já
existe na tabela por sistema da seção 1):

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

**Retenção:** limpa em `_cleanup()` pelo mesmo `retention_detailed_days`
usado por `AccessLog` (padrão 7 dias) — é dado fino, mesmo espírito do log
detalhado.

### Coletor (`backend/collector/access_log_tailer.py`)

`_process_line` passa a chamar, ao lado do `_upsert_daily` existente:

```python
_upsert_hourly(session, accessed_at.strftime("%Y-%m-%d %H"), sistema)
```

com `_upsert_hourly` análogo ao `_upsert_daily` (select + increment ou
insert, chave lógica `(hour, sistema)`).

### API (`backend/api/access_logs.py`)

```
GET /api/access-logs/timeseries?sistema=&granularity=hour|day&day=&month=
```

- `granularity=hour`, sem `day`: últimas 12h corridas (UTC, âncora na hora
  cheia mais recente), lendo `AccessLogHourly`.
- `granularity=hour` + `day=YYYY-MM-DD`: aquele dia inteiro, 00h–23h UTC (se
  for o dia de hoje, só até a hora atual).
- `granularity=day` + `month=YYYY-MM`: um ponto por dia do mês (todos os dias
  se for mês passado; até hoje se for o mês corrente), lendo
  `AccessLogDaily` filtrado por `sistema` e `day LIKE 'YYYY-MM%'`.

Resposta no mesmo formato já usado por `/metrics/history` (compatível com o
componente `LineChart` existente):

```json
{ "granularity": "hour", "data": [{"ts": "2026-07-13T09:00:00Z", "value": 42}, ...] }
```

Buckets sem nenhum acesso aparecem com `value: 0` (não `null` — diferente de
métricas de host, aqui a ausência de dado é "zero acessos", não "sem
amostra").

## 4. Gráfico "Acessos por projeto" (frontend)

Nova seção na página Acessos, abaixo da tabela. Novo componente
`frontend/components/AccessProjectCharts.tsx`:

- Tabs **Dia / Mês** (mesmo padrão visual de botão de aba já usado).
  - Dia: toggle "Últimas 12h" (default) vs. `<input type="date">` (dia
    específico).
  - Mês: `<input type="month">`, default = mês atual.
- Seletor **Projeto**: dropdown de `/access-logs/sistemas` (default = o
  sistema com mais acesso no período corrente da tabela, ou o primeiro da
  lista se a tabela estiver vazia).
- Chama `/access-logs/timeseries` com os filtros acima e renderiza com
  `LineChart` (`unit=""`, `label="Acessos"`).

## 5. Gráfico "Recursos utilizados" (novo endpoint + frontend)

### Backend (`backend/api/metrics.py`)

```
GET /api/metrics/container-history?container_name=&granularity=hour|day&day=&month=
```

Mesma lógica de bucketing de `/access-logs/timeseries` (hora ou dia,
mesmas regras de "últimas 12h" / dia específico / mês), mas lendo
`ContainerMetrics` (já coletado a cada 30s, retido por
`retention_aggregated_days`) filtrado por `container_name`, calculando a
**média** de `cpu_percent`, `mem_percent`, `net_rx_mb`, `net_tx_mb` por
bucket:

```json
{
  "granularity": "hour",
  "data": [
    {"ts": "2026-07-13T09:00:00Z", "cpu_percent": 12.4, "mem_percent": 38.1, "net_rx_mb": 4.2, "net_tx_mb": 1.1}
  ]
}
```

Bucket sem amostra → campos `null` (aqui falta de coleta é falta de amostra,
diferente do gráfico de acessos).

### Frontend

Dentro de `AccessProjectCharts.tsx`, ao trocar o projeto selecionado: chama
`/access-logs/container-para-sistema`. Se retornar um `container_name`:

- Mostra abas de métrica **CPU / RAM / Rede ↓ / Rede ↑** (mesmo padrão de
  `historico/page.tsx`), alimentando `/metrics/container-history` com os
  mesmos filtros Dia/Mês da seção 4, renderizado com `LineChart`.

Se `container_name` for `null`: mostra "Recursos não disponíveis para este
projeto (nenhum container do Traefik encontrado para este domínio)." no
lugar do gráfico.

## Testes

- `test_access_logs_api.py`: `/summary-por-sistema` agrega corretamente por
  sistema→ip e respeita filtro de `ip`; `/container-para-sistema` acha o
  container certo a partir de labels simuladas do Traefik (incluindo regra
  com múltiplos `Host(...)` via `||`) e retorna `null` quando nenhum bate;
  `/timeseries` cobre os três modos (últimas 12h, dia específico, mês) e
  zera buckets sem dado.
- `test_access_log_tailer.py`: nova asserção de que uma linha processada
  também incrementa `AccessLogHourly` na chave `(hour, sistema)` certa.
- `test_metrics_api.py`: `/metrics/container-history` agrega média por
  bucket corretamente e usa `null` (não zero) quando não há amostra no
  bucket.

## Fora de escopo

- Qualquer forma de mapeamento manual sistema→container (o mapeamento via
  labels do Traefik cobre o caso; se não houver labels, o gráfico de
  recursos simplesmente fica indisponível para aquele projeto).
- Multi-seleção de projetos no gráfico (comparar vários sistemas ao mesmo
  tempo) — o filtro é sempre um projeto por vez.
- Fuso horário local configurável nos filtros Dia/Mês — dia e mês são
  tratados em UTC, mesmo padrão já usado pelo resto do sistema de acessos.
- Alteração do endpoint `/summary` (por IP) e do `AccessIpModal` — ambos
  continuam existindo como estão hoje.
