# Registro de Acessos ao Sistema por IP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adiciona uma tela "Acessos" ao painel, mostrando quantos acessos cada IP fez e a quais sistemas hospedados na VPS (incluindo o próprio monitor), com filtro por sistema e por IP, e um modal de detalhe com geolocalização ao clicar num IP.

**Architecture:** Um coletor novo (`access_log_tailer.py`) lê incrementalmente o access log JSON do Traefik (fonte de verdade de quem acessou o quê — todos os sistemas da VPS rodam atrás dele), filtra ruído (assets estáticos, health-checks) e grava em duas tabelas: `AccessLog` (detalhe cru, retenção curta) e `AccessLogDaily` (contador agregado por dia/IP/sistema, retenção longa — reaproveita as duas configs de retenção já existentes no projeto). Um módulo de geolocalização (`geoip.py`) consulta a API pública `ip-api.com` só na primeira vez que vê um IP, com cache em `IpGeoCache`. Uma API nova (`access_logs.py`) expõe agregação e detalhe por IP; o frontend ganha uma página de listagem e um modal de detalhe.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite), httpx, APScheduler, pytest/pytest-asyncio, Next.js/React (TypeScript).

## Global Constraints

- `sistema` é sempre o domínio bruto (`RequestHost` do Traefik) — sem nome amigável configurável nesta entrega.
- `AccessLog` (detalhe) é limpo por `retention_detailed_days` (padrão 7 dias); `AccessLogDaily` (agregado) por `retention_aggregated_days` (padrão 30 dias) — ambas já existem em `Config`, nenhuma config nova é criada.
- Ruído filtrado no coletor: extensões `js css map png jpg jpeg gif svg ico woff woff2 ttf webp avif`, e paths `/favicon.ico`, `/robots.txt`, `/health`, `/healthz`, `/.well-known/*`.
- Geolocalização via `ip-api.com`, sem chave de API; IPs privados/loopback nunca geram chamada externa; resultado (inclusive falha) é cacheado em `IpGeoCache` por IP, para sempre.
- O arquivo do access log do Traefik é uma dependência de infraestrutura fora deste repositório — o coletor deve tolerar sua ausência sem lançar exceção (só loga aviso uma vez).
- Todas as rotas novas em `access_logs.py` exigem o mesmo JWT que as demais rotas protegidas (`verify_token_header`).

---

### Task 1: Schema — `AccessLog`, `AccessLogDaily`, `IpGeoCache`

**Files:**
- Modify: `backend/models/database.py`
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Produces: `AccessLog` (id, accessed_at: DateTime, ip: String, sistema: String, path: String, method: String, status_code: Integer, user_agent: Text nullable). `AccessLogDaily` (id, day: String "YYYY-MM-DD", ip: String, sistema: String, count: Integer). `IpGeoCache` (ip: String PK, country/region/city/isp/org: String nullable, lat/lon: Float nullable, is_private: Integer, looked_up_at: DateTime). Usadas pelas Tasks 2, 3, 4, 5.

- [ ] **Step 1: Escrever os testes que falham**

Adicione ao final de `backend/tests/test_database.py`:

```python
def test_tabela_access_log_criada(test_db):
    with test_db.engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result}
    assert "access_log" in tables
    assert "access_log_daily" in tables
    assert "ip_geo_cache" in tables


def test_insert_access_log(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        session.add(test_db.AccessLog(
            accessed_at=datetime.utcnow(),
            ip="203.0.113.10",
            sistema="app2.dlsistemas.com.br",
            path="/api/pedidos",
            method="GET",
            status_code=200,
            user_agent="Mozilla/5.0",
        ))
        session.commit()
        fetched = session.query(test_db.AccessLog).first()
    assert fetched.ip == "203.0.113.10"
    assert fetched.sistema == "app2.dlsistemas.com.br"


def test_insert_access_log_daily(test_db):
    with Session(test_db.engine) as session:
        session.add(test_db.AccessLogDaily(
            day="2026-07-12", ip="203.0.113.10", sistema="app2.dlsistemas.com.br", count=5,
        ))
        session.commit()
        fetched = session.query(test_db.AccessLogDaily).first()
    assert fetched.count == 5


def test_insert_ip_geo_cache(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        session.add(test_db.IpGeoCache(
            ip="203.0.113.10", country="Brazil", city="São Paulo",
            is_private=0, looked_up_at=datetime.utcnow(),
        ))
        session.commit()
        fetched = session.get(test_db.IpGeoCache, "203.0.113.10")
    assert fetched.country == "Brazil"
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && py -m pytest tests/test_database.py -k "access_log or ip_geo_cache" -v`
Expected: FAIL — `AttributeError: module 'models.database' has no attribute 'AccessLog'`.

- [ ] **Step 3: Adicionar os models**

Em `backend/models/database.py`, o import do SQLAlchemy (linhas 3-6) é:

```python
from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    DateTime, Text, text, ForeignKey
)
```

Troque por (adiciona `Index`):

```python
from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    DateTime, Text, text, ForeignKey, Index
)
```

Depois da classe `ContainerActionLog` (termina na linha 113 com `erro = Column(Text, nullable=True)`) e antes de `class Config(Base):`, insira:

```python
class AccessLog(Base):
    __tablename__ = "access_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    accessed_at = Column(DateTime, nullable=False)
    ip = Column(String, nullable=False)
    sistema = Column(String, nullable=False)
    path = Column(String, nullable=False)
    method = Column(String, nullable=False)
    status_code = Column(Integer)
    user_agent = Column(Text, nullable=True)


Index("ix_access_log_accessed_at", AccessLog.accessed_at)
Index("ix_access_log_ip", AccessLog.ip)


class AccessLogDaily(Base):
    __tablename__ = "access_log_daily"
    id = Column(Integer, primary_key=True, autoincrement=True)
    day = Column(String, nullable=False)
    ip = Column(String, nullable=False)
    sistema = Column(String, nullable=False)
    count = Column(Integer, nullable=False, default=0)


Index("ix_access_log_daily_day", AccessLogDaily.day)
Index("ix_access_log_daily_ip", AccessLogDaily.ip)


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

Nenhuma migração (`ALTER TABLE`) é necessária — são tabelas novas, criadas automaticamente por `Base.metadata.create_all(engine)` dentro de `init_db()`.

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd backend && py -m pytest tests/test_database.py -v`
Expected: PASS (todos, incluindo os pré-existentes).

- [ ] **Step 5: Commit**

```bash
git add backend/models/database.py backend/tests/test_database.py
git commit -m "feat: adiciona models AccessLog, AccessLogDaily e IpGeoCache"
```

