# Gestão de Fail2ban Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Interface no monitor pra criar/editar/excluir jails do fail2ban (só os criados pelo próprio monitor, prefixo `vps-monitor-`), com validação real via `fail2ban-regex` antes de aplicar, jails manuais existentes em modo leitura, e desbanir IP disponível pra qualquer jail.

**Architecture:** Novo módulo `collector/fail2ban_client.py` (wrapper assíncrono em torno do binário `fail2ban-client`/`fail2ban-regex` via subprocess). Novo endpoint `api/fail2ban.py` com CRUD escopado por prefixo de nome + ação de unban irrestrita. Novo modelo `Fail2banActionLog` (audit log, mesmo padrão do `ContainerActionLog`). Novo volume mount + pacote no Dockerfile pra dar ao container do monitor acesso ao fail2ban do host.

**Tech Stack:** FastAPI + SQLAlchemy + pytest (backend, TDD), Next.js/React/TypeScript (frontend, sem suíte de testes — build + verificação manual pelo usuário), `fail2ban-client`/`fail2ban-regex` (CLI, via subprocess).

## Global Constraints

- Só é possível criar/editar/excluir jails cujo nome comece com `vps-monitor-` — qualquer tentativa em outro jail retorna 403.
- Desbanir IP (`unban`) é permitido em **qualquer** jail, inclusive os manuais.
- Toda criação/edição de jail passa por `fail2ban-regex` (dry-run real contra uma linha de exemplo fornecida pelo usuário) antes de escrever o arquivo de jail — se não bater, a operação é rejeitada e o arquivo de filtro recém-escrito é removido.
- Nunca reiniciar o fail2ban inteiro nem recarregar outro jail além do que está sendo criado/editado (`fail2ban-client reload <jail>`, nunca `reload --all` ou `restart`).
- Toda ação (criar/editar/excluir/desbanir) é registrada em `Fail2banActionLog`, sucesso ou falha.

---

### Task 1: `collector/fail2ban_client.py`

**Files:**
- Create: `backend/collector/fail2ban_client.py`
- Test: `backend/tests/test_fail2ban_client.py`

**Interfaces:**
- Produces: `async def status_all() -> list[dict]` (cada item: `{"nome": str, "managed": bool, "currently_banned": int, "total_banned": int, "currently_failed": int, "banned_ips": list[str]}`), `async def dry_run_regex(sample_line: str, filter_path: str) -> tuple[bool, str]`, `async def reload_jail(nome: str) -> None`, `async def stop_jail(nome: str) -> None`, `async def unban_ip(nome: str, ip: str) -> None`. Consumido pela Task 3.

- [ ] **Step 1: Escrever os testes (devem falhar)**

Criar `backend/tests/test_fail2ban_client.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_status_all_parseia_lista_de_jails_e_detalhes():
    from collector import fail2ban_client

    status_geral = "Status\n|- Number of jail:\t2\n`- Jail list:\tsshd, vps-monitor-teste\n"
    status_sshd = (
        "Status for the jail: sshd\n"
        "|- Filter\n|  |- Currently failed:\t0\n|  |- Total failed:\t3\n"
        "`- Actions\n   |- Currently banned:\t1\n   |- Total banned:\t2\n"
        "   `- Banned IP list:\t203.0.113.5\n"
    )
    status_vps_monitor = (
        "Status for the jail: vps-monitor-teste\n"
        "|- Filter\n|  |- Currently failed:\t0\n|  |- Total failed:\t0\n"
        "`- Actions\n   |- Currently banned:\t0\n   |- Total banned:\t0\n"
        "   `- Banned IP list:\n"
    )

    async def fake_run(binario, *args):
        if args == ("status",):
            return 0, status_geral, ""
        if args == ("status", "sshd"):
            return 0, status_sshd, ""
        if args == ("status", "vps-monitor-teste"):
            return 0, status_vps_monitor, ""
        raise AssertionError(f"chamada inesperada: {args}")

    with patch.object(fail2ban_client, "_run", AsyncMock(side_effect=fake_run)):
        jails = await fail2ban_client.status_all()

    assert len(jails) == 2
    sshd = next(j for j in jails if j["nome"] == "sshd")
    assert sshd["managed"] is False
    assert sshd["currently_banned"] == 1
    assert sshd["total_banned"] == 2
    assert sshd["banned_ips"] == ["203.0.113.5"]

    vps = next(j for j in jails if j["nome"] == "vps-monitor-teste")
    assert vps["managed"] is True
    assert vps["currently_banned"] == 0
    assert vps["banned_ips"] == []


