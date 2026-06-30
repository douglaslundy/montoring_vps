# VPS Monitor Fase 1 — Parte 1: Backend (Tasks 1–9)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]` syntax for tracking.

**Goal:** Backend completo — coleta de métricas do host (/proc, /sys), Docker socket, SQLite WAL, WebSocket, API REST, auth JWT.

**Architecture:** FastAPI (async) + APScheduler (AsyncIOScheduler) coleta a cada 30s, persiste em SQLite, faz broadcast via WebSocket. SQLAlchemy sync em thread pool do FastAPI.

**Tech Stack:** Python 3.11, FastAPI 0.115, SQLAlchemy 2.0, APScheduler 3.10, httpx 0.27, python-jose, passlib[bcrypt], slowapi

## Global Constraints
- Sem libs de terceiros para ler /proc (stdlib apenas)
- /proc montado em `/host/proc`, /sys em `/host/sys`
- Docker socket em `/var/run/docker.sock` (read-only)
- SQLite em `/app/data/monitor.db`, WAL mode
- JWT HS256, 24h, armazenado no localStorage (frontend)
- Rate limit 60 req/min por IP em `/api/*`
- Interface em português brasileiro
- `restart: unless-stopped` em todos os containers

---

## Task 1: Scaffold do Projeto + Infraestrutura

**Files:**
- Create: `docker-compose.yml`
- Create: `docker/nginx/monitor.conf`
- Create: `.env.example`
- Create: `deploy.sh`
- Create: `backend/` (estrutura de diretórios)
- Create: `frontend/` (estrutura de diretórios)

**Interfaces:**
- Produces: estrutura completa do projeto, rede Docker `vps_monitor_net`, serviços `monitor-backend`, `monitor-frontend`, `monitor-nginx`

- [ ] **Step 1: Criar estrutura de diretórios**

```bash
mkdir -p backend/{api,collector,models,notifications,ws,tests}
mkdir -p frontend/{app/{login,containers,historico},components,lib}
mkdir -p docker/nginx
touch backend/__init__.py backend/api/__init__.py backend/collector/__init__.py
touch backend/models/__init__.py backend/notifications/__init__.py backend/ws/__init__.py
touch backend/tests/__init__.py
```

- [ ] **Step 2: Criar docker-compose.yml**

```yaml
# docker-compose.yml
services:
  monitor-backend:
    build: ./backend
    container_name: monitor-backend
    environment:
      - DB_PATH=/app/data/monitor.db
      - PROC_BASE=/host/proc
      - SYS_BASE=/host/sys
      - JWT_SECRET=${JWT_SECRET}
      - MONITOR_USER=${MONITOR_USER}
      - MONITOR_PASSWORD=${MONITOR_PASSWORD}
      - PUBLIC_URL=${PUBLIC_URL}
      - RETENTION_DETAILED_DAYS=${RETENTION_DETAILED_DAYS:-7}
      - RETENTION_AGGREGATED_DAYS=${RETENTION_AGGREGATED_DAYS:-30}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - vps_monitor_data:/app/data
    networks:
      - vps_monitor_net
    restart: unless-stopped

  monitor-frontend:
    build: ./frontend
    container_name: monitor-frontend
    networks:
      - vps_monitor_net
    restart: unless-stopped
    depends_on:
      - monitor-backend

  monitor-nginx:
    image: nginx:alpine
    container_name: monitor-nginx
    volumes:
      - ./docker/nginx/monitor.conf:/etc/nginx/conf.d/default.conf:ro
    networks:
      - vps_monitor_net
      - proxy
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.vps-monitor.rule=Host(`monitor.dlsistemas.com.br`)"
      - "traefik.http.routers.vps-monitor.entrypoints=websecure"
      - "traefik.http.routers.vps-monitor.tls.certresolver=letsencrypt"
      - "traefik.http.services.vps-monitor.loadbalancer.server.port=80"
      - "traefik.docker.network=proxy"
    restart: unless-stopped
    depends_on:
      - monitor-frontend
      - monitor-backend

networks:
  vps_monitor_net:
    internal: true
  proxy:
    external: true

volumes:
  vps_monitor_data:
```

- [ ] **Step 3: Criar docker/nginx/monitor.conf**

```nginx
server {
    listen 80;

    location / {
        proxy_pass http://monitor-frontend:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /api/ {
        proxy_pass http://monitor-backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /ws/ {
        proxy_pass http://monitor-backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
}
```

- [ ] **Step 4: Criar .env.example**

```env
MONITOR_USER=admin
MONITOR_PASSWORD=troque_esta_senha
JWT_SECRET=gere_um_secret_aleatorio_de_32_caracteres_aqui

PUBLIC_URL=https://monitor.dlsistemas.com.br

EVOLUTION_URL=
EVOLUTION_API_KEY=
EVOLUTION_INSTANCE=vps-monitor

RETENTION_DETAILED_DAYS=7
RETENTION_AGGREGATED_DAYS=30
```

- [ ] **Step 5: Criar deploy.sh**

```bash
#!/bin/bash
set -e
echo "=== VPS Monitor Deploy ==="
cd /opt/vps-monitor
[ ! -f .env ] && cp .env.example .env && echo "ATENÇÃO: edite o arquivo .env antes de continuar" && exit 1
docker compose build --no-cache
docker compose up -d
echo "=== Deploy concluído ==="
echo "Acesse: https://monitor.dlsistemas.com.br"
```

```bash
chmod +x deploy.sh
```

- [ ] **Step 6: Commit**

```bash
git init
git add .
git commit -m "feat: scaffold do projeto e infraestrutura Docker"
```

---

## Task 2: Modelos de Banco de Dados

**Files:**
- Create: `backend/models/database.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_database.py`

**Interfaces:**
- Produces: `engine`, `Base`, `MetricsHistory`, `ContainerMetrics`, `AlertRule`, `AlertLog`, `Config`, `init_db()`, `get_session()`

- [ ] **Step 1: Escrever o teste**