---

### Task 2: Geolocalização de IP (`geoip.py`)

**Files:**
- Create: `backend/collector/geoip.py`
- Test: `backend/tests/test_geoip.py`

**Interfaces:**
- Consumes: `models.database.IpGeoCache` (Task 1).
- Produces: `async def lookup_ip(ip: str, session: Session) -> dict` retornando `{"is_private": bool, "country": str|None, "region": str|None, "city": str|None, "isp": str|None, "org": str|None, "lat": float|None, "lon": float|None}` — usado pela Task 5.

- [ ] **Step 1: Escrever os testes que falham**

Crie `backend/tests/test_geoip.py`:

```python
import pytest


class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


class _FakeAsyncClient:
    calls = 0

    def __init__(self, json_data=None, exc=None):
        self._json_data = json_data
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        _FakeAsyncClient.calls += 1
        if self._exc:
            raise self._exc
        return _FakeResponse(self._json_data)


@pytest.mark.asyncio
async def test_ip_privado_nao_chama_api_externa(test_db, monkeypatch):
    import collector.geoip as geoip
    _FakeAsyncClient.calls = 0
    monkeypatch.setattr(geoip.httpx, "AsyncClient", lambda timeout=5.0: _FakeAsyncClient())

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        result = await geoip.lookup_ip("192.168.1.10", session)

    assert result["is_private"] is True
    assert _FakeAsyncClient.calls == 0


@pytest.mark.asyncio
async def test_ip_publico_chama_api_e_grava_cache(test_db, monkeypatch):
    import collector.geoip as geoip
    fake_data = {
        "status": "success", "country": "Brazil", "regionName": "SP",
        "city": "São Paulo", "isp": "Provedor X", "org": "Org Y",
        "lat": -23.5, "lon": -46.6, "query": "203.0.113.10",
    }
    _FakeAsyncClient.calls = 0
    monkeypatch.setattr(geoip.httpx, "AsyncClient", lambda timeout=5.0: _FakeAsyncClient(json_data=fake_data))

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        result = await geoip.lookup_ip("203.0.113.10", session)

    assert result["is_private"] is False
    assert result["country"] == "Brazil"
    assert result["city"] == "São Paulo"
    assert _FakeAsyncClient.calls == 1

    with Session(test_db.engine) as session:
        cached = session.get(test_db.IpGeoCache, "203.0.113.10")
    assert cached is not None
    assert cached.isp == "Provedor X"


@pytest.mark.asyncio
async def test_segunda_chamada_usa_cache(test_db, monkeypatch):
    import collector.geoip as geoip
    fake_data = {"status": "success", "country": "Brazil", "query": "203.0.113.10"}
    _FakeAsyncClient.calls = 0
    monkeypatch.setattr(geoip.httpx, "AsyncClient", lambda timeout=5.0: _FakeAsyncClient(json_data=fake_data))

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        await geoip.lookup_ip("203.0.113.10", session)
    with Session(test_db.engine) as session:
        await geoip.lookup_ip("203.0.113.10", session)

    assert _FakeAsyncClient.calls == 1


@pytest.mark.asyncio
async def test_erro_api_nao_lanca_excecao(test_db, monkeypatch):
    import collector.geoip as geoip
    _FakeAsyncClient.calls = 0
    monkeypatch.setattr(
        geoip.httpx, "AsyncClient",
        lambda timeout=5.0: _FakeAsyncClient(exc=Exception("timeout")),
    )

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        result = await geoip.lookup_ip("203.0.113.10", session)

    assert result["is_private"] is False
    assert result["country"] is None

    with Session(test_db.engine) as session:
        cached = session.get(test_db.IpGeoCache, "203.0.113.10")
    assert cached is not None
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && py -m pytest tests/test_geoip.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'collector.geoip'`.

- [ ] **Step 3: Implementar `geoip.py`**

Crie `backend/collector/geoip.py`:

```python
import ipaddress
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from models.database import IpGeoCache

GEO_API_URL = "http://ip-api.com/json/{ip}"
GEO_API_FIELDS = "status,message,country,regionName,city,isp,org,lat,lon,query"


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return addr.is_private or addr.is_loopback or addr.is_link_local


def _geo_dict(row: IpGeoCache) -> dict:
    return {
        "is_private": bool(row.is_private),
        "country": row.country,
        "region": row.region,
        "city": row.city,
        "isp": row.isp,
        "org": row.org,
        "lat": row.lat,
        "lon": row.lon,
    }


async def lookup_ip(ip: str, session: Session) -> dict:
    cached = session.get(IpGeoCache, ip)
    if cached is not None:
        return _geo_dict(cached)

    if _is_private_ip(ip):
        row = IpGeoCache(ip=ip, is_private=1, looked_up_at=datetime.utcnow())
        session.add(row)
        session.commit()
        return _geo_dict(row)

    country = region = city = isp = org = None
    lat = lon = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(GEO_API_URL.format(ip=ip), params={"fields": GEO_API_FIELDS})
            r.raise_for_status()
            data = r.json()
        if data.get("status") == "success":
            country = data.get("country")
            region = data.get("regionName")
            city = data.get("city")
            isp = data.get("isp")
            org = data.get("org")
            lat = data.get("lat")
            lon = data.get("lon")
    except Exception:
        pass

    row = IpGeoCache(
        ip=ip, country=country, region=region, city=city, isp=isp, org=org,
        lat=lat, lon=lon, is_private=0, looked_up_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    return _geo_dict(row)
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd backend && py -m pytest tests/test_geoip.py -v`
Expected: PASS (4 testes).

- [ ] **Step 5: Commit**

```bash
git add backend/collector/geoip.py backend/tests/test_geoip.py
git commit -m "feat: geolocalizacao de IP com cache via ip-api.com"
```

---

### Task 3: Coletor — tail do access log do Traefik

**Files:**
- Create: `backend/collector/access_log_tailer.py`
- Test: `backend/tests/test_access_log_tailer.py`

**Interfaces:**
- Consumes: `models.database.AccessLog`, `AccessLogDaily`, `Config`, `engine` (Task 1, acessados via `db_module.X` para funcionar corretamente com o reload de módulo usado pelos testes).
- Produces: `async def tail_access_log() -> None` — usado pela Task 4 (scheduler).

- [ ] **Step 1: Escrever os testes que falham**

Crie `backend/tests/test_access_log_tailer.py`:

```python
import json
import os
from datetime import datetime

import pytest


def _traefik_line(client_host="203.0.113.10", host="app2.dlsistemas.com.br", path="/api/pedidos", status=200, when=None):
    when = when or datetime.utcnow()
    return json.dumps({
        "ClientHost": client_host,
        "RequestHost": host,
        "RequestPath": path,
        "RequestMethod": "GET",
        "DownstreamStatus": status,
        "time": when.isoformat() + "Z",
        "request_User-Agent": "Mozilla/5.0",
    })


@pytest.mark.asyncio
async def test_processa_linha_valida_grava_access_log_e_daily(test_db, tmp_path, monkeypatch):
    log_file = tmp_path / "access.log"
    log_file.write_text(_traefik_line() + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        access = session.query(test_db.AccessLog).first()
        daily = session.query(test_db.AccessLogDaily).first()

    assert access is not None
    assert access.ip == "203.0.113.10"
    assert access.sistema == "app2.dlsistemas.com.br"
    assert access.path == "/api/pedidos"
    assert daily.count == 1
    assert daily.ip == "203.0.113.10"


@pytest.mark.asyncio
async def test_linha_de_asset_estatico_e_descartada(test_db, tmp_path, monkeypatch):
    log_file = tmp_path / "access.log"
    log_file.write_text(_traefik_line(path="/static/app.js") + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        assert session.query(test_db.AccessLog).count() == 0


@pytest.mark.asyncio
async def test_linha_de_health_check_e_descartada(test_db, tmp_path, monkeypatch):
    log_file = tmp_path / "access.log"
    log_file.write_text(_traefik_line(path="/health") + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        assert session.query(test_db.AccessLog).count() == 0


@pytest.mark.asyncio
async def test_linha_invalida_nao_interrompe_processamento(test_db, tmp_path, monkeypatch):
    log_file = tmp_path / "access.log"
    log_file.write_text("isso nao e json\n" + _traefik_line() + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        assert session.query(test_db.AccessLog).count() == 1


@pytest.mark.asyncio
async def test_offset_persiste_entre_chamadas(test_db, tmp_path, monkeypatch):
    log_file = tmp_path / "access.log"
    log_file.write_text(_traefik_line(client_host="203.0.113.10") + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(_traefik_line(client_host="198.51.100.20") + "\n")

    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        ips = {row.ip for row in session.query(test_db.AccessLog).all()}
    assert ips == {"203.0.113.10", "198.51.100.20"}


@pytest.mark.asyncio
async def test_mudanca_de_inode_reseta_offset(test_db, tmp_path, monkeypatch):
    log_file = tmp_path / "access.log"
    log_file.write_text(_traefik_line(client_host="203.0.113.10") + "\n", encoding="utf-8")
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(log_file))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()

    os.remove(log_file)
    log_file.write_text(_traefik_line(client_host="198.51.100.20") + "\n", encoding="utf-8")

    await tailer.tail_access_log()

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        ips = {row.ip for row in session.query(test_db.AccessLog).all()}
    assert ips == {"203.0.113.10", "198.51.100.20"}


@pytest.mark.asyncio
async def test_arquivo_ausente_nao_lanca_excecao(test_db, tmp_path, monkeypatch):
    monkeypatch.setenv("TRAEFIK_ACCESS_LOG_PATH", str(tmp_path / "nao-existe.log"))

    import collector.access_log_tailer as tailer
    await tailer.tail_access_log()  # não deve levantar

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        assert session.query(test_db.AccessLog).count() == 0
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && py -m pytest tests/test_access_log_tailer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'collector.access_log_tailer'`.

- [ ] **Step 3: Implementar `access_log_tailer.py`**

Crie `backend/collector/access_log_tailer.py`:

```python
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

import models.database as db_module

logger = logging.getLogger(__name__)

_STATIC_EXTENSIONS = {
    "js", "css", "map", "png", "jpg", "jpeg", "gif", "svg", "ico",
    "woff", "woff2", "ttf", "webp", "avif",
}
_NOISE_PATHS = {"/favicon.ico", "/robots.txt", "/health", "/healthz"}
_warned_missing_file = False


def _log_path() -> str:
    return os.environ.get("TRAEFIK_ACCESS_LOG_PATH", "/var/log/traefik/access.log")


def _is_noise(path: str) -> bool:
    if path in _NOISE_PATHS or path.startswith("/.well-known/"):
        return True
    last_segment = path.rsplit("/", 1)[-1]
    if "." not in last_segment:
        return False
    ext = last_segment.rsplit(".", 1)[-1].lower()
    return ext in _STATIC_EXTENSIONS


def _parse_time(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return datetime.utcnow()


def _get_offset(session: Session) -> tuple[int, Optional[int]]:
    offset_row = session.get(db_module.Config, "access_log_offset")
    inode_row = session.get(db_module.Config, "access_log_inode")
    offset = int(offset_row.value) if offset_row else 0
    inode = int(inode_row.value) if inode_row else None
    return offset, inode


def _save_offset(session: Session, offset: int, inode: int) -> None:
    for key, value in (("access_log_offset", str(offset)), ("access_log_inode", str(inode))):
        row = session.get(db_module.Config, key)
        if row:
            row.value = value
        else:
            session.add(db_module.Config(key=key, value=value))
    session.commit()


def _upsert_daily(session: Session, day: str, ip: str, sistema: str) -> None:
    row = (
        session.query(db_module.AccessLogDaily)
        .filter_by(day=day, ip=ip, sistema=sistema)
        .first()
    )
    if row:
        row.count += 1
    else:
        session.add(db_module.AccessLogDaily(day=day, ip=ip, sistema=sistema, count=1))


def _process_line(session: Session, line: str) -> None:
    line = line.strip()
    if not line:
        return
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return

    path = entry.get("RequestPath", "")
    if _is_noise(path):
        return

    ip = entry.get("ClientHost")
    sistema = entry.get("RequestHost")
    if not ip or not sistema:
        return

    accessed_at = _parse_time(entry.get("time", ""))
    session.add(db_module.AccessLog(
        accessed_at=accessed_at,
        ip=ip,
        sistema=sistema,
        path=path,
        method=entry.get("RequestMethod", ""),
        status_code=entry.get("DownstreamStatus"),
        user_agent=entry.get("request_User-Agent"),
    ))
    _upsert_daily(session, accessed_at.strftime("%Y-%m-%d"), ip, sistema)


async def tail_access_log() -> None:
    global _warned_missing_file
    path_obj = Path(_log_path())

    if not path_obj.exists():
        if not _warned_missing_file:
            logger.warning("Access log do Traefik não encontrado em %s", path_obj)
            _warned_missing_file = True
        return
    _warned_missing_file = False

    current_inode = path_obj.stat().st_ino

    with Session(db_module.engine) as session:
        offset, saved_inode = _get_offset(session)
        if saved_inode is not None and saved_inode != current_inode:
            offset = 0

        with open(path_obj, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            for line in f:
                _process_line(session, line)
            new_offset = f.tell()

        session.commit()
        _save_offset(session, new_offset, current_inode)
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd backend && py -m pytest tests/test_access_log_tailer.py -v`
Expected: PASS (7 testes).