@pytest.mark.asyncio
async def test_dry_run_regex_bate():
    from collector import fail2ban_client

    saida = (
        "Results\n=======\n\nFailregex: 1 total\n"
        "Ignoreregex: 0 total\n\nLines: 1 lines, 0 ignored, 1 matched, 0 missed\n"
    )
    with patch.object(fail2ban_client, "_run", AsyncMock(return_value=(0, saida, ""))):
        matched, out = await fail2ban_client.dry_run_regex("linha de exemplo", "/tmp/filtro.conf")

    assert matched is True
    assert "1 matched" in out


@pytest.mark.asyncio
async def test_dry_run_regex_nao_bate():
    from collector import fail2ban_client

    saida = "Results\n=======\n\nFailregex: 0 total\n\nLines: 1 lines, 0 ignored, 0 matched, 1 missed\n"
    with patch.object(fail2ban_client, "_run", AsyncMock(return_value=(0, saida, ""))):
        matched, out = await fail2ban_client.dry_run_regex("linha sem match", "/tmp/filtro.conf")

    assert matched is False


@pytest.mark.asyncio
async def test_reload_jail_chama_comando_correto():
    from collector import fail2ban_client

    mock_run = AsyncMock(return_value=(0, "", ""))
    with patch.object(fail2ban_client, "_run", mock_run):
        await fail2ban_client.reload_jail("vps-monitor-teste")

    mock_run.assert_awaited_once_with("fail2ban-client", "reload", "vps-monitor-teste")


@pytest.mark.asyncio
async def test_stop_jail_chama_comando_correto():
    from collector import fail2ban_client

    mock_run = AsyncMock(return_value=(0, "", ""))
    with patch.object(fail2ban_client, "_run", mock_run):
        await fail2ban_client.stop_jail("vps-monitor-teste")

    mock_run.assert_awaited_once_with("fail2ban-client", "stop", "vps-monitor-teste")


@pytest.mark.asyncio
async def test_unban_ip_chama_comando_correto():
    from collector import fail2ban_client

    mock_run = AsyncMock(return_value=(0, "", ""))
    with patch.object(fail2ban_client, "_run", mock_run):
        await fail2ban_client.unban_ip("sshd", "203.0.113.5")

    mock_run.assert_awaited_once_with("fail2ban-client", "set", "sshd", "unbanip", "203.0.113.5")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_fail2ban_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'collector.fail2ban_client'`

- [ ] **Step 3: Implementar o módulo**

Criar `backend/collector/fail2ban_client.py`:

```python
import asyncio
import re

_JAIL_LIST_RE = re.compile(r"Jail list:\s*(.*)")
_CURRENTLY_BANNED_RE = re.compile(r"Currently banned:\s*(\d+)")
_TOTAL_BANNED_RE = re.compile(r"Total banned:\s*(\d+)")
_BANNED_IP_LIST_RE = re.compile(r"Banned IP list:\s*(.*)")
_CURRENTLY_FAILED_RE = re.compile(r"Currently failed:\s*(\d+)")
_LINES_SUMMARY_RE = re.compile(r"Lines:\s*\d+\s*lines,\s*\d+\s*ignored,\s*(\d+)\s*matched,\s*\d+\s*missed")