```python
# backend/tests/conftest.py
import pytest
import os
import tempfile
from sqlalchemy import text

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")

@pytest.fixture
def test_db(db_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", db_path)
    # Re-import para pegar nova env var
    import importlib
    import models.database as db_module
    importlib.reload(db_module)
    db_module.init_db()
    return db_module
```

```python
# backend/tests/test_database.py
from sqlalchemy.orm import Session
from sqlalchemy import text

def test_tabelas_criadas(test_db):
    with test_db.engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result}
    assert "metrics_history" in tables
    assert "container_metrics" in tables
    assert "alert_rules" in tables
    assert "alert_log" in tables
    assert "config" in tables

def test_wal_mode_ativo(test_db):
    with test_db.engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode")).fetchone()
    assert result[0] == "wal"

def test_regras_padrao_inseridas(test_db):
    with Session(test_db.engine) as session:
        count = session.query(test_db.AlertRule).count()
    assert count == 9

def test_insert_metrics_history(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        record = test_db.MetricsHistory(
            collected_at=datetime.utcnow(),
            cpu_percent=45.2,
            ram_percent=60.1,
            disk_percent=30.0,
        )
        session.add(record)
        session.commit()
        fetched = session.query(test_db.MetricsHistory).first()
    assert fetched.cpu_percent == 45.2
```

- [ ] **Step 2: Rodar o teste para verificar falha**

```bash
cd backend && python -m pytest tests/test_database.py -v
```
Esperado: `ModuleNotFoundError: No module named 'models'`

- [ ] **Step 3: Implementar backend/models/database.py**

```python
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    DateTime, Text, text, ForeignKey
)
from sqlalchemy.orm import DeclarativeBase, Session

DB_PATH = os.environ.get("DB_PATH", "/app/data/monitor.db")
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)


class Base(DeclarativeBase):
    pass


class MetricsHistory(Base):
    __tablename__ = "metrics_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    cpu_percent = Column(Float)
    load_1m = Column(Float)
    load_5m = Column(Float)
    load_15m = Column(Float)
    ram_total_mb = Column(Float)
    ram_used_mb = Column(Float)
    ram_percent = Column(Float)
    disk_used_gb = Column(Float)
    disk_total_gb = Column(Float)
    disk_percent = Column(Float)
    net_rx_bytes_s = Column(Integer)
    net_tx_bytes_s = Column(Integer)
    temperature_c = Column(Float)


class ContainerMetrics(Base):
    __tablename__ = "container_metrics"
    id = Column(Integer, primary_key=True, autoincrement=True)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    container_id = Column(String, nullable=False)
    container_name = Column(String, nullable=False)
    cpu_percent = Column(Float)
    mem_used_mb = Column(Float)
    mem_limit_mb = Column(Float)
    mem_percent = Column(Float)
    net_rx_bytes = Column(Integer)
    net_tx_bytes = Column(Integer)
    status = Column(String)
    restart_count = Column(Integer)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False)
    metrica = Column(String, nullable=False)
    operador = Column(String, nullable=False)
    threshold = Column(Float, nullable=False)
    duracao_minutos = Column(Integer, default=5)
    severidade = Column(String, nullable=False)
    canal_email = Column(Integer, default=1)
    canal_whatsapp = Column(Integer, default=1)
    cooldown_minutos = Column(Integer, default=30)
    ativo = Column(Integer, default=1)
    criado_em = Column(DateTime, default=datetime.utcnow)


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
    notificado_email = Column(Integer, default=0)
    notificado_whatsapp = Column(Integer, default=0)
    erro_email = Column(Text)
    erro_whatsapp = Column(Text)


class Config(Base):
    __tablename__ = "config"
    key = Column(String, primary_key=True)
    value = Column(Text)


_DEFAULT_RULES = [
    {"nome": "CPU Alta", "metrica": "cpu_percent", "operador": ">", "threshold": 80, "duracao_minutos": 5, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "CPU Crítica", "metrica": "cpu_percent", "operador": ">", "threshold": 95, "duracao_minutos": 2, "severidade": "critico", "cooldown_minutos": 15},
    {"nome": "RAM Alta", "metrica": "ram_percent", "operador": ">", "threshold": 85, "duracao_minutos": 3, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "RAM Crítica", "metrica": "ram_percent", "operador": ">", "threshold": 95, "duracao_minutos": 1, "severidade": "critico", "cooldown_minutos": 15},
    {"nome": "Disco Alto", "metrica": "disk_percent", "operador": ">", "threshold": 80, "duracao_minutos": 0, "severidade": "aviso", "cooldown_minutos": 120},
    {"nome": "Disco Crítico", "metrica": "disk_percent", "operador": ">", "threshold": 90, "duracao_minutos": 0, "severidade": "critico", "cooldown_minutos": 60},
    {"nome": "Temperatura Alta", "metrica": "temperature_c", "operador": ">", "threshold": 75, "duracao_minutos": 5, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "Load Alto", "metrica": "load_1m", "operador": ">", "threshold": 6.0, "duracao_minutos": 5, "severidade": "aviso", "cooldown_minutos": 30},
    {"nome": "Container Parado", "metrica": "container_stopped", "operador": "==", "threshold": 1, "duracao_minutos": 0, "severidade": "critico", "cooldown_minutos": 0},
]

_DEFAULT_CONFIG = {
    "server_name": "VPS Principal",
    "timezone": "America/Sao_Paulo",
    "public_url": "",
    "smtp_enabled": "0",
    "whatsapp_enabled": "0",
    "require_auth": "1",
    "retention_detailed_days": "7",
    "retention_aggregated_days": "30",
}


def init_db():
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.commit()
    with Session(engine) as session:
        if session.query(AlertRule).count() == 0:
            for rule in _DEFAULT_RULES:
                session.add(AlertRule(**rule))
        for key, value in _DEFAULT_CONFIG.items():
            if not session.get(Config, key):
                session.add(Config(key=key, value=value))
        session.commit()


def get_session() -> Session:
    return Session(engine)
```

- [ ] **Step 4: Rodar testes**

