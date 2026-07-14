# VPS Monitor — Design Document
**Data:** 2026-06-29  
**Status:** Aprovado  
**Domínio alvo:** monitor.dlsistemas.com.br  
**Diretório de instalação:** /opt/vps-monitor/

---

## 1. Visão Geral

Sistema web dedicado e independente para monitoramento de um servidor Linux com containers Docker. Coleta métricas do host via `/proc` e `/sys`, monitora containers via Docker socket, armazena histórico em SQLite, exibe dados em tempo real via WebSocket, e envia alertas por e-mail (SMTP) e WhatsApp (Evolution API).

---

## 2. Decomposição em Fases

O projeto é grande demais para um único plano de implementação. Dividido em 3 fases independentes, cada uma com seu próprio ciclo de implementação:

| Fase | Conteúdo | Dependências |
|---|---|---|
| **Fase 1** | Backend de monitoramento + Dashboard básico + Infraestrutura | Nenhuma |
| **Fase 2** | Motor de alertas + Página de alertas | Fase 1 |
| **Fase 3** | Notificações (SMTP + WhatsApp) + Página de configurações | Fase 2 |

---

## 3. Stack Técnica

### Backend
- **Python 3.11+** com **FastAPI** (async)
- **APScheduler** — coleta a cada 30 segundos
- **SQLite** via **SQLAlchemy** (WAL mode para escritas concorrentes)
- **httpx** — chamadas HTTP para Evolution API
- **cryptography (Fernet)** — criptografia de campos sensíveis no banco
- **smtplib** nativo — envio de e-mails
- Comunicação com Docker via socket UNIX `/var/run/docker.sock`
- Leitura de `/proc` e `/sys` diretamente (sem libs de terceiros para métricas)

### Frontend
- **Next.js 14** (App Router) + **TypeScript**
- **Recharts** — gráficos de linha com área sombreada
- Sem UI library externa — inline styles com variáveis CSS:

```css
:root {
  --bg: #0f1117;
  --surface: #161b27;
  --card: #1c2333;
  --border: #2a3347;
  --text: #e8eaf0;
  --muted: #6b7a99;
  --accent: #f5a623;
  --success: #43a047;
  --danger: #e53935;
  --warning: #fb8c00;
  --info: #1e88e5;
}
```

### Infraestrutura
- **Docker Compose** com 3 serviços: `monitor-backend`, `monitor-frontend`, `monitor-nginx`
- **Traefik** labels para exposição em `monitor.dlsistemas.com.br` com SSL Let's Encrypt
- Rede interna `vps_monitor_net` + rede externa `proxy` (Traefik)

---

## 4. Arquitetura

```
Internet
    │
 Traefik (rede proxy — já existente na VPS)
    │
 monitor-nginx  ← reverse proxy interno
    ├── /          → monitor-frontend:3000  (Next.js)
    ├── /api/*     → monitor-backend:8000   (FastAPI)
    └── /ws/*      → monitor-backend:8000   (WebSocket)
         │
    monitor-backend
         ├── lê /host/proc  (bind mount :ro)
         ├── lê /host/sys   (bind mount :ro)
         └── lê /var/run/docker.sock (:ro)
         │
    SQLite (volume Docker persistente: vps_monitor_data)
```

**Criptografia de campos sensíveis:** A chave Fernet é derivada do `JWT_SECRET` do `.env`. Campos criptografados: senha SMTP, API Key Evolution, senha do painel. Os valores são descriptografados apenas em runtime, nunca retornados em texto puro pela API (substituídos por `****...últimos6`).

---

## 5. Estrutura de Arquivos

```
vps-monitor/
├── backend/
│   ├── main.py
│   ├── collector/
│   │   ├── host.py
│   │   ├── docker_client.py
│   │   └── scheduler.py
│   ├── api/
│   │   ├── auth.py
│   │   ├── metrics.py
│   │   ├── containers.py
│   │   ├── alerts.py
│   │   ├── config.py
│   │   └── whatsapp.py
│   ├── models/
│   │   └── database.py
│   ├── notifications/
│   │   ├── email_service.py
│   │   ├── whatsapp_service.py
│   │   └── alert_engine.py
│   ├── ws/
│   │   └── stream.py
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx
│   │   ├── containers/page.tsx
│   │   ├── historico/page.tsx
│   │   ├── alertas/page.tsx
│   │   └── configuracoes/page.tsx
│   ├── components/
│   │   ├── MetricCard.tsx
│   │   ├── ProgressBar.tsx
│   │   ├── LineChart.tsx
│   │   ├── ContainerRow.tsx
│   │   ├── AlertBadge.tsx
│   │   ├── QrCodeModal.tsx
│   │   └── Toast.tsx
│   ├── lib/
│   │   ├── api.ts
│   │   └── ws.ts
│   ├── Dockerfile
│   └── next.config.ts
├── docker/
│   └── nginx/monitor.conf
├── docker-compose.yml
├── .env.example
├── deploy.sh
└── README.md
```