async def _run(binario: str, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        binario, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


def _extract_int(pattern: re.Pattern, texto: str) -> int:
    m = pattern.search(texto)
    return int(m.group(1)) if m else 0


async def status_all() -> list[dict]:
    _, out, _ = await _run("fail2ban-client", "status")
    m = _JAIL_LIST_RE.search(out)
    nomes = [n.strip() for n in m.group(1).split(",") if n.strip()] if m else []

    jails = []
    for nome in nomes:
        _, jail_out, _ = await _run("fail2ban-client", "status", nome)
        ip_match = _BANNED_IP_LIST_RE.search(jail_out)
        banned_ips = ip_match.group(1).split() if ip_match and ip_match.group(1).strip() else []
        jails.append({
            "nome": nome,
            "managed": nome.startswith("vps-monitor-"),
            "currently_banned": _extract_int(_CURRENTLY_BANNED_RE, jail_out),
            "total_banned": _extract_int(_TOTAL_BANNED_RE, jail_out),
            "currently_failed": _extract_int(_CURRENTLY_FAILED_RE, jail_out),
            "banned_ips": banned_ips,
        })
    return jails


async def dry_run_regex(sample_line: str, filter_path: str) -> tuple[bool, str]:
    _, out, _ = await _run("fail2ban-regex", sample_line, filter_path)
    m = _LINES_SUMMARY_RE.search(out)
    matched = int(m.group(1)) > 0 if m else False
    return matched, out


async def reload_jail(nome: str) -> None:
    await _run("fail2ban-client", "reload", nome)


async def stop_jail(nome: str) -> None:
    await _run("fail2ban-client", "stop", nome)


async def unban_ip(nome: str, ip: str) -> None:
    await _run("fail2ban-client", "set", nome, "unbanip", ip)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_fail2ban_client.py -v`
Expected: PASS (6 testes)

- [ ] **Step 5: Commit**

```bash
git add backend/collector/fail2ban_client.py backend/tests/test_fail2ban_client.py
git commit -m "feat: adiciona fail2ban_client (wrapper assincrono do fail2ban-client)"
```

---

### Task 2: Modelo `Fail2banActionLog`

**Files:**
- Modify: `backend/models/database.py`
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Produces: `class Fail2banActionLog(Base)` com colunas `id, performed_at, username, jail_nome, acao, detalhes, sucesso, erro`. Consumido pela Task 3.

- [ ] **Step 1: Escrever o teste (deve falhar)**

Adicionar ao final de `backend/tests/test_database.py`:

```python
def test_insert_fail2ban_action_log(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        record = test_db.Fail2banActionLog(
            performed_at=datetime.utcnow(),
            username="admin",
            jail_nome="vps-monitor-teste",
            acao="create",
            sucesso=1,
        )
        session.add(record)
        session.commit()
        fetched = session.query(test_db.Fail2banActionLog).first()
    assert fetched.jail_nome == "vps-monitor-teste"
    assert fetched.acao == "create"
    assert fetched.sucesso == 1
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_database.py -v -k "fail2ban_action_log"`
Expected: FAIL — `AttributeError: module 'models.database' has no attribute 'Fail2banActionLog'`

- [ ] **Step 3: Implementar o modelo**

Em `backend/models/database.py`, logo depois da classe `ContainerActionLog`:

```python
class Fail2banActionLog(Base):
    __tablename__ = "fail2ban_action_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    performed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    username = Column(String, nullable=False)
    jail_nome = Column(String, nullable=False)
    acao = Column(String, nullable=False)
    detalhes = Column(Text, nullable=True)
    sucesso = Column(Integer, default=1)
    erro = Column(Text, nullable=True)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_database.py -v -k "fail2ban_action_log"`
Expected: PASS

- [ ] **Step 5: Rodar toda a suíte de test_database.py**

Run: `cd backend && py -m pytest tests/test_database.py -v`
Expected: todos os testes existentes continuam passando.

- [ ] **Step 6: Commit**

```bash
git add backend/models/database.py backend/tests/test_database.py
git commit -m "feat: adiciona modelo Fail2banActionLog"
```

---

### Task 3: `api/fail2ban.py` — CRUD de jails + unban

**Files:**
- Create: `backend/api/fail2ban.py`
- Test: `backend/tests/test_fail2ban_api.py`

**Interfaces:**
- Consumes: `fail2ban_client.status_all/dry_run_regex/reload_jail/stop_jail/unban_ip` (Task 1), `Fail2banActionLog` (Task 2).
- Produces: `router = APIRouter(prefix="/api/fail2ban", ...)` com `GET /jails`, `POST /jails`, `PUT /jails/{slug}`, `DELETE /jails/{slug}`, `POST /jails/{slug}/unban`. Consumido pela Task 4 (registro em `main.py`) e pela Task 5 (frontend).
- Usa duas variáveis de módulo configuráveis por env var: `FAIL2BAN_JAIL_DIR` (default `/etc/fail2ban/jail.d`), `FAIL2BAN_FILTER_DIR` (default `/etc/fail2ban/filter.d`) — permite apontar pra um diretório temporário nos testes.

- [ ] **Step 1: Escrever os testes (devem falhar)**

Criar `backend/tests/test_fail2ban_api.py`:

```python
import pytest
import importlib
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def auth_client(test_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    monkeypatch.setenv("FAIL2BAN_JAIL_DIR", str(tmp_path / "jail.d"))
    monkeypatch.setenv("FAIL2BAN_FILTER_DIR", str(tmp_path / "filter.d"))
    (tmp_path / "jail.d").mkdir()
    (tmp_path / "filter.d").mkdir()

    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth as auth_mod
    importlib.reload(auth_mod)
    import api.fail2ban as fail2ban_mod
    importlib.reload(fail2ban_mod)
    import main
    importlib.reload(main)

    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_list_jails(auth_client):
    with patch("api.fail2ban.fail2ban_client.status_all", AsyncMock(return_value=[
        {"nome": "sshd", "managed": False, "currently_banned": 0, "total_banned": 0, "currently_failed": 0, "banned_ips": []}
    ])):
        r = auth_client.get("/api/fail2ban/jails")
    assert r.status_code == 200
    assert r.json()[0]["nome"] == "sshd"


def test_criar_jail_sucesso(auth_client, test_db):
    with patch("api.fail2ban.fail2ban_client.dry_run_regex", AsyncMock(return_value=(True, "1 matched"))), \
         patch("api.fail2ban.fail2ban_client.reload_jail", AsyncMock(return_value=None)) as mock_reload:
        r = auth_client.post("/api/fail2ban/jails", json={
            "nome_exibicao": "Teste de Bloqueio",
            "log_path": "/var/log/teste.log",
            "sample_log_line": "203.0.113.5 - erro de teste",
            "regex": r"^<HOST> - erro de teste$",
            "maxretry": 5, "findtime": 600, "bantime": 3600, "port": "http,https",
        })
    assert r.status_code == 201
    assert r.json()["slug"] == "vps-monitor-teste-de-bloqueio"
    mock_reload.assert_awaited_once_with("vps-monitor-teste-de-bloqueio")

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        log = session.query(test_db.Fail2banActionLog).first()
    assert log.acao == "create"
    assert log.sucesso == 1


def test_criar_jail_regex_invalido(auth_client):
    r = auth_client.post("/api/fail2ban/jails", json={
        "nome_exibicao": "Teste Invalido",
        "log_path": "/var/log/teste.log",
        "sample_log_line": "linha qualquer",
        "regex": "(sem fechar",
        "maxretry": 5, "findtime": 600, "bantime": 3600, "port": "http,https",
    })
    assert r.status_code == 400


def test_criar_jail_dry_run_nao_bate(auth_client, test_db):
    with patch("api.fail2ban.fail2ban_client.dry_run_regex", AsyncMock(return_value=(False, "0 matched"))):
        r = auth_client.post("/api/fail2ban/jails", json={
            "nome_exibicao": "Nao Bate",
            "log_path": "/var/log/teste.log",
            "sample_log_line": "linha que nao bate",
            "regex": r"^padrao-que-nao-existe$",
            "maxretry": 5, "findtime": 600, "bantime": 3600, "port": "http,https",
        })
    assert r.status_code == 400
    assert "0 matched" in r.json()["detail"]

    import os
    assert not os.path.exists(os.path.join(os.environ["FAIL2BAN_FILTER_DIR"], "vps-monitor-nao-bate.conf"))


def test_editar_jail_bloqueia_sem_prefixo(auth_client):
    r = auth_client.put("/api/fail2ban/jails/sshd", json={
        "nome_exibicao": "sshd", "log_path": "/var/log/auth.log", "sample_log_line": "x",
        "regex": "^<HOST>$", "maxretry": 5, "findtime": 600, "bantime": 3600, "port": "ssh",
    })
    assert r.status_code == 403


def test_excluir_jail_bloqueia_sem_prefixo(auth_client):
    r = auth_client.delete("/api/fail2ban/jails/sshd")
    assert r.status_code == 403


def test_excluir_jail_gerenciado_sucesso(auth_client, test_db):
    import os
    jail_path = os.path.join(os.environ["FAIL2BAN_JAIL_DIR"], "vps-monitor-teste.local")
    filter_path = os.path.join(os.environ["FAIL2BAN_FILTER_DIR"], "vps-monitor-teste.conf")
    with open(jail_path, "w") as f:
        f.write("[vps-monitor-teste]\n")
    with open(filter_path, "w") as f:
        f.write("[Definition]\nfailregex = x\n")

    with patch("api.fail2ban.fail2ban_client.stop_jail", AsyncMock(return_value=None)) as mock_stop:
        r = auth_client.delete("/api/fail2ban/jails/vps-monitor-teste")

    assert r.status_code == 200
    mock_stop.assert_awaited_once_with("vps-monitor-teste")
    assert not os.path.exists(jail_path)
    assert not os.path.exists(filter_path)


def test_unban_funciona_em_qualquer_jail(auth_client, test_db):
    with patch("api.fail2ban.fail2ban_client.unban_ip", AsyncMock(return_value=None)) as mock_unban:
        r = auth_client.post("/api/fail2ban/jails/sshd/unban", json={"ip": "203.0.113.5"})
    assert r.status_code == 200
    mock_unban.assert_awaited_once_with("sshd", "203.0.113.5")

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        log = session.query(test_db.Fail2banActionLog).first()
    assert log.acao == "unban"
    assert log.jail_nome == "sshd"


def test_fail2ban_endpoints_sem_autenticacao_401():
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    assert client.get("/api/fail2ban/jails").status_code == 401
    assert client.post("/api/fail2ban/jails", json={}).status_code == 401
    assert client.delete("/api/fail2ban/jails/vps-monitor-x").status_code == 401
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_fail2ban_api.py -v`
Expected: FAIL em todos os testes — `ModuleNotFoundError: No module named 'api.fail2ban'` (a linha `import api.fail2ban as fail2ban_mod` dentro do fixture `auth_client` falha imediatamente, já que o módulo ainda não existe).

- [ ] **Step 3: Implementar o módulo**

Criar `backend/api/fail2ban.py`:

```python
import os
import re
import unicodedata
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models.database as db_module
from api.auth import get_token_data, verify_token_header
from collector import fail2ban_client
from models.database import Fail2banActionLog

FAIL2BAN_JAIL_DIR = os.environ.get("FAIL2BAN_JAIL_DIR", "/etc/fail2ban/jail.d")
FAIL2BAN_FILTER_DIR = os.environ.get("FAIL2BAN_FILTER_DIR", "/etc/fail2ban/filter.d")

router = APIRouter(prefix="/api/fail2ban", dependencies=[Depends(verify_token_header)])


def _slugify(nome_exibicao: str) -> str:
    nfkd = unicodedata.normalize("NFKD", nome_exibicao)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_str.lower()).strip("-")
    return f"vps-monitor-{slug}"


def _log_action(username: str, jail_nome: str, acao: str, sucesso: int = 1, erro: Optional[str] = None, detalhes: Optional[str] = None):
    with Session(db_module.engine) as session:
        session.add(Fail2banActionLog(
            username=username, jail_nome=jail_nome, acao=acao,
            sucesso=sucesso, erro=erro, detalhes=detalhes,
        ))
        session.commit()


class JailIn(BaseModel):
    nome_exibicao: str
    log_path: str
    sample_log_line: str
    regex: str
    maxretry: int = 5
    findtime: int = 600
    bantime: int = 3600
    port: str = "http,https"


class UnbanIn(BaseModel):
    ip: str


def _write_jail_files(slug: str, body: JailIn) -> tuple[str, str]:
    filter_path = os.path.join(FAIL2BAN_FILTER_DIR, f"{slug}.conf")
    with open(filter_path, "w", encoding="utf-8") as f:
        f.write(f"[Definition]\nfailregex = {body.regex}\nignoreregex =\n")

    jail_path = os.path.join(FAIL2BAN_JAIL_DIR, f"{slug}.local")
    with open(jail_path, "w", encoding="utf-8") as f:
        f.write(
            f"[{slug}]\nenabled = true\nbackend = auto\nfilter = {slug}\n"
            f"logpath = {body.log_path}\nport = {body.port}\n"
            f"maxretry = {body.maxretry}\nfindtime = {body.findtime}\n"
            f"bantime = {body.bantime}\nbanaction = nftables\n"
        )
    return jail_path, filter_path


@router.get("/jails")
async def list_jails():
    return await fail2ban_client.status_all()


@router.post("/jails", status_code=201)
async def create_jail(body: JailIn, token_data: dict = Depends(get_token_data)):
    slug = _slugify(body.nome_exibicao)
    username = token_data.get("sub", "desconhecido")

    try:
        re.compile(body.regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Regex inválido: {e}")

    filter_path = os.path.join(FAIL2BAN_FILTER_DIR, f"{slug}.conf")
    with open(filter_path, "w", encoding="utf-8") as f:
        f.write(f"[Definition]\nfailregex = {body.regex}\nignoreregex =\n")

    matched, saida = await fail2ban_client.dry_run_regex(body.sample_log_line, filter_path)
    if not matched:
        os.remove(filter_path)
        _log_action(username, slug, "create", sucesso=0, erro=saida)
        raise HTTPException(status_code=400, detail=f"O regex não bateu com a linha de exemplo fornecida: {saida}")

    jail_path = os.path.join(FAIL2BAN_JAIL_DIR, f"{slug}.local")
    with open(jail_path, "w", encoding="utf-8") as f:
        f.write(
            f"[{slug}]\nenabled = true\nbackend = auto\nfilter = {slug}\n"
            f"logpath = {body.log_path}\nport = {body.port}\n"
            f"maxretry = {body.maxretry}\nfindtime = {body.findtime}\n"
            f"bantime = {body.bantime}\nbanaction = nftables\n"
        )

    await fail2ban_client.reload_jail(slug)
    _log_action(username, slug, "create", sucesso=1)
    return {"slug": slug}


@router.put("/jails/{slug}")
async def update_jail(slug: str, body: JailIn, token_data: dict = Depends(get_token_data)):
    if not slug.startswith("vps-monitor-"):
        raise HTTPException(status_code=403, detail="Só é possível editar jails criados pelo monitor.")
    username = token_data.get("sub", "desconhecido")

    try:
        re.compile(body.regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Regex inválido: {e}")

    filter_path = os.path.join(FAIL2BAN_FILTER_DIR, f"{slug}.conf")
    with open(filter_path, "w", encoding="utf-8") as f:
        f.write(f"[Definition]\nfailregex = {body.regex}\nignoreregex =\n")

    matched, saida = await fail2ban_client.dry_run_regex(body.sample_log_line, filter_path)
    if not matched:
        _log_action(username, slug, "edit", sucesso=0, erro=saida)
        raise HTTPException(status_code=400, detail=f"O regex não bateu com a linha de exemplo fornecida: {saida}")

    jail_path = os.path.join(FAIL2BAN_JAIL_DIR, f"{slug}.local")
    with open(jail_path, "w", encoding="utf-8") as f:
        f.write(
            f"[{slug}]\nenabled = true\nbackend = auto\nfilter = {slug}\n"
            f"logpath = {body.log_path}\nport = {body.port}\n"
            f"maxretry = {body.maxretry}\nfindtime = {body.findtime}\n"
            f"bantime = {body.bantime}\nbanaction = nftables\n"
        )

    await fail2ban_client.reload_jail(slug)
    _log_action(username, slug, "edit", sucesso=1)
    return {"ok": True}


@router.delete("/jails/{slug}")
async def delete_jail(slug: str, token_data: dict = Depends(get_token_data)):
    if not slug.startswith("vps-monitor-"):
        raise HTTPException(status_code=403, detail="Só é possível excluir jails criados pelo monitor.")
    username = token_data.get("sub", "desconhecido")

    await fail2ban_client.stop_jail(slug)

    jail_path = os.path.join(FAIL2BAN_JAIL_DIR, f"{slug}.local")
    filter_path = os.path.join(FAIL2BAN_FILTER_DIR, f"{slug}.conf")
    if os.path.exists(jail_path):
        os.remove(jail_path)
    if os.path.exists(filter_path):
        os.remove(filter_path)

    _log_action(username, slug, "delete", sucesso=1)
    return {"ok": True}


@router.post("/jails/{slug}/unban")
async def unban(slug: str, body: UnbanIn, token_data: dict = Depends(get_token_data)):
    username = token_data.get("sub", "desconhecido")
    await fail2ban_client.unban_ip(slug, body.ip)
    _log_action(username, slug, "unban", detalhes=body.ip, sucesso=1)
    return {"ok": True}
```

- [ ] **Step 4: Registrar o router temporariamente pra rodar os testes desta task**

Esta task depende do router estar registrado em `main.py` (o fixture `auth_client` faz `import main`). Adiantar só o registro do router (o resto da Task 4 — Dockerfile/compose — pode vir depois):

Em `backend/main.py`, adicionar a linha de import:

```python
from api.fail2ban import router as fail2ban_router
```

E adicionar, junto aos outros `app.include_router`:

```python
app.include_router(fail2ban_router)
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_fail2ban_api.py -v`
Expected: PASS (9 testes)

- [ ] **Step 6: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: todos os testes passando (179 já existentes + 6 da Task 1 + 1 da Task 2 + 9 da Task 3 = 195), sem `FAILED`.

- [ ] **Step 7: Commit**

```bash
git add backend/api/fail2ban.py backend/tests/test_fail2ban_api.py backend/main.py
git commit -m "feat: adiciona CRUD de jails do fail2ban via API"
```

---

### Task 4: Docker — pacote fail2ban + volumes

**Files:**
- Modify: `backend/Dockerfile`
- Modify: `docker-compose.yml`

**Interfaces:** nenhuma (mudança de infraestrutura, sem código Python).

- [ ] **Step 1: Adicionar o pacote `fail2ban` ao Dockerfile**

Em `backend/Dockerfile`, no bloco `RUN apt-get update && apt-get install -y --no-install-recommends \`, adicionar `fail2ban` à lista de pacotes já instalados (mesma linha de `apt-get install`, sem criar um `RUN` novo).

- [ ] **Step 2: Adicionar os volumes no `docker-compose.yml`**

No serviço `monitor-backend`, trocar:

```yaml
    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - vps_monitor_data:/app/data
      - traefik_access_logs:/var/log/traefik:ro
```

por:

```yaml
    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - vps_monitor_data:/app/data
      - traefik_access_logs:/var/log/traefik:ro
      - /etc/fail2ban/jail.d:/etc/fail2ban/jail.d
      - /etc/fail2ban/filter.d:/etc/fail2ban/filter.d
      - /var/run/fail2ban/fail2ban.sock:/var/run/fail2ban/fail2ban.sock
```

- [ ] **Step 3: Build local pra confirmar que a imagem compila com o pacote novo**

Run: `docker build -t vps-monitor-backend-test ./backend` (se Docker estiver disponível localmente; se não, pular este step e confirmar só no deploy).
Expected: build sem erro, pacote `fail2ban` instalado.

- [ ] **Step 4: Commit**

```bash
git add backend/Dockerfile docker-compose.yml
git commit -m "feat: adiciona pacote fail2ban e volumes de acesso ao Dockerfile/compose"
```

---

### Task 5: Frontend — página `/seguranca`

**Files:**
- Create: `frontend/app/seguranca/page.tsx`
- Modify: componente de navegação lateral (localizar o arquivo que lista os links do menu — mesmo padrão de `/containers`, `/acessos`, `/alertas` já existentes)

**Interfaces:**
- Consumes: `GET/POST/PUT/DELETE /api/fail2ban/jails`, `POST /api/fail2ban/jails/{slug}/unban` (Task 3).

- [ ] **Step 1: Localizar o componente de navegação**

```bash
grep -rl "Containers\|Acessos" frontend/components/ frontend/app/layout.tsx 2>/dev/null
```

Identificar o arquivo que renderiza os links do menu lateral (visto nas screenshots desta sessão: Dashboard, Containers, Histórico, Alertas, Acessos, Configurações, Meus Dados).

- [ ] **Step 2: Criar a página**

Criar `frontend/app/seguranca/page.tsx` com:
- Uma tabela listando todos os jails (via `GET /api/fail2ban/jails`): nome, badge de "Gerenciado" ou "Manual" (baseado em `managed`), contagem de banidos, lista de IPs banidos com botão "Desbanir" ao lado de cada IP (chama `POST /api/fail2ban/jails/{nome}/unban`, disponível em qualquer jail).
- Botão "+ Novo Jail" que abre um modal com formulário: nome de exibição, caminho do log, linha de exemplo, regex, maxretry, findtime, bantime, porta — ao salvar, chama `POST /api/fail2ban/jails`; se retornar 400, mostra a mensagem de erro (saída do dry-run) no próprio modal sem fechá-lo.
- Jails com `managed: true` ganham botões "Editar" (reabre o modal preenchido, chama `PUT /api/fail2ban/jails/{slug}`) e "Excluir" (modal de confirmação, chama `DELETE /api/fail2ban/jails/{slug}`).
- Seguir os mesmos padrões visuais (cores `var(--card)`, `var(--border)`, etc.) já usados em `/containers` e `/alertas`.

- [ ] **Step 3: Adicionar o link no menu**

Adicionar "Segurança" ao componente de navegação identificado no Step 1, apontando pra `/seguranca`, seguindo o mesmo padrão dos links existentes.

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`
Expected: build limpo, sem erros de tipo, rota `/seguranca` aparece na lista de rotas geradas.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/seguranca/page.tsx frontend/components/<arquivo-de-navegacao>
git commit -m "feat: adiciona pagina de gestao de fail2ban (/seguranca)"
```

(Verificação manual no navegador fica por conta do usuário, combinado nesta sessão.)

---

### Task 6: Deploy para produção

**Files:** nenhum (ação operacional, sem mudança de código)

**Atenção:** esta task só deve ser executada após confirmação explícita do usuário — dá ao container `monitor-backend` acesso de escrita a `/etc/fail2ban/jail.d` e `/etc/fail2ban/filter.d` do host, além do socket de controle do fail2ban. É o maior nível de acesso ao host que o monitor já teve.

- [ ] **Step 1: Push para o remoto**

```bash
git push origin main
```

- [ ] **Step 2: Deploy na VPS**

```bash
ssh root@144.91.92.70 "cd /opt/vps-monitor && git pull --ff-only && bash monitor/deploy.sh"
```

- [ ] **Step 3: Confirmar containers saudáveis**

```bash
ssh root@144.91.92.70 "docker ps --filter name=monitor --filter name=vps-monitor --format '{{.Names}}\t{{.Status}}'"
```

Expected: todos `Up`, sem `Restarting`.

- [ ] **Step 4: Confirmar que o monitor-backend enxerga o fail2ban**

```bash
ssh root@144.91.92.70 "docker exec monitor-backend fail2ban-client status" 2>&1
```

Expected: saída normal do `fail2ban-client status`, listando os jails existentes (`sshd`, `mecanicapro-ghost-subdomain`, etc.) — confirma que o socket foi montado corretamente e o binário está instalado.

- [ ] **Step 5: Teste manual end-to-end (opcional, recomendado)**

Criar um jail de teste bem inofensivo pela UI (`/seguranca`), confirmar que aparece em `fail2ban-client status` na VPS, editar, e por fim excluir — confirmando que some tanto da UI quanto de `fail2ban-client status` e que os arquivos em `/etc/fail2ban/jail.d`/`filter.d` foram removidos.