```bash
cd backend && python -m pytest tests/test_database.py -v
```
Esperado: 4 testes passando

- [ ] **Step 5: Commit**

```bash
git add backend/models/ backend/tests/
git commit -m "feat: modelos SQLite com WAL mode e regras padrão"
```

---

## Task 3: Coletor de Métricas do Host

**Files:**
- Create: `backend/collector/host.py`
- Create: `backend/tests/test_host_collector.py`

**Interfaces:**
- Produces: `collect_host_metrics(proc_base, sys_base) -> dict` com chaves `cpu`, `ram`, `disk`, `net`, `uptime`, `temperature_c`

- [ ] **Step 1: Escrever testes com fixtures de /proc**

```python
# backend/tests/test_host_collector.py
import pytest
from pathlib import Path

@pytest.fixture
def proc_dir(tmp_path):
    p = tmp_path / "proc"
    p.mkdir()
    (p / "stat").write_text(
        "cpu  100 0 50 850 0 0 0 0 0 0\n"
        "cpu0 50 0 25 425 0 0 0 0 0 0\n"
    )
    (p / "loadavg").write_text("1.50 1.20 0.90 2/100 1234\n")
    (p / "cpuinfo").write_text(
        "processor\t: 0\nmodel name\t: AMD EPYC 7B13\n"
        "processor\t: 1\nmodel name\t: AMD EPYC 7B13\n"
    )
    (p / "meminfo").write_text(
        "MemTotal:       8192000 kB\n"
        "MemFree:        2048000 kB\n"
        "MemAvailable:   4096000 kB\n"
        "Buffers:         512000 kB\n"
        "Cached:         1024000 kB\n"
    )
    (p / "uptime").write_text("443742.12 1234567.89\n")
    net = p / "net"
    net.mkdir()
    (net / "dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo:    1000       10    0    0    0     0          0         0     1000      10    0    0    0     0       0          0\n"
        "  eth0: 1048576     1000    0    0    0     0          0         0   524288     500    0    0    0     0       0          0\n"
    )
    return str(p)

@pytest.fixture
def sys_dir(tmp_path):
    s = tmp_path / "sys" / "class" / "thermal" / "thermal_zone0"
    s.mkdir(parents=True)
    (s / "temp").write_text("45000\n")
    return str(tmp_path / "sys")

def test_cpu_load(proc_dir, sys_dir):
    import collector.host as h
    h._prev_cpu = None
    h._prev_net = None
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    assert result["cpu"]["load"] == [1.5, 1.2, 0.9]
    assert result["cpu"]["cores"] == 2
    assert result["cpu"]["model"] == "AMD EPYC 7B13"

def test_ram(proc_dir, sys_dir):
    import collector.host as h
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    assert result["ram"]["total_mb"] == pytest.approx(8000.0, abs=1)
    assert result["ram"]["available_mb"] == pytest.approx(4000.0, abs=1)
    assert 0 < result["ram"]["percent"] < 100

def test_uptime(proc_dir, sys_dir):
    import collector.host as h
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    assert result["uptime"]["days"] == 5
    assert result["uptime"]["hours"] == 3

def test_temperature(proc_dir, sys_dir):
    import collector.host as h
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    assert result["temperature_c"] == 45.0

def test_temperature_ausente(proc_dir, tmp_path):
    import collector.host as h
    sys_empty = str(tmp_path / "sys_empty")
    Path(sys_empty).mkdir()
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_empty)
    assert result["temperature_c"] is None

def test_cpu_percent_segunda_leitura(proc_dir, sys_dir):
    import collector.host as h
    h._prev_cpu = None
    h._prev_net = None
    h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    # Simular segunda coleta com mais uso
    stat = Path(proc_dir) / "stat"
    stat.write_text("cpu  200 0 100 1500 0 0 0 0 0 0\ncpu0 100 0 50 750 0 0 0 0 0 0\n")
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    assert result["cpu"]["percent"] is not None
    assert 0 <= result["cpu"]["percent"] <= 100
```

- [ ] **Step 2: Rodar para verificar falha**

```bash
cd backend && python -m pytest tests/test_host_collector.py -v
```
Esperado: `ModuleNotFoundError: No module named 'collector.host'`

- [ ] **Step 3: Implementar backend/collector/host.py**

