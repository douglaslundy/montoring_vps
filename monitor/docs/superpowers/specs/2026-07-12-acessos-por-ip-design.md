# Registro de acessos ao sistema por IP

## Contexto

A VPS monitorada hospeda vários projetos em produção além do próprio VPS
Monitor, todos atrás do Traefik (rede externa `proxy`, já usada pelo
`monitor-nginx` em `docker-compose.yml`). Hoje não existe nenhum registro de
quem acessa o quê: não sabemos quantos acessos cada IP fez, a quais sistemas,
nem de onde eles vêm.

Esta entrega adiciona uma tela "Acessos" ao painel, mostrando, por IP: quantos
acessos fez e a quais sistemas (domínios) hospedados na VPS — incluindo o
próprio monitor, que conta como mais um sistema. Clicar num IP abre um modal
com geolocalização e o detalhe das requisições recentes daquele IP.

Fonte dos dados: o **access log do Traefik em JSON**, lido de forma passiva
(tail) pelo `monitor-backend`. Não há mudança no caminho de tráfego de nenhum
sistema hospedado — o monitor só lê um arquivo de log.

## Pré-requisito de infraestrutura (fora deste repositório)

O Traefik desta VPS roda numa stack separada (não faz parte deste repo — só
é referenciado via labels/rede externa `proxy`). Antes desta feature
funcionar em produção, é preciso, na stack do Traefik:

1. Habilitar access log em JSON (flags estáticas ou `traefik.yml`):
   ```yaml
   accessLog:
     filePath: "/var/log/traefik/access.log"
     format: json
   ```
2. Garantir que o arquivo fique num volume Docker nomeado (ex.
   `traefik_access_logs`) montado no container do Traefik em
   `/var/log/traefik`.
3. Esse mesmo volume precisa existir como **externo** para o
   `monitor-backend` montar read-only (ver mudança em `docker-compose.yml`
   abaixo). Se o volume for criado pela stack do Traefik com
   `driver: local` e nome `traefik_access_logs`, basta declará-lo como
   `external: true` neste projeto.
4. Se o Traefik estiver atrás de outro proxy/CDN antes dele, `trustedIPs`
   precisa estar configurado corretamente para que `ClientHost` no log seja
   o IP real do visitante — fora do escopo desta entrega, mas assumido como
   pré-requisito para os IPs registrados fazerem sentido.

Sem esse arquivo presente, o coletor apenas loga um aviso uma vez e não
grava nada (ver "Coletor", abaixo) — não impede o resto do monitor de
funcionar.

## Modelo de dados (`backend/models/database.py`)

```python
class AccessLog(Base):
    __tablename__ = "access_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    accessed_at = Column(DateTime, nullable=False)
    ip = Column(String, nullable=False)
    sistema = Column(String, nullable=False)   # RequestHost do Traefik
    path = Column(String, nullable=False)
    method = Column(String, nullable=False)
    status_code = Column(Integer)
    user_agent = Column(Text, nullable=True)


class AccessLogDaily(Base):
    __tablename__ = "access_log_daily"
    id = Column(Integer, primary_key=True, autoincrement=True)
    day = Column(String, nullable=False)       # "YYYY-MM-DD", UTC
    ip = Column(String, nullable=False)
    sistema = Column(String, nullable=False)
    count = Column(Integer, nullable=False, default=0)
    # unicidade lógica por (day, ip, sistema), garantida em código
    # (upsert manual, sem UniqueConstraint pra evitar migração complexa em SQLite)


class IpGeoCache(Base):
    __tablename__ = "ip_geo_cache"
    ip = Column(String, primary_key=True)
    country = Column(String, nullable=True)
    region = Column(String, nullable=True)
    city = Column(String, nullable=True)
    isp = Column(String, nullable=True)
    org = Column(String, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    is_private = Column(Integer, default=0)
    looked_up_at = Column(DateTime, nullable=False, default=datetime.utcnow)
```

Índices: `AccessLog` por `(accessed_at)` e `(ip)`; `AccessLogDaily` por
`(day)` e `(ip)` — usados nos filtros da API. Criados via `Index(...)` no
mesmo arquivo, seguindo o padrão SQLAlchemy já usado no projeto (tabelas
existentes não têm índices explícitos além da PK, então isso é aditivo).

**Retenção** (reaproveita as duas configs já existentes em Configurações,
sem criar novas): `AccessLog` limpo por `retention_detailed_days` (padrão 7
dias), `AccessLogDaily` limpo por `retention_aggregated_days` (padrão 30
dias) — ambos no job `_cleanup` existente em `scheduler.py`. `IpGeoCache`
nunca é limpo automaticamente (dado pequeno, um registro por IP visto).

