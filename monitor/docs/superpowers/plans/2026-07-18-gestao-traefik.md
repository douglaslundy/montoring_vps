# Gestão de Rotas Dinâmicas do Traefik Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Interface no monitor pra criar/editar/excluir arquivos de rota dinâmica do Traefik (`/opt/traefik/dynamic/*.yml`, só os criados pelo próprio monitor, prefixo `vps-monitor-`), com validação de sintaxe YAML antes de gravar, arquivos manuais existentes (`mecanicapro.yml`) em modo leitura, e histórico via commit automático no git que já existe em `/opt/traefik` (aplicado por um script no host, não pelo container).

**Architecture:** Helper `_slugify` do fail2ban extraído pra `api/_slug.py` (compartilhado). Novo endpoint `api/traefik.py` com CRUD escopado por prefixo de nome, validando sintaxe via `yaml.safe_load` antes de gravar. Novo modelo `TraefikActionLog` (audit log, mesmo padrão do `Fail2banActionLog`). Mount `/opt/traefik/dynamic` no `docker-compose.yml` muda de `:ro` pra `:rw`. Novo script `scripts/traefik-dynamic-commit-watcher.sh` (cron no host) faz o commit git — não entra em nenhum código Python, roda fora do container pra não expor `/opt/traefik/certs/acme.json` (chave privada). O Traefik em si não muda: já recarrega sozinho (`file.watch: true`) e isola erros por arquivo (confirmado com teste ao vivo em produção durante o brainstorming).

**Tech Stack:** FastAPI + SQLAlchemy + pytest (backend, TDD), PyYAML (validação de sintaxe), Next.js/React/TypeScript (frontend, sem suíte de testes — build + verificação manual pelo usuário), bash + git (watcher no host).

## Global Constraints

- Só é possível criar/editar/excluir arquivos cujo nome comece com `vps-monitor-` — qualquer tentativa em outro arquivo (ex: `mecanicapro.yml`) retorna 403.
- Toda escrita (`POST`/`PUT`) valida `yaml.safe_load(yaml_content)` antes de gravar — se o parser falhar, a operação é rejeitada com 400 e o arquivo original (se existir) não é tocado.
- Nenhum código Python roda `git`, `fail2ban-client` ou qualquer comando do Traefik — o container só lê/escreve arquivos em `/opt/traefik/dynamic`. O commit git é responsabilidade exclusiva do script no host.
- Toda ação (criar/editar/excluir) é registrada em `TraefikActionLog`, sucesso ou falha.
- `TRAEFIK_DYNAMIC_DIR` (env var, default `/opt/traefik/dynamic`) é a mesma variável já usada em `api/projects.py` — não criar uma segunda env var pro mesmo diretório.

---

### Task 1: Extrair `_slugify` compartilhado pra `api/_slug.py`

**Files:**
- Create: `backend/api/_slug.py`
- Modify: `backend/api/fail2ban.py`
- Test: `backend/tests/test_slug.py`

**Interfaces:**
- Produces: `def slugify(nome_exibicao: str, prefix: str = "vps-monitor-") -> str`. Consumido pela Task 3 (`api/traefik.py`) e por `api/fail2ban.py` (refatorado nesta task).

- [ ] **Step 1: Escrever o teste (deve falhar)**

Criar `backend/tests/test_slug.py`:

```python
from api._slug import slugify


def test_slugify_gera_slug_com_prefixo_padrao():
    assert slugify("Teste de Bloqueio") == "vps-monitor-teste-de-bloqueio"


def test_slugify_remove_acentos_e_caracteres_especiais():
    assert slugify("Nóvo Cliénte! (Wildcard)") == "vps-monitor-novo-cliente-wildcard"


def test_slugify_aceita_prefixo_customizado():
    assert slugify("Exemplo", prefix="outro-prefixo-") == "outro-prefixo-exemplo"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_slug.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api._slug'`

- [ ] **Step 3: Implementar o módulo**

Criar `backend/api/_slug.py`:

```python
import re
import unicodedata


def slugify(nome_exibicao: str, prefix: str = "vps-monitor-") -> str:
    nfkd = unicodedata.normalize("NFKD", nome_exibicao)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_str.lower()).strip("-")
    return f"{prefix}{slug}"
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_slug.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Refatorar `api/fail2ban.py` pra usar o helper compartilhado**

Em `backend/api/fail2ban.py`, remover o import `import unicodedata` (não é mais usado nesse arquivo) e o import `import re` continua (ainda usado em `re.compile(body.regex)`).

Substituir:

```python
def _slugify(nome_exibicao: str) -> str:
    nfkd = unicodedata.normalize("NFKD", nome_exibicao)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_str.lower()).strip("-")
    return f"vps-monitor-{slug}"
```

por:

```python
from api._slug import slugify as _slugify
```

(mover essa linha pro topo do arquivo, junto aos outros imports, removendo a definição de função antiga do meio do arquivo).

- [ ] **Step 6: Rodar a suíte de fail2ban pra confirmar que nada quebrou**

Run: `cd backend && py -m pytest tests/test_fail2ban_api.py tests/test_fail2ban_client.py -v`
Expected: PASS (todos os testes existentes, sem nenhuma mudança de comportamento)

- [ ] **Step 7: Commit**

```bash
git add backend/api/_slug.py backend/api/fail2ban.py backend/tests/test_slug.py
git commit -m "refactor: extrai slugify compartilhado pra api/_slug.py"
```

---

### Task 2: Modelo `TraefikActionLog`

**Files:**
- Modify: `backend/models/database.py`
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Produces: `class TraefikActionLog(Base)` com colunas `id, performed_at, username, filename, acao, sucesso, erro`. Consumido pela Task 3.

- [ ] **Step 1: Escrever o teste (deve falhar)**

Adicionar ao final de `backend/tests/test_database.py`:

```python
def test_insert_traefik_action_log(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        record = test_db.TraefikActionLog(
            performed_at=datetime.utcnow(),
            username="admin",
            filename="vps-monitor-teste.yml",
            acao="create",
            sucesso=1,
        )
        session.add(record)
        session.commit()
        fetched = session.query(test_db.TraefikActionLog).first()
    assert fetched.filename == "vps-monitor-teste.yml"
    assert fetched.acao == "create"
    assert fetched.sucesso == 1
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_database.py -v -k "traefik_action_log"`
Expected: FAIL — `AttributeError: module 'models.database' has no attribute 'TraefikActionLog'`

- [ ] **Step 3: Implementar o modelo**

Em `backend/models/database.py`, logo depois da classe `Fail2banActionLog`:

```python
class TraefikActionLog(Base):
    __tablename__ = "traefik_action_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    performed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    username = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    acao = Column(String, nullable=False)
    sucesso = Column(Integer, default=1)
    erro = Column(Text, nullable=True)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_database.py -v -k "traefik_action_log"`
Expected: PASS

- [ ] **Step 5: Rodar toda a suíte de test_database.py**

Run: `cd backend && py -m pytest tests/test_database.py -v`
Expected: todos os testes existentes continuam passando.

- [ ] **Step 6: Commit**

```bash
git add backend/models/database.py backend/tests/test_database.py
git commit -m "feat: adiciona modelo TraefikActionLog"
```

---

### Task 3: `api/traefik.py` — CRUD de rotas dinâmicas

**Files:**
- Create: `backend/api/traefik.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_traefik_api.py`

**Interfaces:**
- Consumes: `slugify` (Task 1), `TraefikActionLog` (Task 2).
- Produces: `router = APIRouter(prefix="/api/traefik", ...)` com `GET /routes`, `POST /routes`, `PUT /routes/{filename}`, `DELETE /routes/{filename}`. Consumido pela Task 4 (registro em `main.py`) e Task 6 (frontend).
- Usa a env var `TRAEFIK_DYNAMIC_DIR` (default `/opt/traefik/dynamic`, já usada em `api/projects.py`) — permite apontar pra um diretório temporário nos testes.

- [ ] **Step 1: Adicionar PyYAML ao `requirements.txt`**

Em `backend/requirements.txt`, adicionar ao final:

```
PyYAML==6.0.2
```

- [ ] **Step 2: Instalar localmente pra rodar os testes**

Run: `cd backend && pip install PyYAML==6.0.2`
Expected: instalado sem erro.

- [ ] **Step 3: Escrever os testes (devem falhar)**

Criar `backend/tests/test_traefik_api.py`:

```python
import os
import pytest
import importlib
from fastapi.testclient import TestClient


@pytest.fixture
def auth_client(test_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    monkeypatch.setenv("TRAEFIK_DYNAMIC_DIR", str(tmp_path / "dynamic"))
    (tmp_path / "dynamic").mkdir()

    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth as auth_mod
    importlib.reload(auth_mod)
    import api.traefik as traefik_mod
    importlib.reload(traefik_mod)
    import main
    importlib.reload(main)

    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


VALID_YAML = (
    "http:\n"
    "  routers:\n"
    "    exemplo:\n"
    "      rule: \"Host(`exemplo.dlsistemas.com.br`)\"\n"
    "      service: exemplo\n"
    "  services:\n"
    "    exemplo:\n"
    "      loadBalancer:\n"
    "        servers:\n"
    "          - url: \"http://172.17.0.1:9000\"\n"
)

INVALID_YAML = "http:\n  routers:\n    exemplo rule: [\n"


def test_listar_rotas(auth_client):
    dynamic_dir = os.environ["TRAEFIK_DYNAMIC_DIR"]
    with open(os.path.join(dynamic_dir, "mecanicapro.yml"), "w") as f:
        f.write(VALID_YAML)
    with open(os.path.join(dynamic_dir, "vps-monitor-teste.yml"), "w") as f:
        f.write(VALID_YAML)

    r = auth_client.get("/api/traefik/routes")
    assert r.status_code == 200
    rotas = {item["filename"]: item for item in r.json()}
    assert rotas["mecanicapro.yml"]["managed"] is False
    assert rotas["vps-monitor-teste.yml"]["managed"] is True
    assert rotas["mecanicapro.yml"]["content"] == VALID_YAML


def test_criar_rota_sucesso(auth_client, test_db):
    r = auth_client.post("/api/traefik/routes", json={
        "nome_exibicao": "Teste de Rota",
        "yaml_content": VALID_YAML,
    })
    assert r.status_code == 201
    assert r.json()["filename"] == "vps-monitor-teste-de-rota.yml"

    dynamic_dir = os.environ["TRAEFIK_DYNAMIC_DIR"]
    path = os.path.join(dynamic_dir, "vps-monitor-teste-de-rota.yml")
    assert os.path.exists(path)
    with open(path) as f:
        assert f.read() == VALID_YAML

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        log = session.query(test_db.TraefikActionLog).first()
    assert log.acao == "create"
    assert log.sucesso == 1


def test_criar_rota_yaml_invalido(auth_client):
    r = auth_client.post("/api/traefik/routes", json={
        "nome_exibicao": "Rota Invalida",
        "yaml_content": INVALID_YAML,
    })
    assert r.status_code == 400

    dynamic_dir = os.environ["TRAEFIK_DYNAMIC_DIR"]
    assert not os.path.exists(os.path.join(dynamic_dir, "vps-monitor-rota-invalida.yml"))


def test_criar_rota_duplicada(auth_client):
    auth_client.post("/api/traefik/routes", json={"nome_exibicao": "Duplicada", "yaml_content": VALID_YAML})
    r = auth_client.post("/api/traefik/routes", json={"nome_exibicao": "Duplicada", "yaml_content": VALID_YAML})
    assert r.status_code == 409


def test_editar_rota_bloqueia_sem_prefixo(auth_client):
    r = auth_client.put("/api/traefik/routes/mecanicapro.yml", json={"yaml_content": VALID_YAML})
    assert r.status_code == 403


def test_editar_rota_nao_encontrada(auth_client):
    r = auth_client.put("/api/traefik/routes/vps-monitor-nao-existe.yml", json={"yaml_content": VALID_YAML})
    assert r.status_code == 404


def test_editar_rota_yaml_invalido_nao_altera_arquivo(auth_client):
    auth_client.post("/api/traefik/routes", json={"nome_exibicao": "Original", "yaml_content": VALID_YAML})
    r = auth_client.put("/api/traefik/routes/vps-monitor-original.yml", json={"yaml_content": INVALID_YAML})
    assert r.status_code == 400

    dynamic_dir = os.environ["TRAEFIK_DYNAMIC_DIR"]
    with open(os.path.join(dynamic_dir, "vps-monitor-original.yml")) as f:
        assert f.read() == VALID_YAML


def test_editar_rota_sucesso(auth_client, test_db):
    auth_client.post("/api/traefik/routes", json={"nome_exibicao": "Editar Mim", "yaml_content": VALID_YAML})
    novo_conteudo = VALID_YAML.replace("exemplo.dlsistemas.com.br", "novo.dlsistemas.com.br")
    r = auth_client.put("/api/traefik/routes/vps-monitor-editar-mim.yml", json={"yaml_content": novo_conteudo})
    assert r.status_code == 200

    dynamic_dir = os.environ["TRAEFIK_DYNAMIC_DIR"]
    with open(os.path.join(dynamic_dir, "vps-monitor-editar-mim.yml")) as f:
        assert f.read() == novo_conteudo

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        log = session.query(test_db.TraefikActionLog).filter_by(acao="edit").first()
    assert log is not None
    assert log.filename == "vps-monitor-editar-mim.yml"


def test_excluir_rota_bloqueia_sem_prefixo(auth_client):
    r = auth_client.delete("/api/traefik/routes/mecanicapro.yml")
    assert r.status_code == 403


def test_excluir_rota_nao_encontrada(auth_client):
    r = auth_client.delete("/api/traefik/routes/vps-monitor-nao-existe.yml")
    assert r.status_code == 404


def test_excluir_rota_sucesso(auth_client, test_db):
    auth_client.post("/api/traefik/routes", json={"nome_exibicao": "Excluir Mim", "yaml_content": VALID_YAML})
    r = auth_client.delete("/api/traefik/routes/vps-monitor-excluir-mim.yml")
    assert r.status_code == 200

    dynamic_dir = os.environ["TRAEFIK_DYNAMIC_DIR"]
    assert not os.path.exists(os.path.join(dynamic_dir, "vps-monitor-excluir-mim.yml"))

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        log = session.query(test_db.TraefikActionLog).filter_by(acao="delete").first()
    assert log is not None


def test_traefik_endpoints_sem_autenticacao_401():
    import main
    client = TestClient(main.app)
    assert client.get("/api/traefik/routes").status_code == 401
    assert client.post("/api/traefik/routes", json={}).status_code == 401
    assert client.put("/api/traefik/routes/vps-monitor-x.yml", json={}).status_code == 401
    assert client.delete("/api/traefik/routes/vps-monitor-x.yml").status_code == 401
```

- [ ] **Step 4: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_traefik_api.py -v`
Expected: FAIL em todos os testes — `ModuleNotFoundError: No module named 'api.traefik'` (a linha `import api.traefik as traefik_mod` dentro do fixture `auth_client` falha imediatamente).

- [ ] **Step 5: Implementar o módulo**

Criar `backend/api/traefik.py`:

```python
import os
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models.database as db_module
from api._slug import slugify
from api.auth import get_token_data, verify_token_header
from models.database import TraefikActionLog

TRAEFIK_DYNAMIC_DIR = os.environ.get("TRAEFIK_DYNAMIC_DIR", "/opt/traefik/dynamic")

router = APIRouter(prefix="/api/traefik", dependencies=[Depends(verify_token_header)])


def _log_action(username: str, filename: str, acao: str, sucesso: int = 1, erro: Optional[str] = None):
    with Session(db_module.engine) as session:
        session.add(TraefikActionLog(
            username=username, filename=filename, acao=acao,
            sucesso=sucesso, erro=erro,
        ))
        session.commit()


class RouteCreateIn(BaseModel):
    nome_exibicao: str
    yaml_content: str


class RouteUpdateIn(BaseModel):
    yaml_content: str


def _validar_yaml(yaml_content: str) -> Optional[str]:
    try:
        yaml.safe_load(yaml_content)
        return None
    except yaml.YAMLError as e:
        return str(e)


@router.get("/routes")
def list_routes():
    if not os.path.isdir(TRAEFIK_DYNAMIC_DIR):
        return []
    rotas = []
    for filename in sorted(os.listdir(TRAEFIK_DYNAMIC_DIR)):
        if not filename.endswith(".yml"):
            continue
        path = os.path.join(TRAEFIK_DYNAMIC_DIR, filename)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        rotas.append({
            "filename": filename,
            "managed": filename.startswith("vps-monitor-"),
            "content": content,
        })
    return rotas


@router.post("/routes", status_code=201)
def create_route(body: RouteCreateIn, token_data: dict = Depends(get_token_data)):
    username = token_data.get("sub", "desconhecido")
    filename = f"{slugify(body.nome_exibicao)}.yml"
    path = os.path.join(TRAEFIK_DYNAMIC_DIR, filename)

    if os.path.exists(path):
        raise HTTPException(status_code=409, detail=f"Já existe uma rota com o nome '{filename}'.")

    erro = _validar_yaml(body.yaml_content)
    if erro:
        _log_action(username, filename, "create", sucesso=0, erro=erro)
        raise HTTPException(status_code=400, detail=f"YAML inválido: {erro}")

    with open(path, "w", encoding="utf-8") as f:
        f.write(body.yaml_content)

    _log_action(username, filename, "create", sucesso=1)
    return {"filename": filename}


@router.put("/routes/{filename}")
def update_route(filename: str, body: RouteUpdateIn, token_data: dict = Depends(get_token_data)):
    if not filename.startswith("vps-monitor-"):
        raise HTTPException(status_code=403, detail="Só é possível editar rotas criadas pelo monitor.")
    username = token_data.get("sub", "desconhecido")

    path = os.path.join(TRAEFIK_DYNAMIC_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Rota não encontrada.")

    erro = _validar_yaml(body.yaml_content)
    if erro:
        _log_action(username, filename, "edit", sucesso=0, erro=erro)
        raise HTTPException(status_code=400, detail=f"YAML inválido: {erro}")

    with open(path, "w", encoding="utf-8") as f:
        f.write(body.yaml_content)

    _log_action(username, filename, "edit", sucesso=1)
    return {"ok": True}


@router.delete("/routes/{filename}")
def delete_route(filename: str, token_data: dict = Depends(get_token_data)):
    if not filename.startswith("vps-monitor-"):
        raise HTTPException(status_code=403, detail="Só é possível excluir rotas criadas pelo monitor.")
    username = token_data.get("sub", "desconhecido")

    path = os.path.join(TRAEFIK_DYNAMIC_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Rota não encontrada.")

    os.remove(path)
    _log_action(username, filename, "delete", sucesso=1)
    return {"ok": True}
```

- [ ] **Step 6: Registrar o router temporariamente pra rodar os testes desta task**

Esta task depende do router estar registrado em `main.py` (o fixture `auth_client` faz `import main`). Em `backend/main.py`, adicionar o import:

```python
from api.traefik import router as traefik_router
```

E adicionar, junto aos outros `app.include_router`:

```python
app.include_router(traefik_router)
```

- [ ] **Step 7: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_traefik_api.py -v`
Expected: PASS (12 testes)

- [ ] **Step 8: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: todos os testes passando (nenhum `FAILED`), incluindo os das Tasks 1 e 2 e os já existentes.

- [ ] **Step 9: Commit**

```bash
git add backend/api/traefik.py backend/tests/test_traefik_api.py backend/main.py backend/requirements.txt
git commit -m "feat: adiciona CRUD de rotas dinamicas do Traefik via API"
```

---

### Task 4: Docker — mount de `/opt/traefik/dynamic` como `rw`

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:** nenhuma (mudança de infraestrutura, sem código Python).

- [ ] **Step 1: Alterar o mount no `docker-compose.yml`**

No serviço `monitor-backend`, trocar:

```yaml
      - /opt/traefik/dynamic:/opt/traefik/dynamic:ro
```

por:

```yaml
      - /opt/traefik/dynamic:/opt/traefik/dynamic:rw
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: monta /opt/traefik/dynamic como rw no monitor-backend"
```

---

### Task 5: Script no host — `scripts/traefik-dynamic-commit-watcher.sh`

**Files:**
- Create: `scripts/traefik-dynamic-commit-watcher.sh`

**Interfaces:** nenhuma (shell script, roda via cron no host, fora de qualquer container).

- [ ] **Step 1: Criar o script**

Criar `scripts/traefik-dynamic-commit-watcher.sh`:

```bash
#!/bin/bash
# Roda no HOST via cron (nao dentro de um container).
#
# monitor-backend so escreve/apaga arquivos em /opt/traefik/dynamic (mount
# rw). Nao roda "git commit" de dentro do container porque isso exigiria
# montar tambem /opt/traefik/.git e a arvore de trabalho inteira, incluindo
# /opt/traefik/certs/acme.json (chave privada, permissao 600) — exposicao
# desnecessaria. Este script detecta mudancas em dynamic/ e comita a partir
# do host, onde o repo ja esta presente por inteiro.
#
# Instalacao (uma vez, fora deste repo):
#   crontab -e
#   * * * * * /opt/vps-monitor/monitor/scripts/traefik-dynamic-commit-watcher.sh >> /var/log/traefik-dynamic-commit-watcher.log 2>&1
set -euo pipefail

DYNAMIC_DIR="/opt/traefik/dynamic"
STATE_FILE="/opt/vps-monitor/.traefik-dynamic-state"

current_state=$(ls -la "$DYNAMIC_DIR" 2>/dev/null || true)

if [ ! -f "$STATE_FILE" ]; then
  echo "$current_state" > "$STATE_FILE"
  exit 0
fi

previous_state=$(cat "$STATE_FILE")

if [ "$current_state" != "$previous_state" ]; then
  git -C /opt/traefik add dynamic/
  if ! git -C /opt/traefik diff --cached --quiet; then
    git -C /opt/traefik commit -m "auto: alteracao via monitor UI ($(date -Iseconds))"
    echo "$(date -Iseconds) commit criado (mudanca detectada em dynamic/)"
  fi
  echo "$current_state" > "$STATE_FILE"
fi
```

- [ ] **Step 2: Dar permissão de execução**

Run: `chmod +x scripts/traefik-dynamic-commit-watcher.sh`

- [ ] **Step 3: Commit**

```bash
git add scripts/traefik-dynamic-commit-watcher.sh
git commit -m "feat: adiciona watcher no host pra auto-commit de rotas dinamicas do Traefik"
```

(A instalação do cron job em si — `crontab -e` na VPS — acontece na Task 7, deploy, já que exige acesso direto ao host.)

---

### Task 6: Frontend — página `/traefik`

**Files:**
- Create: `frontend/app/traefik/page.tsx`
- Modify: `frontend/app/layout.tsx`

**Interfaces:**
- Consumes: `GET/POST/PUT/DELETE /api/traefik/routes` (Task 3).

- [ ] **Step 1: Criar a página**

Criar `frontend/app/traefik/page.tsx`:

```tsx
'use client';
import { useState, useEffect, useCallback } from 'react';
import api from '../../lib/api';
import Toast from '../../components/Toast';

interface Route {
  filename: string;
  managed: boolean;
  content: string;
}

const TEMPLATE = `# Nome do router e do service podem ser iguais ao nome do projeto.
http:
  routers:
    meu-projeto:
      rule: "Host(\`meuprojeto.dlsistemas.com.br\`)"
      entryPoints:
        - websecure
      tls:
        certResolver: letsencrypt
      service: meu-projeto
  services:
    meu-projeto:
      loadBalancer:
        servers:
          - url: "http://172.17.0.1:8080"
`;

const card: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8,
};

const textarea: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '8px 10px', color: 'var(--text)',
  fontSize: 13, width: '100%', boxSizing: 'border-box',
  fontFamily: 'monospace', minHeight: 220, resize: 'vertical',
};