```python
import os
import time
from pathlib import Path

_prev_cpu: dict | None = None
_prev_net: dict | None = None
_prev_time: float | None = None

PROC_BASE = os.environ.get("PROC_BASE", "/host/proc")
SYS_BASE = os.environ.get("SYS_BASE", "/host/sys")


def _read_cpu_raw(proc_base: str) -> tuple[dict, list[float]]:
    with open(f"{proc_base}/stat") as f:
        line = f.readline()
    values = [int(x) for x in line.split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)

    with open(f"{proc_base}/loadavg") as f:
        parts = f.read().split()
    load = [float(parts[0]), float(parts[1]), float(parts[2])]

    return {"idle": idle, "total": total}, load


def _read_cpu_info(proc_base: str) -> tuple[int, str]:
    cores, model = 0, "Unknown"
    with open(f"{proc_base}/cpuinfo") as f:
        for line in f:
            if line.startswith("processor"):
                cores += 1
            elif line.startswith("model name") and model == "Unknown":
                model = line.split(":", 1)[1].strip()
    return cores, model


def _read_ram(proc_base: str) -> dict:
    mem: dict[str, int] = {}
    keys = {"MemTotal", "MemFree", "MemAvailable"}
    with open(f"{proc_base}/meminfo") as f:
        for line in f:
            parts = line.split()
            key = parts[0].rstrip(":")
            if key in keys:
                mem[key] = int(parts[1])
    total_mb = mem.get("MemTotal", 0) / 1024
    avail_mb = mem.get("MemAvailable", 0) / 1024
    used_mb = total_mb - avail_mb
    pct = round(used_mb / total_mb * 100, 1) if total_mb else 0
    return {
        "total_mb": round(total_mb, 1),
        "used_mb": round(used_mb, 1),
        "available_mb": round(avail_mb, 1),
        "percent": pct,
    }


def _read_disk() -> dict:
    candidates = ["/", "/var", "/opt", "/home", "/data"]
    best = {"total_gb": 0.0, "used_gb": 0.0, "available_gb": 0.0, "percent": 0.0, "mountpoint": "/"}
    for mount in candidates:
        try:
            st = os.statvfs(mount)
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
            used = total - free
            if total > best["total_gb"] * 1024 ** 3:
                pct = round(used / total * 100, 1) if total else 0
                best = {
                    "total_gb": round(total / 1024 ** 3, 1),
                    "used_gb": round(used / 1024 ** 3, 1),
                    "available_gb": round(free / 1024 ** 3, 1),
                    "percent": pct,
                    "mountpoint": mount,
                }
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return best


def _read_net(proc_base: str) -> dict:
    global _prev_net, _prev_time
    now = time.monotonic()
    iface_data: dict[str, dict] = {}
    with open(f"{proc_base}/net/dev") as f:
        for line in f:
            line = line.strip()
            if ":" not in line:
                continue
            iface, rest = line.split(":", 1)
            iface = iface.strip()
            if iface == "lo":
                continue
            vals = rest.split()
            iface_data[iface] = {"rx": int(vals[0]), "tx": int(vals[8])}

    result = {"rx_bytes_s": 0, "tx_bytes_s": 0, "interface": "unknown"}
    if iface_data:
        iface = next(iter(iface_data))
        curr = iface_data[iface]
        if _prev_net and _prev_time:
            elapsed = now - _prev_time
            if elapsed > 0:
                rx_s = int((curr["rx"] - _prev_net.get("rx", curr["rx"])) / elapsed)
                tx_s = int((curr["tx"] - _prev_net.get("tx", curr["tx"])) / elapsed)
                result = {"rx_bytes_s": max(0, rx_s), "tx_bytes_s": max(0, tx_s), "interface": iface}
        _prev_net = curr
        _prev_time = now
    return result


def _read_uptime(proc_base: str) -> dict:
    with open(f"{proc_base}/uptime") as f:
        secs = int(float(f.read().split()[0]))
    return {
        "days": secs // 86400,
        "hours": (secs % 86400) // 3600,
        "minutes": (secs % 3600) // 60,
        "seconds": secs,
    }


def _read_temperature(sys_base: str) -> float | None:
    thermal = Path(f"{sys_base}/class/thermal")
    if not thermal.exists():
        return None
    temps = []
    for zone in thermal.glob("thermal_zone*/temp"):
        try:
            temps.append(int(zone.read_text().strip()) / 1000.0)
        except (ValueError, OSError):
            continue
    return round(max(temps), 1) if temps else None


def collect_host_metrics(
    proc_base: str = PROC_BASE,
    sys_base: str = SYS_BASE,
) -> dict:
    global _prev_cpu

    cpu_raw, load = _read_cpu_raw(proc_base)
    cpu_percent: float | None = None
    if _prev_cpu:
        d_idle = cpu_raw["idle"] - _prev_cpu["idle"]
        d_total = cpu_raw["total"] - _prev_cpu["total"]
        if d_total > 0:
            cpu_percent = round(100.0 * (1 - d_idle / d_total), 1)
    _prev_cpu = cpu_raw

    cores, model = _read_cpu_info(proc_base)

    return {
        "cpu": {"percent": cpu_percent, "load": load, "cores": cores, "model": model},
        "ram": _read_ram(proc_base),
        "disk": _read_disk(),
        "net": _read_net(proc_base),
        "uptime": _read_uptime(proc_base),
        "temperature_c": _read_temperature(sys_base),
    }
```

- [ ] **Step 4: Rodar testes**

```bash
cd backend && python -m pytest tests/test_host_collector.py -v
```
Esperado: 6 testes passando

- [ ] **Step 5: Commit**

```bash
git add backend/collector/host.py backend/tests/test_host_collector.py
git commit -m "feat: coletor de métricas do host via /proc e /sys"
```

---

## Task 4: Docker Socket Client

**Files:**
- Create: `backend/collector/docker_client.py`
- Create: `backend/tests/test_docker_client.py`

**Interfaces:**
- Produces: `DockerClient` com `collect_all() -> list[dict]`, `get_logs(id, tail) -> list[str]`

- [ ] **Step 1: Escrever testes**

```python
# backend/tests/test_docker_client.py
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

MOCK_CONTAINERS = [
    {
        "Id": "abc123def456",
        "Names": ["/meu-container"],
        "Image": "nginx:latest",
        "State": "running",
        "Status": "Up 2 hours",
        "HostConfig": {"RestartCount": 0},
    }
]

MOCK_STATS = {
    "cpu_stats": {
        "cpu_usage": {"total_usage": 200000000},
        "system_cpu_usage": 2000000000,
        "online_cpus": 2,
    },
    "precpu_stats": {
        "cpu_usage": {"total_usage": 100000000},
        "system_cpu_usage": 1000000000,
    },
    "memory_stats": {
        "usage": 104857600,
        "limit": 1073741824,
        "stats": {"cache": 0},
    },
    "networks": {"eth0": {"rx_bytes": 1024, "tx_bytes": 512}},
}


def test_calculate_cpu_percent():
    from collector.docker_client import calculate_cpu_percent
    result = calculate_cpu_percent(MOCK_STATS)
    # (100M / 1000M) * 2 cpus * 100 = 20%
    assert result == pytest.approx(20.0, abs=0.1)


def test_calculate_cpu_percent_zeros():
    from collector.docker_client import calculate_cpu_percent
    bad_stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 0}, "system_cpu_usage": 0, "online_cpus": 1},
        "precpu_stats": {"cpu_usage": {"total_usage": 0}, "system_cpu_usage": 0},
    }
    assert calculate_cpu_percent(bad_stats) == 0.0


@pytest.mark.asyncio
async def test_collect_all():
    from collector.docker_client import DockerClient

    async def mock_list():
        return MOCK_CONTAINERS

    async def mock_stats(cid):
        return MOCK_STATS

    client = DockerClient()
    with patch.object(client, "list_containers", mock_list), \
         patch.object(client, "get_stats", mock_stats):
        result = await client.collect_all()

    assert len(result) == 1
    c = result[0]
    assert c["name"] == "meu-container"
    assert c["status"] == "running"
    assert c["cpu_percent"] == pytest.approx(20.0, abs=0.1)
    assert c["mem_used_mb"] == pytest.approx(100.0, abs=1)
    assert c["mem_percent"] == pytest.approx(9.8, abs=0.2)
```