- [ ] **Step 5: Commit**

```bash
git add backend/collector/access_log_tailer.py backend/tests/test_access_log_tailer.py
git commit -m "feat: coletor le access log do Traefik e grava acessos por IP/sistema"
```

---

### Task 4: Scheduler — job periódico e limpeza por retenção

**Files:**
- Modify: `backend/collector/scheduler.py`
- Test: `backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `tail_access_log` (Task 3), `AccessLog`/`AccessLogDaily` (Task 1).

- [ ] **Step 1: Escrever o teste que falha**

Adicione ao final de `backend/tests/test_scheduler.py`:

```python
@pytest.mark.asyncio
async def test_cleanup_remove_access_log_e_daily_antigos(test_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    from datetime import datetime, timedelta
    from sqlalchemy.orm import Session

    with Session(test_db.engine) as session:
        session.add(test_db.AccessLog(
            accessed_at=datetime.utcnow() - timedelta(days=10),
            ip="203.0.113.10", sistema="app2.dlsistemas.com.br",
            path="/api/x", method="GET", status_code=200,
        ))
        session.add(test_db.AccessLog(
            accessed_at=datetime.utcnow(),
            ip="203.0.113.10", sistema="app2.dlsistemas.com.br",
            path="/api/y", method="GET", status_code=200,
        ))
        old_day = (datetime.utcnow() - timedelta(days=40)).strftime("%Y-%m-%d")
        recent_day = datetime.utcnow().strftime("%Y-%m-%d")
        session.add(test_db.AccessLogDaily(day=old_day, ip="203.0.113.10", sistema="app2.dlsistemas.com.br", count=3))
        session.add(test_db.AccessLogDaily(day=recent_day, ip="203.0.113.10", sistema="app2.dlsistemas.com.br", count=1))
        session.commit()

    import collector.scheduler as sched
    await sched._cleanup()

    with Session(test_db.engine) as session:
        access_rows = session.query(test_db.AccessLog).all()
        daily_rows = session.query(test_db.AccessLogDaily).all()

    assert len(access_rows) == 1
    assert access_rows[0].path == "/api/y"
    assert len(daily_rows) == 1
    assert daily_rows[0].day == recent_day
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_scheduler.py -k "cleanup_remove_access_log" -v`
Expected: FAIL — os registros antigos ainda estão no banco (`_cleanup` ainda não limpa `AccessLog`/`AccessLogDaily`).

- [ ] **Step 3: Ligar o coletor e a limpeza no scheduler**

Em `backend/collector/scheduler.py`, a linha 11 é:

```python
from models.database import ContainerDiskUsage, ContainerMetrics, MetricsHistory, engine
```

Troque por:

```python
from models.database import AccessLog, AccessLogDaily, ContainerDiskUsage, ContainerMetrics, MetricsHistory, engine
```

Logo abaixo, na linha 12 (`from notifications.alert_engine import evaluate`), adicione a nova importação:

```python
from collector.access_log_tailer import tail_access_log
```

Na função `_cleanup` (linhas 104-120), o segundo bloco `with Session(engine) as session:` é:

```python
    with Session(engine) as session:
        session.query(MetricsHistory).filter(MetricsHistory.collected_at < detailed_cutoff).delete()
        session.query(ContainerMetrics).filter(ContainerMetrics.collected_at < aggregated_cutoff).delete()
        session.query(ContainerDiskUsage).filter(ContainerDiskUsage.collected_at < aggregated_cutoff).delete()
        session.commit()
```

Troque por:

```python
    with Session(engine) as session:
        session.query(MetricsHistory).filter(MetricsHistory.collected_at < detailed_cutoff).delete()
        session.query(ContainerMetrics).filter(ContainerMetrics.collected_at < aggregated_cutoff).delete()
        session.query(ContainerDiskUsage).filter(ContainerDiskUsage.collected_at < aggregated_cutoff).delete()
        session.query(AccessLog).filter(AccessLog.accessed_at < detailed_cutoff).delete()
        session.query(AccessLogDaily).filter(AccessLogDaily.day < aggregated_cutoff.strftime("%Y-%m-%d")).delete()
        session.commit()
```

Por fim, `start_scheduler()` (linhas 127-134) é:

```python
def start_scheduler():
    scheduler.add_job(collect_and_store, "interval", seconds=30, id="collect", replace_existing=True)
    scheduler.add_job(collect_disk_usage, "interval", minutes=10, id="disk_usage", replace_existing=True)
    scheduler.add_job(_cleanup, "interval", hours=1, id="cleanup", replace_existing=True)
    if not scheduler.running:
        scheduler.start()
    asyncio.ensure_future(collect_and_store())
    asyncio.ensure_future(collect_disk_usage())
```

Troque por:

```python
def start_scheduler():
    scheduler.add_job(collect_and_store, "interval", seconds=30, id="collect", replace_existing=True)
    scheduler.add_job(collect_disk_usage, "interval", minutes=10, id="disk_usage", replace_existing=True)
    scheduler.add_job(tail_access_log, "interval", seconds=15, id="access_log_tail", replace_existing=True)
    scheduler.add_job(_cleanup, "interval", hours=1, id="cleanup", replace_existing=True)
    if not scheduler.running:
        scheduler.start()
    asyncio.ensure_future(collect_and_store())
    asyncio.ensure_future(collect_disk_usage())
    asyncio.ensure_future(tail_access_log())
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_scheduler.py -v`
Expected: PASS (todos, incluindo os pré-existentes).

- [ ] **Step 5: Commit**

```bash
git add backend/collector/scheduler.py backend/tests/test_scheduler.py
git commit -m "feat: agenda coleta de access log a cada 15s e limpa por retencao"
```

---

### Task 5: API — `/api/access-logs`

**Files:**
- Create: `backend/api/access_logs.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_access_logs_api.py`

**Interfaces:**
- Consumes: `AccessLog`, `AccessLogDaily` (Task 1), `lookup_ip` (Task 2).
- Produces: `GET /api/access-logs/summary`, `GET /api/access-logs/sistemas`, `GET /api/access-logs/ip/{ip}` — usados pelas Tasks 7 e 8.

- [ ] **Step 1: Escrever os testes que falham**

Crie `backend/tests/test_access_logs_api.py`:

```python
import importlib
import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import AsyncMock, patch


@pytest.fixture
def auth_client(test_db, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth
    importlib.reload(api.auth)
    import api.access_logs
    importlib.reload(api.access_logs)
    import main
    importlib.reload(main)
    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client, test_db


def _seed_daily(db, day, ip, sistema, count):
    with Session(db.engine) as session:
        session.add(db.AccessLogDaily(day=day, ip=ip, sistema=sistema, count=count))
        session.commit()


def test_summary_agrega_por_ip(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 5)
    _seed_daily(db, today, "203.0.113.10", "monitor.dlsistemas.com.br", 2)
    _seed_daily(db, today, "198.51.100.20", "app2.dlsistemas.com.br", 1)

    r = client.get("/api/access-logs/summary?days=7")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    top = data[0]
    assert top["ip"] == "203.0.113.10"
    assert top["total_acessos"] == 7
    assert {s["sistema"] for s in top["sistemas"]} == {"app2.dlsistemas.com.br", "monitor.dlsistemas.com.br"}


def test_summary_filtra_por_sistema(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 5)
    _seed_daily(db, today, "203.0.113.10", "monitor.dlsistemas.com.br", 2)

    r = client.get("/api/access-logs/summary?days=7&sistema=monitor.dlsistemas.com.br")
    data = r.json()
    assert len(data) == 1
    assert data[0]["total_acessos"] == 2


def test_summary_filtra_por_ip_prefixo(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 5)
    _seed_daily(db, today, "198.51.100.20", "app2.dlsistemas.com.br", 1)

    r = client.get("/api/access-logs/summary?days=7&ip=203.0.113")
    data = r.json()
    assert len(data) == 1
    assert data[0]["ip"] == "203.0.113.10"


def test_summary_ignora_dias_fora_da_janela(auth_client):
    client, db = auth_client
    old_day = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d")
    _seed_daily(db, old_day, "203.0.113.10", "app2.dlsistemas.com.br", 5)

    r = client.get("/api/access-logs/summary?days=7")
    assert r.json() == []


def test_sistemas_retorna_lista_distinta(auth_client):
    client, db = auth_client
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 5)
    _seed_daily(db, today, "198.51.100.20", "monitor.dlsistemas.com.br", 1)

    r = client.get("/api/access-logs/sistemas")
    assert r.status_code == 200
    assert set(r.json()) == {"app2.dlsistemas.com.br", "monitor.dlsistemas.com.br"}


def test_ip_detail_retorna_geo_e_recentes(auth_client):
    client, db = auth_client
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    _seed_daily(db, today, "203.0.113.10", "app2.dlsistemas.com.br", 3)
    with Session(db.engine) as session:
        session.add(db.AccessLog(
            accessed_at=now, ip="203.0.113.10", sistema="app2.dlsistemas.com.br",
            path="/api/pedidos", method="GET", status_code=200,
        ))
        session.commit()

    fake_geo = {"is_private": False, "country": "Brazil", "region": "SP", "city": "São Paulo", "isp": "X", "org": "Y", "lat": -23.5, "lon": -46.6}
    with patch("api.access_logs.lookup_ip", AsyncMock(return_value=fake_geo)):
        r = client.get("/api/access-logs/ip/203.0.113.10?days=7")

    assert r.status_code == 200
    data = r.json()
    assert data["ip"] == "203.0.113.10"
    assert data["geo"]["country"] == "Brazil"
    assert data["total_acessos"] == 3
    assert len(data["acessos_recentes"]) == 1
    assert data["acessos_recentes"][0]["path"] == "/api/pedidos"


def test_endpoints_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/access-logs/summary").status_code == 401
    assert client.get("/api/access-logs/sistemas").status_code == 401
    assert client.get("/api/access-logs/ip/203.0.113.10").status_code == 401
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd backend && py -m pytest tests/test_access_logs_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.access_logs'`.

- [ ] **Step 3: Implementar a API**

Crie `backend/api/access_logs.py`:

```python
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.auth import verify_token_header
from collector.geoip import lookup_ip
from models.database import AccessLog, AccessLogDaily, get_session

router = APIRouter(prefix="/api/access-logs", dependencies=[Depends(verify_token_header)])


def _cutoff_day(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")


@router.get("/summary")
def summary(
    sistema: Optional[str] = None,
    ip: Optional[str] = None,
    days: int = Query(30),
    session: Session = Depends(get_session),
):
    cutoff = _cutoff_day(days)
    q = session.query(AccessLogDaily).filter(AccessLogDaily.day >= cutoff)
    if sistema:
        q = q.filter(AccessLogDaily.sistema == sistema)
    if ip:
        q = q.filter(AccessLogDaily.ip.like(f"{ip}%"))
    rows = q.all()

    by_ip: dict[str, dict] = {}
    for r in rows:
        entry = by_ip.setdefault(r.ip, {
            "ip": r.ip, "total_acessos": 0, "sistemas": {},
            "primeiro_acesso": r.day, "ultimo_acesso": r.day,
        })
        entry["total_acessos"] += r.count
        entry["sistemas"][r.sistema] = entry["sistemas"].get(r.sistema, 0) + r.count
        if r.day < entry["primeiro_acesso"]:
            entry["primeiro_acesso"] = r.day
        if r.day > entry["ultimo_acesso"]:
            entry["ultimo_acesso"] = r.day

    result = [
        {
            "ip": v["ip"],
            "total_acessos": v["total_acessos"],
            "sistemas": [
                {"sistema": s, "count": c}
                for s, c in sorted(v["sistemas"].items(), key=lambda x: -x[1])
            ],
            "primeiro_acesso": v["primeiro_acesso"],
            "ultimo_acesso": v["ultimo_acesso"],
        }
        for v in by_ip.values()
    ]
    result.sort(key=lambda x: -x["total_acessos"])
    return result


@router.get("/sistemas")
def sistemas(session: Session = Depends(get_session)):
    rows = session.query(AccessLogDaily.sistema).distinct().order_by(AccessLogDaily.sistema).all()
    return [r[0] for r in rows]


@router.get("/ip/{ip}")
async def ip_detail(
    ip: str,
    days: int = Query(30),
    session: Session = Depends(get_session),
):
    cutoff = _cutoff_day(days)
    daily_rows = (
        session.query(AccessLogDaily)
        .filter(AccessLogDaily.ip == ip, AccessLogDaily.day >= cutoff)
        .all()
    )

    sistemas_map: dict[str, int] = {}
    for r in daily_rows:
        sistemas_map[r.sistema] = sistemas_map.get(r.sistema, 0) + r.count

    recentes = (
        session.query(AccessLog)
        .filter(AccessLog.ip == ip)
        .order_by(AccessLog.accessed_at.desc())
        .limit(200)
        .all()
    )

    ultimo_por_sistema: dict[str, str] = {}
    for row in recentes:
        ts = row.accessed_at.isoformat() + "Z"
        if row.sistema not in ultimo_por_sistema:
            ultimo_por_sistema[row.sistema] = ts

    geo = await lookup_ip(ip, session)

    return {
        "ip": ip,
        "geo": geo,
        "total_acessos": sum(sistemas_map.values()),
        "sistemas": [
            {"sistema": s, "count": c, "ultimo_acesso": ultimo_por_sistema.get(s)}
            for s, c in sorted(sistemas_map.items(), key=lambda x: -x[1])
        ],
        "acessos_recentes": [
            {
                "sistema": r.sistema,
                "path": r.path,
                "method": r.method,
                "status_code": r.status_code,
                "accessed_at": r.accessed_at.isoformat() + "Z",
            }
            for r in recentes
        ],
    }
```

Em `backend/main.py`, adicione o import junto aos demais routers (após a linha `from api.whatsapp import router as whatsapp_router`):

```python
from api.access_logs import router as access_logs_router
```

E adicione o `include_router` junto aos demais (após `app.include_router(whatsapp_router)`):

```python
app.include_router(access_logs_router)
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd backend && py -m pytest tests/test_access_logs_api.py -v`
Expected: PASS (7 testes).

- [ ] **Step 5: Commit**

```bash
git add backend/api/access_logs.py backend/main.py backend/tests/test_access_logs_api.py
git commit -m "feat: API de acessos por IP (summary, sistemas, detalhe com geo)"
```

---

### Task 6: Infraestrutura — `docker-compose.yml` e `.env.example`

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`

**Interfaces:**
- N/A (config apenas).

- [ ] **Step 1: Adicionar env var e volume ao `monitor-backend`**

Em `docker-compose.yml`, o bloco `monitor-backend.environment` (linhas 7-16) é:

```yaml
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
```

Troque por (adiciona `TRAEFIK_ACCESS_LOG_PATH`):

```yaml
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
      - TRAEFIK_ACCESS_LOG_PATH=/var/log/traefik/access.log
```

O bloco `monitor-backend.volumes` (linhas 17-21) é:

```yaml
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - vps_monitor_data:/app/data
```

Troque por:

```yaml
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - vps_monitor_data:/app/data
      - traefik_access_logs:/var/log/traefik:ro
```

O bloco `volumes:` no final do arquivo (linhas 60-61) é:

```yaml
volumes:
  vps_monitor_data:
```

Troque por (declara o volume do Traefik como externo — precisa já existir na stack do Traefik):

```yaml
volumes:
  vps_monitor_data:
  # Criado/gerenciado pela stack do Traefik (fora deste repo). Requer que o
  # Traefik tenha accessLog em JSON habilitado, gravando nesse volume, e que
  # o nome do volume aqui bata com o nome real do volume do Traefik.
  traefik_access_logs:
    external: true
```

- [ ] **Step 2: Documentar a env var no `.env.example`**

Em `.env.example`, ao final do arquivo, adicione:

```
# Caminho do access log do Traefik (JSON) dentro do container monitor-backend,
# montado read-only a partir do volume `traefik_access_logs` (ver docker-compose.yml).
# Depende da stack do Traefik ter accessLog em JSON habilitado — sem isso, a
# tela de Acessos fica vazia (o coletor apenas loga um aviso, sem falhar).
TRAEFIK_ACCESS_LOG_PATH=/var/log/traefik/access.log
```

- [ ] **Step 3: Validar sintaxe do compose**

Run: `docker compose config --quiet`
Expected: sem erros (o volume externo não precisa existir para a validação de sintaxe passar — só para o `up` de verdade).

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat: monta access log do Traefik no monitor-backend"
```

---

### Task 7: Frontend — página "Acessos"

**Files:**
- Modify: `frontend/app/layout.tsx`
- Create: `frontend/app/acessos/page.tsx`

**Interfaces:**
- Consumes: `GET /access-logs/summary?sistema=&ip=&days=`, `GET /access-logs/sistemas` (Task 5).
- Produces: rota `/acessos`, item de navegação — a Task 8 adiciona o modal chamado a partir desta página.

- [ ] **Step 1: Adicionar item de navegação**

Em `frontend/app/layout.tsx`, o array `NAV` (linhas 7-14) é:

```tsx
const NAV = [
  { href: '/', label: 'Dashboard', icon: '📊' },
  { href: '/containers', label: 'Containers', icon: '🐳' },
  { href: '/historico', label: 'Histórico', icon: '📈' },
  { href: '/alertas', label: 'Alertas', icon: '🔔' },
  { href: '/configuracoes', label: 'Configurações', icon: '⚙️' },
  { href: '/minha-conta', label: 'Meus Dados', icon: '👤' },
];
```

Troque por (adiciona "Acessos" entre "Alertas" e "Configurações"):

```tsx
const NAV = [
  { href: '/', label: 'Dashboard', icon: '📊' },
  { href: '/containers', label: 'Containers', icon: '🐳' },
  { href: '/historico', label: 'Histórico', icon: '📈' },
  { href: '/alertas', label: 'Alertas', icon: '🔔' },
  { href: '/acessos', label: 'Acessos', icon: '🌐' },
  { href: '/configuracoes', label: 'Configurações', icon: '⚙️' },
  { href: '/minha-conta', label: 'Meus Dados', icon: '👤' },
];
```

- [ ] **Step 2: Criar a página de listagem**

Crie `frontend/app/acessos/page.tsx`:

```tsx
'use client';
import { useState, useEffect, useCallback } from 'react';
import api from '../../lib/api';
import AccessIpModal from '../../components/AccessIpModal';

type Range = '24h' | '7d' | '30d';

interface SistemaCount { sistema: string; count: number; }
interface AccessSummaryRow {
  ip: string;
  total_acessos: number;
  sistemas: SistemaCount[];
  primeiro_acesso: string;
  ultimo_acesso: string;
}

const RANGES: { value: Range; label: string; days: number }[] = [
  { value: '24h', label: '24 horas', days: 1 },
  { value: '7d', label: '7 dias', days: 7 },
  { value: '30d', label: '30 dias', days: 30 },
];

export default function AcessosPage() {
  const [range, setRange] = useState<Range>('7d');
  const [sistemaFiltro, setSistemaFiltro] = useState('');
  const [ipFiltro, setIpFiltro] = useState('');
  const [sistemas, setSistemas] = useState<string[]>([]);
  const [rows, setRows] = useState<AccessSummaryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [ipSelecionado, setIpSelecionado] = useState<string | null>(null);

  const days = RANGES.find(r => r.value === range)!.days;

  const loadSistemas = useCallback(async () => {
    try {
      const r = await api.get('/access-logs/sistemas');
      setSistemas(r.data ?? []);
    } catch { setSistemas([]); }
  }, []);

  const loadSummary = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number> = { days };
      if (sistemaFiltro) params.sistema = sistemaFiltro;
      if (ipFiltro) params.ip = ipFiltro;
      const r = await api.get('/access-logs/summary', { params });
      setRows(r.data ?? []);
    } catch { setRows([]); }
    finally { setLoading(false); }
  }, [days, sistemaFiltro, ipFiltro]);

  useEffect(() => { loadSistemas(); }, [loadSistemas]);
  useEffect(() => {
    const t = setTimeout(loadSummary, 300);
    return () => clearTimeout(t);
  }, [loadSummary]);

  const tabBtn = (active: boolean): React.CSSProperties => ({
    padding: '6px 14px', borderRadius: 6, border: '1px solid var(--border)',
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#000' : 'var(--muted)',
    fontWeight: active ? 700 : 400,
    cursor: 'pointer', fontSize: 12,
  });

  return (
    <div>
      <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 24 }}>Acessos</h1>

      <div style={{ display: 'flex', gap: 20, marginBottom: 20, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Período</div>
          <div style={{ display: 'flex', gap: 6 }}>
            {RANGES.map(r => (
              <button key={r.value} onClick={() => setRange(r.value)} style={tabBtn(range === r.value)}>
                {r.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Sistema</div>
          <select
            value={sistemaFiltro}
            onChange={(e) => setSistemaFiltro(e.target.value)}
            style={{
              padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
              background: 'var(--surface)', color: 'var(--text)', fontSize: 12,
            }}
          >
            <option value="">Todos</option>
            {sistemas.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>

        <div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase' }}>Filtrar por IP</div>
          <input
            placeholder="ex: 203.0.113"
            value={ipFiltro}
            onChange={(e) => setIpFiltro(e.target.value)}
            style={{
              padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)',
              background: 'var(--surface)', color: 'var(--text)', fontSize: 12, width: 160,
            }}
          />
        </div>
      </div>

      <div style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
              {['IP', 'Acessos', 'Sistemas acessados', 'Último acesso'].map((h) => (
                <th key={h} style={{
                  padding: '10px 16px', textAlign: 'left', fontSize: 11,
                  color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={4} style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>
                  {loading ? 'Carregando...' : 'Nenhum acesso registrado no período.'}
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.ip} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '10px 16px' }}>
                    <button
                      onClick={() => setIpSelecionado(row.ip)}
                      style={{
                        background: 'none', border: 'none', color: 'var(--accent)',
                        cursor: 'pointer', fontFamily: 'monospace', fontSize: 13, padding: 0,
                      }}
                    >
                      {row.ip}
                    </button>
                  </td>
                  <td style={{ padding: '10px 16px' }}>{row.total_acessos}</td>
                  <td style={{ padding: '10px 16px' }}>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {row.sistemas.slice(0, 3).map(s => (
                        <span key={s.sistema} style={{
                          fontSize: 11, padding: '2px 8px', borderRadius: 10,
                          background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--muted)',
                        }}>
                          {s.sistema} ({s.count})
                        </span>
                      ))}
                      {row.sistemas.length > 3 && (
                        <span style={{ fontSize: 11, color: 'var(--muted)' }}>+{row.sistemas.length - 3}</span>
                      )}
                    </div>
                  </td>
                  <td style={{ padding: '10px 16px', color: 'var(--muted)', fontSize: 12 }}>{row.ultimo_acesso}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {ipSelecionado && (
        <AccessIpModal ip={ipSelecionado} days={days} onClose={() => setIpSelecionado(null)} />
      )}
    </div>
  );
}
```

Este arquivo importa `AccessIpModal`, criado na Task 8 — o build só passa depois dela. Continue para a Task 8 antes de verificar o build.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/layout.tsx frontend/app/acessos/page.tsx
git commit -m "feat: pagina Acessos com filtro por sistema e IP"
```

---

### Task 8: Frontend — modal de detalhe do IP

**Files:**
- Create: `frontend/components/AccessIpModal.tsx`

**Interfaces:**
- Consumes: `GET /access-logs/ip/{ip}?days=` (Task 5). Props: `{ ip: string; days: number; onClose: () => void }` — usado pela Task 7.

- [ ] **Step 1: Criar o componente**

Crie `frontend/components/AccessIpModal.tsx`:

```tsx
'use client';
import { useEffect, useState } from 'react';
import api from '../lib/api';

interface Geo {
  is_private: boolean;
  country: string | null;
  region: string | null;
  city: string | null;
  isp: string | null;
  org: string | null;
  lat: number | null;
  lon: number | null;
}
interface SistemaDetalhe { sistema: string; count: number; ultimo_acesso: string | null; }
interface AcessoRecente { sistema: string; path: string; method: string; status_code: number | null; accessed_at: string; }
interface IpDetail {
  ip: string;
  geo: Geo;
  total_acessos: number;
  sistemas: SistemaDetalhe[];
  acessos_recentes: AcessoRecente[];
}

interface Props {
  ip: string;
  days: number;
  onClose: () => void;
}

export default function AccessIpModal({ ip, days, onClose }: Props) {
  const [detail, setDetail] = useState<IpDetail | null>(null);
  const [erro, setErro] = useState('');

  useEffect(() => {
    let cancelado = false;
    setDetail(null);
    setErro('');
    api.get(`/access-logs/ip/${ip}`, { params: { days } })
      .then(r => { if (!cancelado) setDetail(r.data); })
      .catch(() => { if (!cancelado) setErro('Erro ao carregar detalhes do IP.'); });
    return () => { cancelado = true; };
  }, [ip, days]);

  const overlay: React.CSSProperties = {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
    zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
  };
  const modal: React.CSSProperties = {
    background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12,
    width: '85%', maxWidth: 640, maxHeight: '85vh', display: 'flex', flexDirection: 'column',
  };

  return (
    <div style={overlay} onClick={onClose}>
      <div style={modal} onClick={(e) => e.stopPropagation()}>
        <div style={{
          padding: '14px 20px', borderBottom: '1px solid var(--border)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span style={{ fontWeight: 600, fontFamily: 'monospace' }}>{ip}</span>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 22 }}
          >×</button>
        </div>

        <div style={{ padding: 20, overflow: 'auto' }}>
          {erro && <p style={{ color: 'var(--danger)' }}>{erro}</p>}
          {!detail && !erro && <p style={{ color: 'var(--muted)' }}>Carregando...</p>}

          {detail && (
            <>
              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, textTransform: 'uppercase' }}>Localização</div>
                {detail.geo.is_private ? (
                  <p style={{ fontSize: 13 }}>IP privado/local (rede interna).</p>
                ) : detail.geo.country ? (
                  <table style={{ fontSize: 13, width: '100%', borderCollapse: 'collapse' }}>
                    <tbody>
                      {([
                        ['País', detail.geo.country],
                        ['Região', detail.geo.region ?? '—'],
                        ['Cidade', detail.geo.city ?? '—'],
                        ['Provedor (ISP)', detail.geo.isp ?? '—'],
                        ['Organização', detail.geo.org ?? '—'],
                      ] as [string, string][]).map(([k, v]) => (
                        <tr key={k} style={{ borderBottom: '1px solid var(--border)' }}>
                          <td style={{ padding: '4px 0', color: 'var(--muted)', width: 140 }}>{k}</td>
                          <td style={{ padding: '4px 0' }}>{v}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p style={{ fontSize: 13, color: 'var(--muted)' }}>Localização indisponível.</p>
                )}
              </div>

              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, textTransform: 'uppercase' }}>
                  Sistemas acessados ({detail.total_acessos} acessos no período)
                </div>
                <table style={{ fontSize: 13, width: '100%', borderCollapse: 'collapse' }}>
                  <tbody>
                    {detail.sistemas.map(s => (
                      <tr key={s.sistema} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '4px 0' }}>{s.sistema}</td>
                        <td style={{ padding: '4px 0', textAlign: 'right' }}>{s.count} acessos</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, textTransform: 'uppercase' }}>Acessos recentes</div>
                <div style={{ maxHeight: 240, overflow: 'auto', fontFamily: 'monospace', fontSize: 11 }}>
                  {detail.acessos_recentes.length === 0 ? (
                    <p style={{ color: 'var(--muted)' }}>Sem detalhe disponível para este período.</p>
                  ) : (
                    detail.acessos_recentes.map((a, i) => (
                      <div key={i} style={{ padding: '3px 0', borderBottom: '1px solid var(--border)' }}>
                        {a.accessed_at} — {a.method} {a.sistema}{a.path} ({a.status_code ?? '—'})
                      </div>
                    ))
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Rodar o build do frontend**

Run: `cd frontend && npm run build`
Expected: build conclui sem erros de tipo ou lint.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/AccessIpModal.tsx
git commit -m "feat: modal de detalhe de IP com geolocalizacao e acessos recentes"
```

---

### Task 9: Verificação final da suíte completa

**Files:**
- Nenhum arquivo modificado — apenas verificação.

**Interfaces:**
- N/A.

- [ ] **Step 1: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: mesmas 3 falhas pré-existentes de antes desta feature (`test_metrics_api.py::test_sem_autenticacao_401`, `test_scheduler.py::test_collect_and_store_salva_no_banco`, `test_websocket.py::test_websocket_conecta_e_recebe` — falhas de isolamento de módulo/JWT_SECRET entre testes, não relacionadas a este trabalho) e nenhuma falha nova. Se alguma falha nova aparecer, ela precisa ser corrigida antes de prosseguir.

- [ ] **Step 2: Rodar o build do frontend**

Run: `cd frontend && npm run build`
Expected: build conclui sem erros de tipo ou lint.

- [ ] **Step 3: Validar sintaxe final do compose**

Run: `docker compose config --quiet`
Expected: sem erros.

## Verificação manual (pós-implementação, em ambiente com Docker de verdade)

1. Na stack do Traefik (fora deste repo), habilitar `accessLog` em JSON e garantir que ele grava no volume `traefik_access_logs` (ver Task 6). Sem isso, a tela de Acessos fica vazia — comportamento esperado, não é bug.
2. Subir o stack (`docker compose up -d --build`) e conferir nos logs do `monitor-backend` que não aparece o aviso "Access log do Traefik não encontrado" repetidamente (ele só aparece uma vez, se o arquivo realmente não existir).
3. Gerar tráfego real em pelo menos dois sistemas hospedados na VPS (incluindo o próprio `monitor.dlsistemas.com.br`) a partir de IPs diferentes.
4. Abrir a página **Acessos**: conferir que os IPs aparecem com contagem e chips de sistema corretos; testar o filtro por sistema (dropdown) e por IP (texto); trocar o período (24h/7d/30d).
5. Clicar num IP: conferir que o modal abre, mostra geolocalização (ou "IP privado/local" se for tráfego interno) e a lista de acessos recentes com path/método/status.
6. Esperar mais de `retention_detailed_days` (padrão 7 dias, ajustável em Configurações) e confirmar que `access_log` foi limpo mas `access_log_daily` (e portanto a contagem na tela) permanece, dentro de `retention_aggregated_days` (padrão 30 dias).

## Self-Review

**Cobertura do spec:**
- Coleta via tail do access log do Traefik, com offset persistente e tolerância a rotação/ausência do arquivo → Task 3.
- Filtro de ruído (assets estáticos, health-checks) → Task 3.
- `AccessLog` (detalhe) + `AccessLogDaily` (agregado), retenção reaproveitando `retention_detailed_days`/`retention_aggregated_days` → Tasks 1, 4.
- Geolocalização via `ip-api.com` com cache, IP privado sem chamada externa → Task 2.
- API de summary/sistemas/detalhe protegida por JWT → Task 5.
- Pré-requisito de infraestrutura documentado (volume compartilhado com o Traefik) → Task 6.
- Tela "Acessos" com filtro por sistema e IP, período, tabela por IP → Task 7.
- Modal de detalhe do IP (geo + sistemas + acessos recentes) ao clicar → Task 8.
- Fora de escopo (nome amigável por domínio, alerta de anomalia, bloqueio de IP, refresh manual de geo, múltiplos arquivos de log) — nenhuma task implementa isso, conforme spec.