const input: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
};

export default function TraefikPage() {
  const [routes, setRoutes] = useState<Route[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [nomeExibicao, setNomeExibicao] = useState('');
  const [yamlContent, setYamlContent] = useState(TEMPLATE);
  const [editFilename, setEditFilename] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState('');
  const [deleteAlvo, setDeleteAlvo] = useState<string | null>(null);

  const loadRoutes = useCallback(async () => {
    setLoading(true);
    try { setRoutes((await api.get('/traefik/routes')).data); } catch { setRoutes([]); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadRoutes(); }, [loadRoutes]);

  function startCreate() {
    setEditFilename(null);
    setNomeExibicao('');
    setYamlContent(TEMPLATE);
    setFormError('');
    setShowForm(true);
  }

  function startEdit(route: Route) {
    setEditFilename(route.filename);
    setYamlContent(route.content);
    setFormError('');
    setShowForm(true);
  }

  async function saveRoute() {
    setFormError('');
    try {
      if (editFilename) {
        await api.put(`/traefik/routes/${editFilename}`, { yaml_content: yamlContent });
        setToast({ msg: 'Rota atualizada — Traefik recarrega em segundos', type: 'success' });
      } else {
        await api.post('/traefik/routes', { nome_exibicao: nomeExibicao, yaml_content: yamlContent });
        setToast({ msg: 'Rota criada — Traefik recarrega em segundos', type: 'success' });
      }
      setShowForm(false);
      loadRoutes();
    } catch (e: any) {
      setFormError(e?.response?.data?.detail || 'Erro ao salvar rota');
    }
  }

  async function confirmDelete() {
    if (!deleteAlvo) return;
    try {
      await api.delete(`/traefik/routes/${deleteAlvo}`);
      setToast({ msg: 'Rota excluída', type: 'success' });
      loadRoutes();
    } catch { setToast({ msg: 'Erro ao excluir rota', type: 'error' }); }
    setDeleteAlvo(null);
  }

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      {toast && <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />}
      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Traefik</h1>

      <div style={{ marginBottom: 16 }}>
        <button
          onClick={startCreate}
          style={{ padding: '8px 16px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
        >
          + Nova Rota
        </button>
      </div>

      {loading && routes.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Carregando...</p>
      )}

      {routes.map((route) => (
        <div key={route.filename} style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8, flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--text)', fontWeight: 600, fontFamily: 'monospace' }}>{route.filename}</span>
            <span style={{
              padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600,
              background: route.managed ? 'var(--accent)' : 'var(--surface)',
              color: route.managed ? '#000' : 'var(--muted)',
              border: '1px solid var(--border)',
            }}>
              {route.managed ? 'Gerenciado' : 'Manual'}
            </span>
            {route.managed && (
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                <button
                  onClick={() => startEdit(route)}
                  style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text)', cursor: 'pointer', fontSize: 12 }}
                >
                  Editar
                </button>
                <button
                  onClick={() => setDeleteAlvo(route.filename)}
                  style={{ padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 6, color: 'var(--danger)', cursor: 'pointer', fontSize: 12 }}
                >
                  Excluir
                </button>
              </div>
            )}
          </div>
          <pre style={{
            background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6,
            padding: 10, fontSize: 12, color: 'var(--muted)', overflowX: 'auto', margin: 0,
          }}>
            {route.content}
          </pre>
        </div>
      ))}

      {/* Modal de criar/editar */}
      {showForm && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setShowForm(false)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, width: '90%', maxWidth: 640, padding: 24 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 16, color: 'var(--text)' }}>{editFilename ? 'Editar Rota' : 'Nova Rota'}</h3>

            <div style={{ display: 'grid', gap: 12, marginBottom: 16 }}>
              {!editFilename && (
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Nome de exibição</label>
                  <input style={input} value={nomeExibicao} onChange={(e) => setNomeExibicao(e.target.value)} />
                </div>
              )}
              <div>
                <label style={{ color: 'var(--muted)', fontSize: 12 }}>YAML (config do Traefik file provider)</label>
                <textarea style={textarea} value={yamlContent} onChange={(e) => setYamlContent(e.target.value)} />
              </div>
            </div>

            {formError && (
              <p style={{ color: 'var(--danger)', fontSize: 12, marginBottom: 12, whiteSpace: 'pre-wrap' }}>{formError}</p>
            )}

            <div style={{ display: 'flex', gap: 10 }}>
              <button
                onClick={saveRoute}
                style={{ padding: '8px 20px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
              >
                Salvar
              </button>
              <button
                onClick={() => setShowForm(false)}
                style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
              >
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal de confirmação de exclusão */}
      {deleteAlvo && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setDeleteAlvo(null)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, maxWidth: 420 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>Excluir rota</h3>
            <p style={{ color: 'var(--muted)', marginBottom: 20, fontSize: 14 }}>
              Tem certeza que deseja excluir a rota &quot;{deleteAlvo}&quot;? Essa ação não pode ser desfeita.
            </p>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setDeleteAlvo(null)}
                style={{ padding: '8px 16px', background: 'var(--surface)', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: 6, cursor: 'pointer' }}
              >
                Cancelar
              </button>
              <button
                onClick={confirmDelete}
                style={{ padding: '8px 20px', background: 'var(--danger)', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
              >
                Confirmar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Adicionar o link no menu**

Em `frontend/app/layout.tsx`, no array `NAV`, adicionar uma entrada logo depois de `/seguranca`:

```tsx
  { href: '/seguranca', label: 'Segurança', icon: '🛡️' },
  { href: '/traefik', label: 'Traefik', icon: '🔀' },
  { href: '/configuracoes', label: 'Configurações', icon: '⚙️' },
```

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build limpo, sem erros de tipo, rota `/traefik` aparece na lista de rotas geradas.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/traefik/page.tsx frontend/app/layout.tsx
git commit -m "feat: adiciona pagina de gestao de rotas do Traefik (/traefik)"
```

(Verificação manual no navegador fica por conta do usuário, combinado nesta sessão.)

---

### Task 7: Deploy para produção

**Files:** nenhum (ação operacional, sem mudança de código)

**Atenção:** esta task só deve ser executada após confirmação explícita do usuário — dá ao container `monitor-backend` acesso de escrita a `/opt/traefik/dynamic` (rotas de produção afetando tráfego real de outros projetos, ex: mecanicapro), e instala um cron job novo direto na VPS.

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

- [ ] **Step 4: Instalar o cron job do watcher**

```bash
ssh root@144.91.92.70 "(crontab -l 2>/dev/null; echo '* * * * * /opt/vps-monitor/monitor/scripts/traefik-dynamic-commit-watcher.sh >> /var/log/traefik-dynamic-commit-watcher.log 2>&1') | crontab -"
```

Confirmar que entrou:

```bash
ssh root@144.91.92.70 "crontab -l | grep traefik-dynamic"
```

Expected: a linha aparece na listagem.

- [ ] **Step 5: Teste manual end-to-end**

Criar uma rota de teste bem inofensiva pela UI (`/traefik`), ex: `Host(\`teste-monitor.dlsistemas.com.br\`)` apontando pra um serviço qualquer. Confirmar:
1. O arquivo aparece em `/opt/traefik/dynamic/vps-monitor-*.yml` na VPS.
2. Em até 1 minuto, `git -C /opt/traefik log --oneline -1` mostra um commit automático.
3. Editar a rota pela UI, confirmar que o arquivo e o próximo commit refletem a mudança.
4. Excluir a rota pela UI, confirmar que o arquivo some e o commit seguinte reflete a remoção.
5. Confirmar que `mecanicapro.yml` não aparece com botões de editar/excluir na UI, e que uma tentativa direta via API (`PUT`/`DELETE /api/traefik/routes/mecanicapro.yml`) retorna 403.