- [ ] **Step 2: Adicionar pytest-asyncio ao conftest**

```python
# Adicionar no topo de backend/tests/conftest.py
import pytest

pytest_plugins = ['pytest_asyncio']
```

- [ ] **Step 3: Rodar para verificar falha**

```bash
cd backend && python -m pytest tests/test_docker_client.py -v
```

- [ ] **Step 4: Implementar backend/collector/docker_client.py**

```python
import asyncio
import httpx
from typing import Optional


def calculate_cpu_percent(stats: dict) -> float:
    try:
        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        sys_delta = (
            stats["cpu_stats"]["system_cpu_usage"]
            - stats["precpu_stats"]["system_cpu_usage"]
        )
        ncpus = stats["cpu_stats"].get("online_cpus") or len(
            stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])
        )
        if sys_delta > 0 and cpu_delta >= 0:
            return round((cpu_delta / sys_delta) * ncpus * 100.0, 2)
    except (KeyError, ZeroDivisionError, TypeError):
        pass
    return 0.0


class DockerClient:
    def __init__(self, socket_path: str = "/var/run/docker.sock"):
        self._socket = socket_path

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=self._socket),
            base_url="http://localhost",
            timeout=10.0,
        )

    async def list_containers(self) -> list[dict]:
        async with self._client() as c:
            r = await c.get("/containers/json", params={"all": True})
            r.raise_for_status()
            return r.json()

    async def get_stats(self, container_id: str) -> Optional[dict]:
        try:
            async with self._client() as c:
                r = await c.get(
                    f"/containers/{container_id}/stats",
                    params={"stream": "false"},
                    timeout=5.0,
                )
                r.raise_for_status()
                return r.json()
        except Exception:
            return None

    async def get_logs(self, container_id: str, tail: int = 50) -> list[str]:
        try:
            async with self._client() as c:
                r = await c.get(
                    f"/containers/{container_id}/logs",
                    params={"tail": tail, "stdout": True, "stderr": True, "timestamps": True},
                    timeout=5.0,
                )
                r.raise_for_status()
                raw = r.content
                lines: list[str] = []
                i = 0
                while i + 8 <= len(raw):
                    size = int.from_bytes(raw[i + 4:i + 8], "big")
                    msg = raw[i + 8:i + 8 + size].decode("utf-8", errors="replace").rstrip("\n")
                    if msg:
                        lines.append(msg)
                    i += 8 + size
                return lines[-tail:]
        except Exception:
            return []

    async def collect_all(self) -> list[dict]:
        containers = await self.list_containers()
        if not containers:
            return []

        stats_list = await asyncio.gather(
            *[self.get_stats(c["Id"]) for c in containers]
        )

        result = []
        for container, stats in zip(containers, stats_list):
            name = (container["Names"][0].lstrip("/") if container["Names"]
                    else container["Id"][:12])

            cpu_pct = mem_used = mem_limit = mem_pct = 0.0
            net_rx = net_tx = 0

            if stats:
                cpu_pct = calculate_cpu_percent(stats)
                ms = stats.get("memory_stats", {})
                cache = ms.get("stats", {}).get("cache", 0)
                raw_used = ms.get("usage", 0) - cache
                lim = ms.get("limit", 1) or 1
                mem_used = round(raw_used / 1024 ** 2, 1)
                mem_limit = round(lim / 1024 ** 2, 1)
                mem_pct = round(raw_used / lim * 100, 1)
                for iface in stats.get("networks", {}).values():
                    net_rx += iface.get("rx_bytes", 0)
                    net_tx += iface.get("tx_bytes", 0)

            result.append({
                "id": container["Id"][:12],
                "id_full": container["Id"],
                "name": name,
                "image": container.get("Image", ""),
                "status": container.get("State", "unknown"),
                "status_text": container.get("Status", ""),
                "cpu_percent": cpu_pct,
                "mem_used_mb": mem_used,
                "mem_limit_mb": mem_limit,
                "mem_percent": mem_pct,
                "net_rx_bytes": net_rx,
                "net_tx_bytes": net_tx,
                "restart_count": container.get("HostConfig", {}).get("RestartCount", 0),
            })
        return result
```

- [ ] **Step 5: Rodar testes**

```bash
cd backend && python -m pytest tests/test_docker_client.py -v
```
Esperado: 3 testes passando

- [ ] **Step 6: Commit**

```bash
git add backend/collector/docker_client.py backend/tests/test_docker_client.py
git commit -m "feat: cliente Docker via socket Unix com cálculo de CPU%"
```

---

## Task 5: FastAPI App + Auth JWT

**Files:**
- Create: `backend/api/auth.py`
- Create: `backend/main.py`
- Create: `backend/tests/test_auth.py`

**Interfaces:**
- Produces: `app` (FastAPI), `verify_token_header` (dependency), `POST /api/auth/login`

- [ ] **Step 1: Escrever testes**