---

## 6. Schema do Banco de Dados (SQLite, WAL mode)

```sql
CREATE TABLE metrics_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at DATETIME NOT NULL,
    cpu_percent REAL,
    load_1m REAL, load_5m REAL, load_15m REAL,
    ram_total_mb REAL, ram_used_mb REAL, ram_percent REAL,
    disk_used_gb REAL, disk_total_gb REAL, disk_percent REAL,
    net_rx_bytes_s INTEGER, net_tx_bytes_s INTEGER,
    temperature_c REAL
);
CREATE INDEX idx_metrics_collected_at ON metrics_history(collected_at);

CREATE TABLE container_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at DATETIME NOT NULL,
    container_id TEXT NOT NULL,
    container_name TEXT NOT NULL,
    cpu_percent REAL,
    mem_used_mb REAL, mem_limit_mb REAL, mem_percent REAL,
    net_rx_bytes INTEGER, net_tx_bytes INTEGER,
    status TEXT,
    restart_count INTEGER
);
CREATE INDEX idx_container_collected_at ON container_metrics(collected_at, container_name);

CREATE TABLE alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    metrica TEXT NOT NULL,
    operador TEXT NOT NULL,
    threshold REAL NOT NULL,
    duracao_minutos INTEGER DEFAULT 5,
    severidade TEXT NOT NULL,
    canal_email INTEGER DEFAULT 1,
    canal_whatsapp INTEGER DEFAULT 1,
    cooldown_minutos INTEGER DEFAULT 30,
    ativo INTEGER DEFAULT 1,
    criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER REFERENCES alert_rules(id),
    triggered_at DATETIME NOT NULL,
    resolved_at DATETIME,
    severidade TEXT,
    metrica TEXT,
    valor_no_disparo REAL,
    threshold REAL,
    mensagem TEXT,
    notificado_email INTEGER DEFAULT 0,
    notificado_whatsapp INTEGER DEFAULT 0,
    erro_email TEXT,
    erro_whatsapp TEXT
);
CREATE INDEX idx_alert_log_triggered ON alert_log(triggered_at);

CREATE TABLE config (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

---

## 7. Endpoints da API

```
# Auth
POST /api/auth/login                   → { token }

# Métricas
GET  /api/metrics/current
GET  /api/metrics/history?metric=cpu&range=1h|6h|24h|7d

# Containers
GET  /api/containers
GET  /api/containers/{id}/logs

# Alertas — Regras
GET    /api/alerts/rules
POST   /api/alerts/rules
PUT    /api/alerts/rules/{id}
DELETE /api/alerts/rules/{id}
POST   /api/alerts/rules/{id}/toggle

# Alertas — Log
GET  /api/alerts/active
GET  /api/alerts/history?from=&to=&severity=&metric=

# Configurações
GET  /api/config
PUT  /api/config

# Notificações — Testes
POST /api/notifications/test/email
POST /api/notifications/test/whatsapp

# WhatsApp — Gerenciamento de Instância
GET    /api/whatsapp/status
POST   /api/whatsapp/connect
GET    /api/whatsapp/qrcode
DELETE /api/whatsapp/disconnect
DELETE /api/whatsapp/delete-instance