## Coletor (`backend/collector/access_log_tailer.py`)

```python
async def tail_access_log() -> None
```

- Lê `TRAEFIK_ACCESS_LOG_PATH` (env var, default
  `/var/log/traefik/access.log`).
- Guarda posição de leitura (`byte offset` + `inode` do arquivo) em duas
  chaves da tabela `Config` existente (`access_log_offset`,
  `access_log_inode`) — sobrevive a restart do container. Se o inode mudar
  (rotação de log), reseta offset para 0.
- Se o arquivo não existir: loga aviso uma única vez por processo (flag em
  memória) e não falha o job.
- Para cada linha nova, faz `json.loads`; linhas inválidas são ignoradas
  (log em nível debug, não interrompe o processamento das demais).
- Extrai `ClientHost`, `RequestHost`, `RequestPath`, `RequestMethod`,
  `DownstreamStatus`, `time` (RFC3339), `request_User-Agent` (se presente).
- **Filtro de ruído** — descarta a linha se:
  - a extensão do path (últimos `.xxx`) está em
    `{js, css, map, png, jpg, jpeg, gif, svg, ico, woff, woff2, ttf, webp, avif}`;
  - o path é `/favicon.ico`, `/robots.txt`, `/health`, `/healthz`, ou começa
    com `/.well-known/`.
- Linhas que passam o filtro: insere uma linha em `AccessLog` e faz upsert
  (select + update/insert) do contador do dia em `AccessLogDaily` para
  `(day, ip, sistema)`.
- Roda como job do APScheduler a cada 15s (`scheduler.add_job(tail_access_log, "interval", seconds=15, id="access_log_tail")`), registrado em `start_scheduler()`.

## Geolocalização (`backend/collector/geoip.py`)

```python
async def lookup_ip(ip: str, session: Session) -> dict
```

- Se `ip` já está em `IpGeoCache`, retorna do cache (sem chamada externa).
- Se `ip` é privado/loopback (`10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`, `127.0.0.0/8`, `::1`, `fc00::/7`), grava
  `is_private=1` sem chamar a API externa e retorna
  `{"is_private": True, ...campos None}`.
- Caso contrário, chama `http://ip-api.com/json/{ip}?fields=status,message,country,regionName,city,isp,org,lat,lon,query`
  via `httpx` (já é dependência do projeto), timeout curto (5s). Em caso de
  erro/timeout, grava um registro com `looked_up_at=now` e campos `None`
  (evita ficar tentando de novo a cada request — próxima tentativa só
  quando o cache for invalidado manualmente, fora de escopo).
- Resultado persistido em `IpGeoCache` antes de retornar.

## API (`backend/api/access_logs.py`)

```python
router = APIRouter(prefix="/api/access-logs", dependencies=[Depends(verify_token_header)])
```

- `GET /api/access-logs/summary?sistema=&ip=&days=30`
  Agrega `AccessLogDaily` (filtra `day >= hoje - days`, e por `sistema`/`ip`
  se informados; filtro de `ip` é *prefix match* com `LIKE`). Retorna, por
  IP, ordenado por total desc:
  ```json
  [
    {
      "ip": "203.0.113.10",
      "total_acessos": 128,
      "sistemas": [{"sistema": "app2.dlsistemas.com.br", "count": 100}, {"sistema": "monitor.dlsistemas.com.br", "count": 28}],
      "primeiro_acesso": "2026-07-05",
      "ultimo_acesso": "2026-07-12"
    }
  ]
  ```
- `GET /api/access-logs/sistemas` — `SELECT DISTINCT sistema FROM access_log_daily ORDER BY sistema`, para popular o filtro no frontend.
- `GET /api/access-logs/ip/{ip}?days=30`
  ```json
  {
    "ip": "203.0.113.10",
    "geo": {"is_private": false, "country": "Brazil", "region": "SP", "city": "São Paulo", "isp": "...", "org": "...", "lat": -23.5, "lon": -46.6},
    "total_acessos": 128,
    "sistemas": [{"sistema": "app2.dlsistemas.com.br", "count": 100, "ultimo_acesso": "2026-07-12T14:03:00Z"}],
    "acessos_recentes": [{"sistema": "app2.dlsistemas.com.br", "path": "/api/pedidos", "method": "GET", "status_code": 200, "accessed_at": "2026-07-12T14:03:00Z"}]
  }
  ```
  `acessos_recentes` vem de `AccessLog` (só existe dentro da janela de
  `retention_detailed_days`), limitado a 200 linhas mais recentes.
  `geo` dispara `lookup_ip` se ainda não estiver em cache (chamada síncrona
  dentro do request — aceitável porque só acontece uma vez por IP; nas
  próximas consultas vem do cache, sem latência extra).