```python
# backend/tests/test_auth.py
import pytest
import os
from fastapi.testclient import TestClient

@pytest.fixture
def client(test_db, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "senha123")
    monkeypatch.setenv("JWT_SECRET", "secret-de-teste-32-caracteres-ok")
    import importlib
    import api.auth as auth_mod
    importlib.reload(auth_mod)
    import main
    importlib.reload(main)
    return TestClient(main.app, raise_server_exceptions=True)

def test_login_correto(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "senha123"})
    assert r.status_code == 200
    assert "token" in r.json()

def test_login_senha_errada(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "errada"})
    assert r.status_code == 401

def test_rota_protegida_sem_token(client):
    r = client.get("/api/metrics/current")
    assert r.status_code == 401

def test_rota_protegida_com_token(client):
    token = client.post(
        "/api/auth/login", json={"username": "admin", "password": "senha123"}
    ).json()["token"]
    r = client.get("/api/metrics/current", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200

def test_health_sem_auth(client):
    r = client.get("/api/health")
    assert r.status_code == 200
```

- [ ] **Step 2: Rodar para verificar falha**

```bash
cd backend && python -m pytest tests/test_auth.py -v
```

- [ ] **Step 3: Implementar backend/api/auth.py**

```python
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from models.database import Config, get_session

auth_router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.environ.get("JWT_SECRET", "insecure-default-change-this-now-please")
ALGORITHM = "HS256"


class LoginRequest(BaseModel):
    username: str
    password: str


def _get_credentials() -> tuple[str, str]:
    with get_session() as session:
        u = session.get(Config, "auth_username")
        p = session.get(Config, "auth_password_hash")
    username = u.value if u else os.environ.get("MONITOR_USER", "admin")
    if p:
        return username, p.value
    raw = os.environ.get("MONITOR_PASSWORD", "admin")
    return username, pwd_context.hash(raw)


def create_token(username: str) -> str:
    exp = datetime.utcnow() + timedelta(hours=24)
    return jwt.encode({"sub": username, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


async def verify_token_header(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token ausente")
    if verify_token(authorization.removeprefix("Bearer ")) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")


@auth_router.post("/auth/login")
def login(body: LoginRequest):
    username, pw_hash = _get_credentials()
    if body.username != username or not pwd_context.verify(body.password, pw_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")
    return {"token": create_token(body.username)}
```

- [ ] **Step 4: Implementar backend/main.py**

```python
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from api.auth import auth_router, verify_token_header
from api.metrics import metrics_router
from api.containers import containers_router
from ws.stream import ws_router
from models.database import init_db

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

app = FastAPI(title="VPS Monitor", docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_protected = {"dependencies": [Depends(verify_token_header)]}

app.include_router(auth_router, prefix="/api")
app.include_router(metrics_router, prefix="/api", **_protected)
app.include_router(containers_router, prefix="/api", **_protected)
app.include_router(ws_router)


@app.on_event("startup")
async def startup():
    init_db()
    from collector.scheduler import start_scheduler
    start_scheduler()


@app.get("/api/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 5: Criar stubs temporários para imports**

```python
# backend/api/metrics.py (stub)
from fastapi import APIRouter
metrics_router = APIRouter()

@metrics_router.get("/metrics/current")
def current(): return {}
```

```python
# backend/api/containers.py (stub)
from fastapi import APIRouter
containers_router = APIRouter()
```

```python
# backend/ws/stream.py (stub)
from fastapi import APIRouter
ws_router = APIRouter()
```

```python
# backend/collector/scheduler.py (stub)
def start_scheduler(): pass
```

- [ ] **Step 6: Rodar testes**

```bash
cd backend && python -m pytest tests/test_auth.py -v
```
Esperado: 5 testes passando

- [ ] **Step 7: Commit**

```bash
git add backend/api/auth.py backend/main.py backend/api/metrics.py \
        backend/api/containers.py backend/ws/stream.py backend/collector/scheduler.py
git commit -m "feat: FastAPI app com auth JWT e middleware de rate limiting"
```

---

## Task 6: WebSocket Connection Manager

**Files:**
- Modify: `backend/ws/stream.py` (substituir stub)
- Create: `backend/tests/test_websocket.py`

**Interfaces:**
- Produces: `manager` (ConnectionManager), `ws_router` com `WS /ws/metrics`
- Consumes: nada (broadcast chamado pelo scheduler)

- [ ] **Step 1: Escrever testes**

```python
# backend/tests/test_websocket.py
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client(test_db):
    import main
    import importlib
    importlib.reload(main)
    return TestClient(main.app)

def test_websocket_conecta_e_recebe(client):
    with client.websocket_connect("/ws/metrics") as ws:
        # Só verifica que conecta sem erro
        # Dados chegam quando o scheduler faz broadcast
        pass

def test_broadcast_envia_para_clientes(test_db):
    import asyncio
    from ws.stream import manager
    from fastapi import WebSocket
    from unittest.mock import AsyncMock, MagicMock

    mock_ws = MagicMock(spec=WebSocket)
    mock_ws.send_json = AsyncMock()
    manager.active.append(mock_ws)

    asyncio.get_event_loop().run_until_complete(
        manager.broadcast({"ts": "2026-06-30", "cpu": {"percent": 10}})
    )

    mock_ws.send_json.assert_called_once()
    manager.active.remove(mock_ws)
```

- [ ] **Step 2: Implementar backend/ws/stream.py**

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

ws_router = APIRouter()


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


@ws_router.websocket("/ws/metrics")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
```

- [ ] **Step 3: Rodar testes**

```bash
cd backend && python -m pytest tests/test_websocket.py -v
```
Esperado: 2 testes passando

- [ ] **Step 4: Commit**

```bash
git add backend/ws/stream.py backend/tests/test_websocket.py
git commit -m "feat: WebSocket connection manager com broadcast"
```

---

## Task 7: APScheduler + Loop de Coleta

**Files:**
- Modify: `backend/collector/scheduler.py` (substituir stub)
- Create: `backend/notifications/alert_engine.py`
- Create: `backend/tests/test_scheduler.py`

**Interfaces:**
- Produces: `start_scheduler()`, `get_last_metrics() -> dict`, `docker_client` (instância global)
- Consumes: `collect_host_metrics()`, `DockerClient.collect_all()`, `manager.broadcast()`, `Session(engine)`

- [ ] **Step 1: Criar stub do alert engine**

```python
# backend/notifications/alert_engine.py
async def evaluate(metrics: dict, containers: list) -> list:
    """Fase 2: avaliação de regras de alerta. Retorna alertas ativos."""
    return []
```