# WebSocket
WS /ws/metrics
```

---

## 8. WebSocket — Payload

Emitido a cada 30 segundos para todos os clientes conectados:

```json
{
  "ts": "2026-06-29T01:00:00Z",
  "cpu": { "percent": 12.3, "load": [0.5, 0.4, 0.3], "cores": 4, "model": "AMD EPYC" },
  "ram": { "total_mb": 12288, "used_mb": 4096, "available_mb": 8192, "percent": 33.3 },
  "disk": { "total_gb": 192.7, "used_gb": 52.6, "available_gb": 140.1, "percent": 27 },
  "net": { "rx_bytes_s": 1024, "tx_bytes_s": 512, "interface": "eth0" },
  "temperature_c": 45.0,
  "uptime": { "days": 5, "hours": 3, "minutes": 22, "seconds": 443742 },
  "containers": [
    {
      "id": "abc123", "name": "mecanicapro-backend", "status": "running",
      "cpu_percent": 0.5, "mem_percent": 12.1, "mem_used_mb": 148.3, "restart_count": 0
    }
  ],
  "active_alerts": [
    {
      "id": 5, "severidade": "aviso", "metrica": "ram_percent",
      "mensagem": "RAM em 87% por 6 minutos", "triggered_at": "2026-06-29T00:54:00Z"
    }
  ]
}
```

---

## 9. Fase 1 — Backend de Monitoramento + Dashboard

### 9.1 Coletor de Métricas do Host (`collector/host.py`)

Lê diretamente dos arquivos virtuais do kernel (sem libs de terceiros):

| Métrica | Fonte |
|---|---|
| CPU percent | `/host/proc/stat` (delta entre amostras) |
| Load average | `/host/proc/loadavg` |
| CPU cores e modelo | `/host/proc/cpuinfo` |
| RAM | `/host/proc/meminfo` |
| Disco por partição | `df -k` via subprocess (ou `/proc/mounts` + `os.statvfs`) |
| Rede (bytes/s) | `/host/proc/net/dev` (delta entre amostras) |
| Uptime | `/host/proc/uptime` |
| Temperatura | `/host/sys/class/thermal/thermal_zone*/temp` (ignora se ausente) |

**Cálculo de CPU%:** guarda snapshot de `/proc/stat` na coleta anterior; na próxima coleta calcula `(delta_work / delta_total) * 100`.

**Cálculo de bytes/s de rede:** mesma abordagem — delta de bytes dividido pelo intervalo de 30s.

### 9.2 Coletor Docker (`collector/docker_client.py`)

Comunicação via socket UNIX `/var/run/docker.sock` com `httpx.AsyncClient`:

```
GET  /containers/json?all=true          → lista todos os containers
GET  /containers/{id}/stats?stream=false → métricas de um container (1s de amostra)
GET  /containers/{id}/logs?tail=50&timestamps=true → últimas 50 linhas de log
```

- Stats chamados em paralelo com `asyncio.gather` para todos os containers
- CPU% do container calculado a partir do `cpu_stats` e `precpu_stats` do payload do Docker
- Timeout de 5s por chamada de stats para evitar bloqueio

### 9.3 Scheduler (`collector/scheduler.py`)

APScheduler rodando no processo FastAPI:
1. Chama `host.py` e `docker_client.py` em paralelo
2. Grava em `metrics_history` e `container_metrics`
3. Publica no WebSocket via `stream.py`
4. Chama `alert_engine.evaluate()` (Fase 2 — stub vazio na Fase 1)
5. Roda limpeza de dados antigos diariamente (midnight)

### 9.4 Auth JWT

- `POST /api/auth/login` valida user/senha contra config do banco (hash bcrypt)
- Retorna JWT com `exp` de 24h
- Middleware FastAPI verifica `Authorization: Bearer <token>` em todas as rotas `/api/*` exceto `/api/auth/login`
- Credenciais padrão do `.env`: `MONITOR_USER` e `MONITOR_PASSWORD`

### 9.5 Dashboard Frontend (`app/page.tsx`)

**Cards de resumo (topo, 5 cards):**
- Saúde Geral: ✅/⚠️/🔴 baseado em alertas ativos
- Containers: "X rodando / Y total"
- RAM: percentual + `ProgressBar`
- Disco: percentual + `ProgressBar`
- Uptime: "Xd Xh Xm"

**Gráficos em tempo real (Recharts `LineChart`):**
- CPU% última 1h com marcadores de alerta (pontos vermelhos)
- RAM% última 1h
- Rede MB/s (rx e tx) última 1h
- Seletor de range: 1h / 6h / 24h / 7d

**Tabela de containers:**
- Colunas: Nome, Imagem, Status (badge), CPU%, RAM%, Uptime, Restarts, Ações
- Botão "Ver Logs" → modal com últimas 50 linhas + busca por texto
- Filtro: todos / rodando / parados
- Countdown visual de 30s até próximo refresh

**Timeline de eventos recentes:**
- Últimos 20 alertas/eventos (da tabela `alert_log`)
- Ícone severidade + descrição + horário + status ativo/resolvido

---

## 10. Fase 2 — Motor de Alertas

### 10.1 Motor de Avaliação (`notifications/alert_engine.py`)

Roda a cada ciclo de coleta (30s). Fluxo:

```
Para cada regra ativa:
  1. Obtém valor atual da métrica correspondente
  2. Avalia: valor OP threshold (>, <, >=, <=)
  3. Se VERDADEIRO:
     a. Se sem alerta ativo → cria registro em alert_log, marca início
     b. Se alerta ativo E duração >= duracao_minutos E cooldown passou:
        → agenda notificação (email e/ou whatsapp conforme regra)
        → atualiza last_notified_at
  4. Se FALSO E alerta ativo:
     → se condição falsa por >= 2 minutos → resolve alerta
     → envia notificação de resolução
     → atualiza resolved_at

Alertas container_stopped: imediatos (duracao_minutos ignorado)
Agrupamento: múltiplos alertas em < 5 min → lote único de notificação
```

### 10.2 Regras Padrão (inseridas no primeiro run)

| Nome | Métrica | Operador | Threshold | Duração | Severidade | Cooldown |
|---|---|---|---|---|---|---|
| CPU Alta | cpu_percent | > | 80 | 5 min | aviso | 30 min |
| CPU Crítica | cpu_percent | > | 95 | 2 min | critico | 15 min |
| RAM Alta | ram_percent | > | 85 | 3 min | aviso | 30 min |
| RAM Crítica | ram_percent | > | 95 | 1 min | critico | 15 min |
| Disco Alto | disk_percent | > | 80 | 0 min | aviso | 120 min |
| Disco Crítico | disk_percent | > | 90 | 0 min | critico | 60 min |
| Temperatura Alta | temperature_c | > | 75 | 5 min | aviso | 30 min |
| Load Alto | load_1m | > | cores×1.5 | 5 min | aviso | 30 min |
| Container Parado | container_stopped | — | — | 0 min | critico | 0 min |

### 10.3 Página de Alertas (`app/alertas/page.tsx`)

3 abas:
- **Ativas:** alertas não resolvidos com badge de severidade e duração decorrida
- **Histórico:** todos os alertas com filtro por data, métrica e severidade
- **Regras:** listagem com toggle ativo/inativo + formulário CRUD (nome, métrica, operador, threshold, duração mínima, severidade, canais, cooldown)

---

## 11. Fase 3 — Notificações + Configurações

### 11.1 Serviço de E-mail (`notifications/email_service.py`)

- `smtplib` nativo — suporte a TLS (STARTTLS) e SSL
- Template HTML com: header colorido por severidade, tabela de métricas, seção de contexto, botão "Acessar Painel", footer
- Agrupamento: se múltiplos alertas em < 5 min → único e-mail consolidado
- Notificação de resolução com header verde
- Botão de teste envia e-mail real para os destinatários configurados

### 11.2 Serviço WhatsApp — Evolution API (`notifications/whatsapp_service.py`)

Endpoints utilizados da Evolution API:

| Ação | Endpoint |
|---|---|
| Verificar instâncias | `GET /instance/fetchInstances` |
| Criar instância | `POST /instance/create` |
| Obter QR code | `GET /instance/connect/{instance}` |
| Verificar estado | `GET /instance/connectionState/{instance}` |
| Desconectar | `DELETE /instance/logout/{instance}` |
| Excluir instância | `DELETE /instance/delete/{instance}` |
| Enviar mensagem | `POST /message/sendText/{instance}` |

**Fluxo de conexão:**
1. Frontend chama `POST /api/whatsapp/connect`
2. Backend verifica se instância existe via `fetchInstances`
3. Se não existe: cria com `POST /instance/create`
4. Chama `GET /instance/connect/{instance}` → retorna QR code base64
5. Frontend exibe QR no modal com countdown de 30s
6. Frontend faz polling em `GET /api/whatsapp/status` a cada 3s
7. Se status = `connected` → fecha modal automaticamente, exibe toast
8. Se QR expirar (30s): frontend solicita novo via `GET /api/whatsapp/qrcode`

**Formato de mensagem WhatsApp:**
```
🚨 *ALERTA VPS MONITOR*
Severidade: ⚠️ AVISO / 🔴 CRÍTICO

📊 *Métrica:* CPU em uso
📈 *Valor atual:* 92,3%
⏱ *Duração:* 7 minutos
🕐 *Horário:* 14:32:15 (29/06/2026)

🖥️ Servidor: VPS Principal
🌐 Acesse o painel: https://monitor.dlsistemas.com.br

_Alerta gerado automaticamente pelo VPS Monitor_
```

### 11.3 Página de Configurações (`app/configuracoes/page.tsx`)

4 seções:

**Geral:** nome do servidor, timezone, URL pública do painel

**SMTP:** host, porta, usuário, senha (toggle show/hide), criptografia (TLS/SSL), e-mail remetente, nome remetente, destinatários (textarea), toggle ativo, botão "Enviar e-mail de teste"

**WhatsApp — Evolution API:**
- Campos: URL da API, API Key (mascarada: `****...últimos6`), nome da instância
- Números destinatários (textarea, formato 5511999990001)
- Toggle ativo
- Status da instância (badge colorido)
- **Botões contextuais (Opção A):**
  - `Sem instância` → `[ Criar Instância ]`
  - `Desconectada` → `[ Conectar (QR) ] [ Excluir Instância ]`
  - `Conectada` → `[ Desconectar ] [ Excluir Instância ]`
- Botão "Enviar mensagem de teste" (visível quando conectado)
- Modal QR code (`QrCodeModal.tsx`)

**Segurança + Retenção:** troca de usuário/senha, toggle "Exigir autenticação", dias de retenção detalhada (padrão 7) e agregada (padrão 30), botão "Limpar dados históricos"

### 11.4 QrCodeModal.tsx — Comportamento

1. Ao abrir: chama `POST /api/whatsapp/connect` → recebe QR base64
2. Exibe imagem QR em mínimo 250×250px
3. Polling a cada 3s em `GET /api/whatsapp/status`
4. Se `connected`: fecha modal + toast "✅ WhatsApp conectado!"
5. Countdown visual de 30s (barra de progresso)
6. Ao chegar em 0: solicita novo QR via `GET /api/whatsapp/qrcode` automaticamente
7. Instruções passo a passo numeradas no modal

---

## 12. Segurança

- Auth JWT com expiração de 24h
- Fernet encryption para campos sensíveis no SQLite
- Docker socket montado como `:ro` (read-only)
- `/proc` e `/sys` montados como `:ro`
- Rate limiting: 60 req/min por IP
- CORS: apenas origem do domínio configurado em `PUBLIC_URL`
- Campos sensíveis nunca retornados em texto puro pela API

---

## 13. Infraestrutura Docker

### docker-compose.yml (resumo)

```yaml
services:
  monitor-backend:
    build: ./backend
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - vps_monitor_data:/app/data
    networks: [vps_monitor_net]
    restart: unless-stopped

  monitor-frontend:
    build: ./frontend
    networks: [vps_monitor_net]
    restart: unless-stopped

  monitor-nginx:
    image: nginx:alpine
    volumes:
      - ./docker/nginx/monitor.conf:/etc/nginx/conf.d/default.conf:ro
    networks: [vps_monitor_net, proxy]
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.vps-monitor.rule=Host(`monitor.dlsistemas.com.br`)"
      - "traefik.http.routers.vps-monitor.tls.certresolver=letsencrypt"
    restart: unless-stopped

networks:
  vps_monitor_net:
    internal: true
  proxy:
    external: true

volumes:
  vps_monitor_data:
```

---

## 14. Requisitos Não-Funcionais

- Coleta de métricas: máximo 1% de CPU em idle
- SQLite WAL mode para escritas concorrentes
- Gráficos históricos renderizados em < 2 segundos
- Frontend responsivo para 1024px ou maior
- WebSocket com reconnect automático (backoff exponencial: 1s → 2s → 4s → 8s → 30s max)
- Envio de notificação em < 10 segundos após alerta disparado
- Interface completamente em português brasileiro
- Dark theme apenas (sem modo claro)
- Todos os containers com `restart: unless-stopped`

---

## 15. Variáveis de Ambiente (.env.example)

```env
MONITOR_USER=admin
MONITOR_PASSWORD=troque_esta_senha
JWT_SECRET=gere_um_secret_aleatorio_de_32_chars

PUBLIC_URL=https://monitor.dlsistemas.com.br

EVOLUTION_URL=https://ev.seudominio.com.br
EVOLUTION_API_KEY=
EVOLUTION_INSTANCE=vps-monitor

RETENTION_DETAILED_DAYS=7
RETENTION_AGGREGATED_DAYS=30
```