## Frontend

### Navegação (`frontend/app/layout.tsx`)

Novo item no `NAV`, entre "Alertas" e "Configurações":
```ts
{ href: '/acessos', label: 'Acessos', icon: '🌐' },
```

### Página (`frontend/app/acessos/page.tsx`)

Segue o padrão visual de `historico/page.tsx` (mesmos estilos de botão de
filtro/tab):

- Seletor de período: 24h / 7d / 30d (equivalente ao `RANGES` de Histórico,
  mapeando pra `days`).
- Dropdown "Sistema" populado por `/api/access-logs/sistemas` (opção
  "Todos" default).
- Campo de texto "Filtrar por IP" (debounce de 300ms antes de refazer a
  chamada).
- Tabela com colunas: **IP** (clicável, abre modal) | **Acessos** | **Sistemas
  acessados** (chips, até 3 visíveis + "+N" se houver mais) | **Último
  acesso** (data relativa, ex. "há 2h").
- Estado vazio: "Nenhum acesso registrado no período." (cobre tanto o caso
  de filtro sem resultado quanto o caso do access log do Traefik ainda não
  estar configurado).

### Modal (`frontend/components/AccessIpModal.tsx`)

Aberto ao clicar num IP na tabela, busca `/api/access-logs/ip/{ip}?days=<mesmo período da página>`:

- Cabeçalho: IP + bandeira/país (texto) + cidade/região.
- Bloco de geo: país, região, cidade, ISP/organização — "IP privado/local"
  quando `geo.is_private`; "Localização indisponível" quando os campos
  vierem `null` (falha da API externa).
- Tabela "Sistemas acessados": sistema | nº de acessos | último acesso.
- Lista "Acessos recentes" (scroll interno, mesmo padrão do modal de logs
  de container já existente): sistema, path, método, status, hora.
- Fecha com X ou clique fora, mesmo padrão de `QrCodeModal.tsx`.

## `docker-compose.yml`

```yaml
services:
  monitor-backend:
    environment:
      # ...existentes...
      - TRAEFIK_ACCESS_LOG_PATH=/var/log/traefik/access.log
    volumes:
      # ...existentes...
      - traefik_access_logs:/var/log/traefik:ro

volumes:
  # ...existente vps_monitor_data...
  traefik_access_logs:
    external: true
```

`.env.example` ganha `TRAEFIK_ACCESS_LOG_PATH=/var/log/traefik/access.log`
comentado, com nota de que depende da stack do Traefik já estar configurada
(ver seção "Pré-requisito de infraestrutura").

## Testes

- `test_access_log_tailer.py`: parse de linha JSON válida grava `AccessLog`
  + `AccessLogDaily`; linha de asset estático/health-check é descartada;
  linha JSON inválida não derruba o processamento das demais; offset
  persiste entre chamadas (não reprocessa linha já lida); mudança de inode
  reseta offset; arquivo ausente não lança exceção.
- `test_geoip.py`: IP privado não chama API externa; IP público chama
  `ip-api.com` só na primeira vez (segunda chamada usa cache); erro/timeout
  da API externa não lança exceção, grava cache com campos `None`.
- `test_access_logs_api.py`: `/summary` agrega corretamente por IP e
  respeita filtros de `sistema`/`ip`/`days`; `/sistemas` retorna lista
  distinta; `/ip/{ip}` dispara geo lookup na primeira chamada e usa cache na
  segunda; todos os endpoints exigem token.

## Fora de escopo

- Configuração automática do Traefik (habilitar access log, criar volume) —
  é feita manualmente pelo operador na stack do Traefik, fora deste repo.
- Nome amigável configurável por domínio (ex. mapear
  `app2.dlsistemas.com.br` → "Sistema de Vendas") — o domínio bruto é usado
  como nome do sistema.
- Detecção/alerta de comportamento anômalo (ex. IP com volume suspeito de
  acessos, scanners, brute-force) — esta entrega é só visualização, sem
  motor de alerta sobre acessos.
- Bloqueio de IPs a partir do painel (isso seria feito no Traefik/firewall,
  não no monitor).
- Refresh manual do cache de geolocalização de um IP já consultado.
- Suporte a múltiplas instâncias de Traefik/múltiplos arquivos de access
  log — assume um único arquivo cobrindo todos os sistemas da VPS.