- [ ] **Step 2: Escrever teste do scheduler**

```python
# backend/tests/test_scheduler.py
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime

@pytest.mark.asyncio
async def test_collect_and_store_salva_no_banco(test_db):
    mock_host = {
        "cpu": {"percent": 25.0, "load": [1.0, 0.8, 0.6], "cores": 4, "model": "Test CPU"},
        "ram": {"total_mb": 8192, "used_mb": 2048, "available_mb": 6144, "percent": 25.0},
        "disk": {"total_gb": 100.0, "used_gb": 30.0, "available_gb": 70.0, "percent": 30.0, "mountpoint": "/"},
        "net": {"rx_bytes_s": 1024, "tx_bytes_s": 512, "interface": "eth0"},
        "uptime": {"days": 1, "hours": 2, "minutes": 30, "seconds": 95400},
        "temperature_c": 42.5,
    }
    mock_containers = [
        {"id": "abc123", "id_full": "abc123def456", "name": "test", "image": "nginx",
         "status": "running", "status_text": "Up", "cpu_percent": 2.0,
         "mem_used_mb": 100.0, "mem_limit_mb": 512.0, "mem_percent": 19.5,
         "net_rx_bytes": 0, "net_tx_bytes": 0, "restart_count": 0}
    ]

    import collector.scheduler as sched
    from sqlalchemy.orm import Session
    from models.database import MetricsHistory, ContainerMetrics

    with patch("collector.scheduler.collect_host_metrics", return_value=mock_host), \
         patch.object(sched.docker_client, "collect_all", AsyncMock(return_value=mock_containers)), \
         patch("collector.scheduler.manager") as mock_mgr:
        mock_mgr.broadcast = AsyncMock()
        await sched.collect_and_store()

    with Session(test_db.engine) as session:
        row = session.query(MetricsHistory).first()
        assert row is not None
        assert row.cpu_percent == 25.0
        assert row.temperature_c == 42.5
        c_row = session.query(ContainerMetrics).first()
        assert c_row is not None
        assert c_row.container_name == "test"
```

- [ ] **Step 3: Implementar backend/collector/scheduler.py**

```python
import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from collector.docker_client import DockerClient
from collector.host import collect_host_metrics
from models.database import ContainerMetrics, MetricsHistory, engine, get_session
from notifications.alert_engine import evaluate
from ws.stream import manager

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="UTC")
docker_client = DockerClient()
_last_metrics: dict = {}


async def collect_and_store():
    global _last_metrics
    try:
        loop = asyncio.get_event_loop()
        host_task = loop.run_in_executor(None, collect_host_metrics)
        docker_task = docker_client.collect_all()
        host, containers = await asyncio.gather(host_task, docker_task)

        now = datetime.utcnow()

        with Session(engine) as session:
            session.add(MetricsHistory(
                collected_at=now,
                cpu_percent=host["cpu"]["percent"],
                load_1m=host["cpu"]["load"][0],
                load_5m=host["cpu"]["load"][1],
                load_15m=host["cpu"]["load"][2],
                ram_total_mb=host["ram"]["total_mb"],
                ram_used_mb=host["ram"]["used_mb"],
                ram_percent=host["ram"]["percent"],
                disk_used_gb=host["disk"]["used_gb"],
                disk_total_gb=host["disk"]["total_gb"],
                disk_percent=host["disk"]["percent"],
                net_rx_bytes_s=host["net"]["rx_bytes_s"],
                net_tx_bytes_s=host["net"]["tx_bytes_s"],
                temperature_c=host["temperature_c"],
            ))
            for c in containers:
                session.add(ContainerMetrics(
                    collected_at=now,
                    container_id=c["id"],
                    container_name=c["name"],
                    cpu_percent=c["cpu_percent"],
                    mem_used_mb=c["mem_used_mb"],
                    mem_limit_mb=c["mem_limit_mb"],
                    mem_percent=c["mem_percent"],
                    net_rx_bytes=c["net_rx_bytes"],
                    net_tx_bytes=c["net_tx_bytes"],
                    status=c["status"],
                    restart_count=c["restart_count"],
                ))
            session.commit()

        active_alerts = await evaluate(host, containers)

        payload = {
            "ts": now.isoformat() + "Z",
            "cpu": host["cpu"],
            "ram": host["ram"],
            "disk": host["disk"],
            "net": host["net"],
            "temperature_c": host["temperature_c"],
            "uptime": host["uptime"],
            "containers": containers,
            "active_alerts": active_alerts,
        }
        _last_metrics = payload
        await manager.broadcast(payload)
    except Exception:
        logger.exception("Erro na coleta de métricas")


async def _cleanup():
    import os
    from models.database import Config
    with get_session() as session:
        cfg = session.get(Config, "retention_detailed_days")
        days = int(cfg.value) if cfg else int(os.environ.get("RETENTION_DETAILED_DAYS", "7"))
    cutoff = datetime.utcnow() - timedelta(days=days)
    with Session(engine) as session:
        session.query(MetricsHistory).filter(MetricsHistory.collected_at < cutoff).delete()
        session.query(ContainerMetrics).filter(ContainerMetrics.collected_at < cutoff).delete()
        session.commit()


def get_last_metrics() -> dict:
    return _last_metrics


def start_scheduler():
    scheduler.add_job(collect_and_store, "interval", seconds=30, id="collect", replace_existing=True)
    scheduler.add_job(_cleanup, "cron", hour=0, minute=0, id="cleanup", replace_existing=True)
    if not scheduler.running:
        scheduler.start()
    asyncio.ensure_future(collect_and_store())
```

- [ ] **Step 4: Rodar testes**

```bash
cd backend && python -m pytest tests/test_scheduler.py -v
```
Esperado: 1 teste passando

- [ ] **Step 5: Commit**

```bash
git add backend/collector/scheduler.py backend/notifications/alert_engine.py \
        backend/tests/test_scheduler.py
git commit -m "feat: scheduler APScheduler com coleta a cada 30s e limpeza diária"
```

---

## Task 8: Metrics REST API

**Files:**
- Modify: `backend/api/metrics.py` (substituir stub)
- Create: `backend/tests/test_metrics_api.py`

**Interfaces:**
- Produces: `GET /api/metrics/current`, `GET /api/metrics/history?metric=&range=`
- Consumes: `get_last_metrics()`, `Session(engine)`, `MetricsHistory`

- [ ] **Step 1: Escrever testes**

```python
# backend/tests/test_metrics_api.py
import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

@pytest.fixture
def auth_client(test_db, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    import importlib, main, api.auth
    importlib.reload(api.auth)
    importlib.reload(main)
    client = TestClient(main.app)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, test_db

def test_current_metrics_vazio(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/current")
    assert r.status_code == 200

def test_history_retorna_dados(auth_client):
    client, db = auth_client
    now = datetime.utcnow()
    with Session(db.engine) as session:
        for i in range(5):
            session.add(db.MetricsHistory(
                collected_at=now - timedelta(minutes=i*5),
                cpu_percent=float(10 + i),
                ram_percent=float(50 + i),
                disk_percent=30.0,
            ))
        session.commit()
    r = client.get("/api/metrics/history?metric=cpu&range=1h")
    assert r.status_code == 200
    data = r.json()
    assert data["metric"] == "cpu"
    assert len(data["data"]) == 5
    assert "value" in data["data"][0]
    assert "ts" in data["data"][0]

def test_history_range_invalido_usa_1h(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/history?metric=cpu&range=invalido")
    assert r.status_code == 200

def test_history_metrica_invalida(auth_client):
    client, _ = auth_client
    r = client.get("/api/metrics/history?metric=inexistente&range=1h")
    assert r.status_code == 200
```

- [ ] **Step 2: Implementar backend/api/metrics.py**

```python
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from sqlalchemy.orm import Session
from models.database import MetricsHistory, engine
from collector.scheduler import get_last_metrics

metrics_router = APIRouter()

RANGE_HOURS = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
METRIC_MAP = {
    "cpu": "cpu_percent",
    "ram": "ram_percent",
    "disk": "disk_percent",
    "load": "load_1m",
    "net_rx": "net_rx_bytes_s",
    "net_tx": "net_tx_bytes_s",
    "temperature": "temperature_c",
}


@metrics_router.get("/metrics/current")
def current_metrics():
    return get_last_metrics()


@metrics_router.get("/metrics/history")
def metrics_history(
    metric: str = Query("cpu"),
    range: str = Query("1h"),
):
    hours = RANGE_HOURS.get(range, 1)
    col = METRIC_MAP.get(metric, "cpu_percent")
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    with Session(engine) as session:
        rows = (
            session.query(MetricsHistory)
            .filter(MetricsHistory.collected_at >= cutoff)
            .order_by(MetricsHistory.collected_at.asc())
            .all()
        )

    return {
        "metric": metric,
        "range": range,
        "data": [
            {"ts": r.collected_at.isoformat() + "Z", "value": getattr(r, col)}
            for r in rows
        ],
    }
```

- [ ] **Step 3: Rodar testes**

```bash
cd backend && python -m pytest tests/test_metrics_api.py -v
```
Esperado: 4 testes passando

- [ ] **Step 4: Commit**

```bash
git add backend/api/metrics.py backend/tests/test_metrics_api.py
git commit -m "feat: API REST de métricas com histórico por range"
```

---

## Task 9: Containers REST API

**Files:**
- Modify: `backend/api/containers.py` (substituir stub)
- Create: `backend/tests/test_containers_api.py`

**Interfaces:**
- Produces: `GET /api/containers`, `GET /api/containers/{id}/logs`
- Consumes: `get_last_metrics()`, `docker_client.get_logs()`

- [ ] **Step 1: Escrever testes**

```python
# backend/tests/test_containers_api.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

@pytest.fixture
def auth_client(test_db, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    import importlib, main, api.auth
    importlib.reload(api.auth)
    importlib.reload(main)
    client = TestClient(main.app)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client

def test_lista_containers(auth_client):
    with patch("collector.scheduler._last_metrics", {
        "containers": [{"id": "abc", "name": "web", "status": "running", "cpu_percent": 1.0}]
    }):
        r = auth_client.get("/api/containers")
    assert r.status_code == 200
    assert len(r.json()["containers"]) == 1

def test_containers_vazio(auth_client):
    with patch("collector.scheduler._last_metrics", {}):
        r = auth_client.get("/api/containers")
    assert r.status_code == 200
    assert r.json()["containers"] == []

def test_logs_container(auth_client):
    with patch("collector.scheduler.docker_client") as mock_dc:
        mock_dc.get_logs = AsyncMock(return_value=["linha 1", "linha 2"])
        r = auth_client.get("/api/containers/abc123/logs")
    assert r.status_code == 200
    assert r.json()["logs"] == ["linha 1", "linha 2"]
```

- [ ] **Step 2: Implementar backend/api/containers.py**

```python
from fastapi import APIRouter
from collector.scheduler import docker_client, get_last_metrics

containers_router = APIRouter()


@containers_router.get("/containers")
def list_containers():
    metrics = get_last_metrics()
    return {"containers": metrics.get("containers", [])}


@containers_router.get("/containers/{container_id}/logs")
async def get_logs(container_id: str, tail: int = 50):
    logs = await docker_client.get_logs(container_id, tail=tail)
    return {"logs": logs}
```

- [ ] **Step 3: Rodar testes**

```bash
cd backend && python -m pytest tests/test_containers_api.py -v
```
Esperado: 3 testes passando

- [ ] **Step 4: Rodar todos os testes do backend**

```bash
cd backend && python -m pytest tests/ -v
```
Esperado: todos passando (≥ 18 testes)

- [ ] **Step 5: Commit**

```bash
git add backend/api/containers.py backend/tests/test_containers_api.py
git commit -m "feat: API REST de containers com listagem e logs"
```
