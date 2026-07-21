# Gestão de Regras de Firewall (UFW) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Interface no monitor pra ver/criar/remover regras do UFW (porta, protocolo, permitir/negar, origem IP/CIDR opcional), com as portas 22/80/443 travadas no código sem exceção, pra nunca a UI conseguir se auto-bloquear do SSH ou do próprio monitor.

**Architecture:** Mesmo padrão já usado 3x nesta base (fail2ban, Traefik, backup/restore): o container `monitor-backend` nunca roda `ufw` — só grava pedidos (`FirewallRuleRequest`) no seu próprio SQLite e lê um snapshot JSON somente-leitura. Um script novo no host, `scripts/firewall-worker.sh` (cron, 1x/min), aplica os pedidos de verdade via `ufw allow/deny`/`ufw delete allow/deny` e regenera o snapshot a partir de `ufw status numbered` (parseado com `python3`, mais confiável que bash/awk pra gerar JSON).

**Tech Stack:** FastAPI + SQLAlchemy + pytest (backend, TDD), Next.js/React/TypeScript (frontend, sem suíte de testes — build limpo), bash + `ufw`/`python3` (script no host, sem teste automatizado).

## Global Constraints

- **Portas `{22, 80, 443}` nunca podem ser alteradas pela UI, sem exceção** — nem `POST /api/firewall/rules` com `acao: "add"` nem `acao: "remove"` envolvendo essas portas passa da primeira validação (400 imediato). Checagem repetida no worker (defesa em profundidade), não só na API.
- Remoção de regra é sempre **por especificação** (`porta`, `protocolo`, `permitir`, `origem_ip`), nunca por número de posição do `ufw status numbered` (esses números mudam a cada alteração).
- O container `monitor-backend` nunca roda `ufw` — todo comando real acontece no `scripts/firewall-worker.sh`, no host.
- `FIREWALL_STATE_FILE` (env var, default `/opt/vps-monitor-firewall-state.json`) é montado **read-only** no `monitor-backend` — só o worker escreve ali.
- Um pedido novo (`add` ou `remove`) com a mesma combinação `(acao, permitir, porta, protocolo, origem_ip)` de um pedido já `pending`/`running` retorna 409.

---

### Task 1: Modelo `FirewallRuleRequest`

**Files:**
- Modify: `backend/models/database.py`
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Produces: `class FirewallRuleRequest(Base)` com colunas `id, acao, permitir, porta, protocolo, origem_ip, status, criado_em, concluido_em, erro, username`. Consumido pela Task 2.

- [ ] **Step 1: Escrever o teste (deve falhar)**

Adicionar ao final de `backend/tests/test_database.py`:

```python
def test_insert_firewall_rule_request(test_db):
    from datetime import datetime
    with Session(test_db.engine) as session:
        req = test_db.FirewallRuleRequest(
            acao="add", permitir=1, porta=8081, protocolo="tcp",
            origem_ip=None, status="pending", criado_em=datetime.utcnow(),
            username="admin",
        )
        session.add(req)
        session.commit()
        fetched = session.query(test_db.FirewallRuleRequest).first()
    assert fetched.acao == "add"
    assert fetched.porta == 8081
    assert fetched.status == "pending"
    assert fetched.origem_ip is None
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_database.py -v -k firewall_rule_request`
Expected: FAIL — `AttributeError: module 'models.database' has no attribute 'FirewallRuleRequest'`

- [ ] **Step 3: Implementar o modelo**

Em `backend/models/database.py`, logo depois da classe `BackupJob`:

```python
class FirewallRuleRequest(Base):
    __tablename__ = "firewall_rule_request"
    id = Column(Integer, primary_key=True, autoincrement=True)
    acao = Column(String, nullable=False)
    permitir = Column(Integer, nullable=False)
    porta = Column(Integer, nullable=False)
    protocolo = Column(String, nullable=False)
    origem_ip = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)
    concluido_em = Column(DateTime, nullable=True)
    erro = Column(Text, nullable=True)
    username = Column(String, nullable=False)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_database.py -v -k firewall_rule_request`
Expected: PASS

- [ ] **Step 5: Rodar toda a suíte de `test_database.py`**

Run: `cd backend && py -m pytest tests/test_database.py -v`
Expected: todos os testes existentes continuam passando.

- [ ] **Step 6: Commit**

```bash
git add backend/models/database.py backend/tests/test_database.py
git commit -m "feat: adiciona modelo FirewallRuleRequest"
```

---

### Task 2: `api/firewall.py` — fila de pedidos + leitura do snapshot

**Files:**
- Create: `backend/api/firewall.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_firewall_api.py`

**Interfaces:**
- Consumes: `FirewallRuleRequest` (Task 1).
- Produces: `router = APIRouter(prefix="/api/firewall", ...)` com `GET /rules`, `POST /rules`. Consumido pela Task 5 (frontend).
- Usa a env var `FIREWALL_STATE_FILE` (default `/opt/vps-monitor-firewall-state.json`).

- [ ] **Step 1: Escrever os testes (devem falhar)**

Criar `backend/tests/test_firewall_api.py`:

```python
import json
import os
import pytest
import importlib
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _garante_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")


@pytest.fixture
def auth_client(test_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_USER", "admin")
    monkeypatch.setenv("MONITOR_PASSWORD", "test123")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-ok-yes")
    monkeypatch.setenv("FIREWALL_STATE_FILE", str(tmp_path / "firewall-state.json"))

    import limiter as limiter_mod
    importlib.reload(limiter_mod)
    import api.auth as auth_mod
    importlib.reload(auth_mod)
    import api.firewall as firewall_mod
    importlib.reload(firewall_mod)
    import main
    importlib.reload(main)

    client = TestClient(main.app)
    token = client.post("/api/auth/login", data={"username": "admin", "password": "test123"}).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _escrever_estado(tmp_path, regras):
    caminho = tmp_path / "firewall-state.json"
    caminho.write_text(json.dumps({"regras": regras}), encoding="utf-8")


def test_listar_regras_sem_arquivo_de_estado(auth_client):
    r = auth_client.get("/api/firewall/rules")
    assert r.status_code == 200
    assert r.json()["regras"] == []
    assert r.json()["jobs_pendentes"] == []


def test_listar_regras_le_snapshot(auth_client, tmp_path):
    _escrever_estado(tmp_path, [
        {"porta": 22, "protocolo": "tcp", "permitir": True, "origem_ip": None, "protegida": True},
        {"porta": 8081, "protocolo": "tcp", "permitir": True, "origem_ip": None, "protegida": False},
    ])
    r = auth_client.get("/api/firewall/rules")
    assert r.status_code == 200
    regras = r.json()["regras"]
    assert len(regras) == 2
    assert regras[0]["protegida"] is True
    assert regras[1]["protegida"] is False


def test_criar_regra_add_sucesso(auth_client, test_db):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": True, "porta": 8081, "protocolo": "tcp", "origem_ip": None,
    })
    assert r.status_code == 202
    request_id = r.json()["request_id"]

    from sqlalchemy.orm import Session
    with Session(test_db.engine) as session:
        req = session.get(test_db.FirewallRuleRequest, request_id)
    assert req.acao == "add"
    assert req.porta == 8081
    assert req.status == "pending"


def test_criar_regra_bloqueia_porta_22_add(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": True, "porta": 22, "protocolo": "tcp", "origem_ip": None,
    })
    assert r.status_code == 400


def test_criar_regra_bloqueia_porta_80_remove(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "remove", "permitir": True, "porta": 80, "protocolo": "tcp", "origem_ip": None,
    })
    assert r.status_code == 400


def test_criar_regra_bloqueia_porta_443(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": False, "porta": 443, "protocolo": "tcp", "origem_ip": None,
    })
    assert r.status_code == 400


def test_criar_regra_protocolo_invalido(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": True, "porta": 8081, "protocolo": "icmp", "origem_ip": None,
    })
    assert r.status_code == 400


def test_criar_regra_porta_fora_do_range(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": True, "porta": 70000, "protocolo": "tcp", "origem_ip": None,
    })
    assert r.status_code == 400


def test_criar_regra_origem_ip_invalida(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": True, "porta": 8081, "protocolo": "tcp", "origem_ip": "999.999.999.999",
    })
    assert r.status_code == 400


def test_criar_regra_origem_cidr_valido_aceito(auth_client, test_db):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "add", "permitir": True, "porta": 8081, "protocolo": "tcp", "origem_ip": "203.0.113.0/24",
    })
    assert r.status_code == 202


def test_criar_regra_409_se_pedido_identico_pendente(auth_client):
    body = {"acao": "add", "permitir": True, "porta": 8081, "protocolo": "tcp", "origem_ip": None}
    auth_client.post("/api/firewall/rules", json=body)
    r = auth_client.post("/api/firewall/rules", json=body)
    assert r.status_code == 409


def test_criar_regra_acao_invalida(auth_client):
    r = auth_client.post("/api/firewall/rules", json={
        "acao": "modificar", "permitir": True, "porta": 8081, "protocolo": "tcp", "origem_ip": None,
    })
    assert r.status_code == 400


def test_firewall_endpoints_sem_autenticacao_401():
    import main
    client = TestClient(main.app)
    assert client.get("/api/firewall/rules").status_code == 401
    assert client.post("/api/firewall/rules", json={}).status_code == 401
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd backend && py -m pytest tests/test_firewall_api.py -v`
Expected: FAIL em todos os testes — `ModuleNotFoundError: No module named 'api.firewall'`.

- [ ] **Step 3: Implementar o módulo**

Criar `backend/api/firewall.py`:

```python
import ipaddress
import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models.database as db_module
from api.auth import get_token_data, verify_token_header
from models.database import FirewallRuleRequest

FIREWALL_STATE_FILE = os.environ.get("FIREWALL_STATE_FILE", "/opt/vps-monitor-firewall-state.json")
PORTAS_PROTEGIDAS = {22, 80, 443}
_PROTOCOLOS_VALIDOS = {"tcp", "udp"}
_ACOES_VALIDAS = {"add", "remove"}
_STATUS_ATIVOS = ["pending", "running"]

router = APIRouter(prefix="/api/firewall", dependencies=[Depends(verify_token_header)])


class RuleIn(BaseModel):
    acao: str
    permitir: bool
    porta: int
    protocolo: str
    origem_ip: Optional[str] = None


def _validar_regra(body: RuleIn) -> None:
    if body.porta in PORTAS_PROTEGIDAS:
        raise HTTPException(status_code=400, detail=f"Porta {body.porta} é protegida e não pode ser alterada pela UI.")
    if body.acao not in _ACOES_VALIDAS:
        raise HTTPException(status_code=400, detail=f"Ação inválida: '{body.acao}'.")
    if body.protocolo not in _PROTOCOLOS_VALIDOS:
        raise HTTPException(status_code=400, detail=f"Protocolo inválido: '{body.protocolo}'.")
    if not (1 <= body.porta <= 65535):
        raise HTTPException(status_code=400, detail="Porta deve estar entre 1 e 65535.")
    if body.origem_ip:
        try:
            ipaddress.ip_network(body.origem_ip, strict=False)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Origem IP/CIDR inválido: '{body.origem_ip}'.")


def _job_pendente_existe(session: Session, body: RuleIn) -> bool:
    return session.query(FirewallRuleRequest).filter(
        FirewallRuleRequest.status.in_(_STATUS_ATIVOS),
        FirewallRuleRequest.acao == body.acao,
        FirewallRuleRequest.permitir == int(body.permitir),
        FirewallRuleRequest.porta == body.porta,
        FirewallRuleRequest.protocolo == body.protocolo,
        FirewallRuleRequest.origem_ip == body.origem_ip,
    ).count() > 0


def _ler_estado() -> list[dict]:
    if not os.path.isfile(FIREWALL_STATE_FILE):
        return []
    with open(FIREWALL_STATE_FILE, encoding="utf-8") as f:
        estado = json.load(f)
    return estado.get("regras", [])


@router.get("/rules")
def list_rules():
    regras = _ler_estado()
    with Session(db_module.engine) as session:
        pendentes = session.query(FirewallRuleRequest).filter(
            FirewallRuleRequest.status.in_(_STATUS_ATIVOS)
        ).all()
        jobs = [
            {
                "id": j.id, "acao": j.acao, "permitir": bool(j.permitir),
                "porta": j.porta, "protocolo": j.protocolo, "origem_ip": j.origem_ip,
                "status": j.status,
            }
            for j in pendentes
        ]
    return {"regras": regras, "jobs_pendentes": jobs}


@router.post("/rules", status_code=202)
def create_rule(body: RuleIn, token_data: dict = Depends(get_token_data)):
    _validar_regra(body)
    username = token_data.get("sub", "desconhecido")

    with Session(db_module.engine) as session:
        if _job_pendente_existe(session, body):
            raise HTTPException(status_code=409, detail="Já existe um pedido idêntico em andamento.")
        job = FirewallRuleRequest(
            acao=body.acao, permitir=int(body.permitir), porta=body.porta,
            protocolo=body.protocolo, origem_ip=body.origem_ip, status="pending",
            username=username,
        )
        session.add(job)
        session.commit()
        return {"request_id": job.id}
```

- [ ] **Step 4: Registrar o router temporariamente pra rodar os testes desta task**

Em `backend/main.py`, adicionar o import:

```python
from api.firewall import router as firewall_router
```

E adicionar, junto aos outros `app.include_router`:

```python
app.include_router(firewall_router)
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `cd backend && py -m pytest tests/test_firewall_api.py -v`
Expected: PASS (13 testes)

- [ ] **Step 6: Rodar a suíte completa do backend**

Run: `cd backend && py -m pytest -q`
Expected: todos os testes passando, sem `FAILED`.

- [ ] **Step 7: Commit**

```bash
git add backend/api/firewall.py backend/tests/test_firewall_api.py backend/main.py
git commit -m "feat: adiciona fila de pedidos e leitura de regras de firewall via API"
```

---

### Task 3: Docker — mount read-only de `FIREWALL_STATE_FILE`

**Files:**
- Modify: `docker-compose.yml`

**Interfaces:** nenhuma (mudança de infraestrutura, sem código Python).

- [ ] **Step 1: Adicionar o mount no `docker-compose.yml`**

No serviço `monitor-backend`, dentro do bloco `volumes:`, adicionar (mantendo os mounts existentes):

```yaml
      - /opt/vps-monitor-firewall-state.json:/opt/vps-monitor-firewall-state.json:ro
```

(Sem adicionar `FIREWALL_STATE_FILE=` em `environment:` — o default do código já bate com esse path, mesmo padrão já usado com `TRAEFIK_DYNAMIC_DIR`/`BACKUPS_DIR`.)

**Atenção pro implementador:** montar um bind mount de **arquivo único** (não diretório) exige que o arquivo já exista no host **antes** do primeiro `docker compose up`, senão o Docker cria um **diretório** com esse nome no lugar do arquivo (comportamento padrão de bind mount pra um path inexistente). Isso é tratado explicitamente na Task 6 (deploy) — o worker roda uma vez manualmente pra criar o arquivo real antes do primeiro `docker compose up`/restart que aplica este mount.

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: monta FIREWALL_STATE_FILE read-only no monitor-backend"
```

---

### Task 4: Script no host — `scripts/firewall-worker.sh`

**Files:**
- Create: `scripts/firewall-worker.sh`

**Interfaces:** nenhuma (shell script, roda via cron no host, fora de qualquer container).

- [ ] **Step 1: Criar o script**

Criar `scripts/firewall-worker.sh`:

```bash
#!/bin/bash
# Roda no HOST via cron (nao dentro de um container).
#
# monitor-backend so grava "intencoes" (linhas em firewall_rule_request, no
# SQLite do proprio monitor) — nunca roda `ufw` diretamente, o que mexeria
# no firewall do kernel do HOST, nao do container. Este script aplica as
# regras de verdade a partir do host, e regenera um snapshot JSON do estado
# atual em FIREWALL_STATE_FILE pra API ler sem nunca precisar rodar `ufw`
# ela mesma (o mount desse arquivo no container e read-only).
#
# Portas protegidas (22/80/443) sao checadas de novo aqui (defesa em
# profundidade) mesmo a API ja validando antes de gravar o pedido.
#
# Nao usa "set -e": precisa continuar apos falha pra marcar o job como
# failed, tratamento de erro explicito em cada etapa.
#
# Pre-requisito (uma vez, fora deste repo): apt-get install -y python3
# (normalmente ja vem instalado; usado so pra gerar o JSON do snapshot,
# mais confiavel que parsing em bash/awk puro).
#
# Instalacao do cron (uma vez, fora deste repo):
#   crontab -e
#   * * * * * /opt/vps-monitor/monitor/scripts/firewall-worker.sh >> /var/log/firewall-worker.log 2>&1
set -uo pipefail

DB_PATH="/var/lib/docker/volumes/vps-monitor_vps_monitor_data/_data/monitor.db"
FIREWALL_STATE_FILE="/opt/vps-monitor-firewall-state.json"
PORTAS_PROTEGIDAS="22 80 443"
LOCK_FILE="/var/lock/firewall-worker.lock"

# Impede que duas execucoes do cron rodem ao mesmo tempo mexendo no
# firewall simultaneamente — mesmo padrao ja usado no backup-worker.sh.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date -Iseconds) outra execucao do firewall-worker.sh ja esta em andamento, saindo." >&2
  exit 0
fi

sqlite3_exec() {
  # ".timeout" (dot-command) nao emite linha de saida, diferente de
  # "PRAGMA busy_timeout=...", que contaminaria a captura via $(...) — erro
  # ja cometido e corrigido no backup-worker.sh, nao repetir aqui.
  sqlite3 -cmd ".timeout 5000" "$DB_PATH" "$1"
}

porta_protegida() {
  local porta="$1"
  for p in $PORTAS_PROTEGIDAS; do
    [ "$p" = "$porta" ] && return 0
  done
  return 1
}

aplicar_regra() {
  local acao="$1" permitir="$2" porta="$3" protocolo="$4" origem_ip="$5"

  if porta_protegida "$porta"; then
    echo "Recusado: porta $porta e protegida (22/80/443), nunca aplicada via worker." >&2
    return 1
  fi

  local verbo="allow"
  [ "$permitir" = "0" ] && verbo="deny"

  local comando=(ufw)
  [ "$acao" = "remove" ] && comando+=(delete)
  comando+=("$verbo")
  if [ -n "$origem_ip" ] && [ "$origem_ip" != "None" ]; then
    comando+=(from "$origem_ip")
  fi
  comando+=(to any port "$porta" proto "$protocolo")

  "${comando[@]}"
}

gerar_snapshot() {
  ufw status numbered | python3 -c '
import json, re, sys

PORTAS_PROTEGIDAS = {22, 80, 443}
padrao = re.compile(r"^\[\s*\d+\]\s+(\S+)\s+(ALLOW|DENY)\s+IN\s+(.+?)\s*$")
regras = []

for linha in sys.stdin:
    if "(v6)" in linha:
        # Uma regra sem origem especifica (ufw allow to any port X) e
        # espelhada automaticamente pro IPv6 pelo proprio ufw — um unico
        # "ufw allow/delete" ja cria/remove os dois de uma vez, entao a
        # entrada (v6) e sempre redundante com a nao-v6 correspondente
        # nesta ferramenta (nunca criamos uma regra so-IPv6 pela UI).
        continue
    m = padrao.match(linha)
    if not m:
        continue
    porta_proto, acao, origem = m.groups()
    if "/" not in porta_proto:
        continue
    porta_str, protocolo = porta_proto.split("/", 1)
    try:
        porta = int(porta_str)
    except ValueError:
        continue
    origem_ip = None if origem == "Anywhere" else origem
    regras.append({
        "porta": porta,
        "protocolo": protocolo,
        "permitir": acao == "ALLOW",
        "origem_ip": origem_ip,
        "protegida": porta in PORTAS_PROTEGIDAS,
    })

print(json.dumps({"regras": regras}))
' > "${FIREWALL_STATE_FILE}.tmp" && mv "${FIREWALL_STATE_FILE}.tmp" "$FIREWALL_STATE_FILE"
}

# ---------- 0. Libera jobs presos (worker interrompido no meio de uma execucao) ----------
# Aplicar uma regra de firewall e quase instantaneo (bem mais rapido que um
# snapshot de backup), entao 1h ja e um limite bem generoso pra detectar um
# job realmente travado.
sqlite3_exec "UPDATE firewall_rule_request SET status='failed', concluido_em=datetime('now'), erro='Job travado em running por mais de 1h - worker provavelmente interrompido.' WHERE status='running' AND criado_em < datetime('now', '-1 hours');"

# ---------- 1. Processa no maximo um pedido pendente por execucao ----------
job_linha=$(sqlite3_exec "SELECT id, acao, permitir, porta, protocolo, IFNULL(origem_ip, '') FROM firewall_rule_request WHERE status='pending' ORDER BY criado_em LIMIT 1;")

if [ -n "$job_linha" ]; then
  IFS='|' read -r job_id job_acao job_permitir job_porta job_protocolo job_origem <<< "$job_linha"

  sqlite3_exec "UPDATE firewall_rule_request SET status='running' WHERE id=$job_id;"

  if saida=$(aplicar_regra "$job_acao" "$job_permitir" "$job_porta" "$job_protocolo" "$job_origem" 2>&1); then
    sqlite3_exec "UPDATE firewall_rule_request SET status='done', concluido_em=datetime('now') WHERE id=$job_id;"
    echo "$saida"
  else
    erro_escapado=$(echo "$saida" | sed "s/'/''/g" | tr '\n' ' ')
    sqlite3_exec "UPDATE firewall_rule_request SET status='failed', concluido_em=datetime('now'), erro='$erro_escapado' WHERE id=$job_id;"
    echo "$saida" >&2
  fi
fi

# ---------- 2. Regenera o snapshot do estado atual ----------
gerar_snapshot
```

- [ ] **Step 2: Checar sintaxe do script**

Run: `bash -n scripts/firewall-worker.sh`
Expected: sem saída (sintaxe válida).

- [ ] **Step 3: Dar permissão de execução**

Run: `chmod +x scripts/firewall-worker.sh`

- [ ] **Step 4: Commit**

```bash
git add scripts/firewall-worker.sh
git commit -m "feat: adiciona worker no host pra aplicar regras de firewall (UFW)"
```

(A instalação do cron e a criação inicial do arquivo de estado acontecem na Task 6, deploy, já que exigem acesso direto ao host.)

---

### Task 5: Frontend — página `/firewall`

**Files:**
- Create: `frontend/app/firewall/page.tsx`
- Modify: `frontend/app/layout.tsx`

**Interfaces:**
- Consumes: `GET/POST /api/firewall/rules` (Task 2).

- [ ] **Step 1: Criar a página**

Criar `frontend/app/firewall/page.tsx`:

```tsx
'use client';
import { useState, useEffect, useCallback } from 'react';
import api from '../../lib/api';
import Toast from '../../components/Toast';

interface Regra {
  porta: number;
  protocolo: string;
  permitir: boolean;
  origem_ip: string | null;
  protegida: boolean;
}

interface JobPendente {
  id: number;
  acao: string;
  permitir: boolean;
  porta: number;
  protocolo: string;
  origem_ip: string | null;
  status: string;
}

const PORTAS_PROTEGIDAS = [22, 80, 443];

const card: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)',
  borderRadius: 8, padding: 16, marginBottom: 8,
};

const input: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)',
  fontSize: 14, width: '100%', boxSizing: 'border-box',
};

const selectStyle: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
  borderRadius: 6, padding: '6px 10px', color: 'var(--text)', fontSize: 14, width: '100%',
};

export default function FirewallPage() {
  const [regras, setRegras] = useState<Regra[]>([]);
  const [jobsPendentes, setJobsPendentes] = useState<JobPendente[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [porta, setPorta] = useState('');
  const [protocolo, setProtocolo] = useState('tcp');
  const [permitir, setPermitir] = useState('allow');
  const [origemIp, setOrigemIp] = useState('');
  const [formError, setFormError] = useState('');
  const [deleteAlvo, setDeleteAlvo] = useState<Regra | null>(null);

  const loadRules = useCallback(async () => {
    try {
      const r = await api.get('/firewall/rules');
      setRegras(r.data.regras);
      setJobsPendentes(r.data.jobs_pendentes);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { setLoading(true); loadRules(); }, [loadRules]);

  useEffect(() => {
    const temPendente = jobsPendentes.length > 0;
    const id = setInterval(loadRules, temPendente ? 5000 : 30000);
    return () => clearInterval(id);
  }, [jobsPendentes, loadRules]);

  function portaEhProtegida(valor: string): boolean {
    return PORTAS_PROTEGIDAS.includes(Number(valor));
  }

  function abrirNovaRegra() {
    setShowForm(true);
    setFormError('');
    setPorta('');
    setProtocolo('tcp');
    setPermitir('allow');
    setOrigemIp('');
  }

  async function handleSalvar() {
    setFormError('');
    if (portaEhProtegida(porta)) {
      setFormError('Portas 22, 80 e 443 são protegidas e não podem ser alteradas.');
      return;
    }
    try {
      await api.post('/firewall/rules', {
        acao: 'add',
        permitir: permitir === 'allow',
        porta: Number(porta),
        protocolo,
        origem_ip: origemIp || null,
      });
      setToast({ msg: 'Regra enfileirada — aplica em até 1 minuto', type: 'success' });
      setShowForm(false);
      loadRules();
    } catch (e: any) {
      setFormError(e?.response?.data?.detail || 'Erro ao criar regra');
    }
  }

  async function confirmDelete() {
    if (!deleteAlvo) return;
    try {
      await api.post('/firewall/rules', {
        acao: 'remove',
        permitir: deleteAlvo.permitir,
        porta: deleteAlvo.porta,
        protocolo: deleteAlvo.protocolo,
        origem_ip: deleteAlvo.origem_ip,
      });
      setToast({ msg: 'Remoção enfileirada — aplica em até 1 minuto', type: 'success' });
      loadRules();
    } catch {
      setToast({ msg: 'Erro ao remover regra', type: 'error' });
    }
    setDeleteAlvo(null);
  }

  return (
    <div style={{ padding: 24, maxWidth: 1000 }}>
      {toast && <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />}
      <h1 style={{ color: 'var(--text)', marginBottom: 20, fontSize: 22 }}>Firewall</h1>

      <div style={{ marginBottom: 16 }}>
        <button
          onClick={abrirNovaRegra}
          style={{ padding: '8px 16px', background: 'var(--accent)', color: '#000', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 700 }}
        >
          + Nova regra
        </button>
      </div>

      {loading && regras.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Carregando...</p>
      )}
      {!loading && regras.length === 0 && (
        <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 40 }}>Nenhuma regra encontrada.</p>
      )}

      {regras.map((r, i) => (
        <div key={`${r.porta}-${r.protocolo}-${r.origem_ip}-${i}`} style={card}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--text)', fontWeight: 600, fontFamily: 'monospace' }}>{r.porta}/{r.protocolo}</span>
            <span style={{
              padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600,
              background: r.permitir ? 'var(--success)' : 'var(--danger)', color: '#fff',
            }}>
              {r.permitir ? 'Permitir' : 'Negar'}
            </span>
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>Origem: {r.origem_ip ?? 'Qualquer'}</span>
            {r.protegida && (
              <span style={{
                padding: '2px 8px', borderRadius: 12, fontSize: 11, fontWeight: 600,
                background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--muted)',
              }}>
                Protegida
              </span>
            )}
            {!r.protegida && (
              <button
                onClick={() => setDeleteAlvo(r)}
                style={{ marginLeft: 'auto', padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--danger)', borderRadius: 6, color: 'var(--danger)', cursor: 'pointer', fontSize: 12 }}
              >
                Excluir
              </button>
            )}
          </div>
        </div>
      ))}

      {jobsPendentes.length > 0 && (
        <p style={{ color: 'var(--accent)', fontSize: 13, marginTop: 12 }}>
          {jobsPendentes.length} pedido(s) aplicando...
        </p>
      )}

      {showForm && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setShowForm(false)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, width: '85%', maxWidth: 460, padding: 24 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 16, color: 'var(--text)' }}>Nova regra</h3>
            <div style={{ display: 'grid', gap: 12, marginBottom: 16 }}>
              <div>
                <label style={{ color: 'var(--muted)', fontSize: 12 }}>Porta</label>
                <input type="number" style={input} value={porta} onChange={(e) => setPorta(e.target.value)} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Protocolo</label>
                  <select style={selectStyle} value={protocolo} onChange={(e) => setProtocolo(e.target.value)}>
                    <option value="tcp">TCP</option>
                    <option value="udp">UDP</option>
                  </select>
                </div>
                <div>
                  <label style={{ color: 'var(--muted)', fontSize: 12 }}>Ação</label>
                  <select style={selectStyle} value={permitir} onChange={(e) => setPermitir(e.target.value)}>
                    <option value="allow">Permitir</option>
                    <option value="deny">Negar</option>
                  </select>
                </div>
              </div>
              <div>
                <label style={{ color: 'var(--muted)', fontSize: 12 }}>Origem IP/CIDR (opcional)</label>
                <input
                  style={input} value={origemIp} onChange={(e) => setOrigemIp(e.target.value)}
                  placeholder="ex: 203.0.113.5 (vazio = qualquer origem)"
                />
              </div>
            </div>

            {formError && (
              <p style={{ color: 'var(--danger)', fontSize: 12, marginBottom: 12, whiteSpace: 'pre-wrap' }}>{formError}</p>
            )}

            <div style={{ display: 'flex', gap: 10 }}>
              <button
                onClick={handleSalvar}
                disabled={portaEhProtegida(porta)}
                style={{
                  padding: '8px 20px', border: 'none', borderRadius: 6, fontWeight: 700,
                  background: portaEhProtegida(porta) ? 'var(--surface)' : 'var(--accent)',
                  color: portaEhProtegida(porta) ? 'var(--muted)' : '#000',
                  cursor: portaEhProtegida(porta) ? 'not-allowed' : 'pointer',
                }}
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

      {deleteAlvo && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setDeleteAlvo(null)}
        >
          <div
            style={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, maxWidth: 420 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginBottom: 12, color: 'var(--text)' }}>Excluir regra</h3>
            <p style={{ color: 'var(--muted)', marginBottom: 20, fontSize: 14 }}>
              Tem certeza que deseja excluir a regra da porta {deleteAlvo.porta}/{deleteAlvo.protocolo}? Essa ação não pode ser desfeita.
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

Em `frontend/app/layout.tsx`, no array `NAV`, adicionar uma entrada logo depois de `/backups`:

```tsx
  { href: '/backups', label: 'Backups', icon: '💾' },
  { href: '/firewall', label: 'Firewall', icon: '🛡️' },
  { href: '/configuracoes', label: 'Configurações', icon: '⚙️' },
```

(Nota: `/seguranca` já usa o ícone 🛡️ — repetir o ícone é aceitável já que os labels são diferentes, mas usar outro ícone tipo 🧱 ou 🔒 é uma opção se quiser evitar duplicidade visual; não é bloqueante.)

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build limpo, sem erros de tipo, rota `/firewall` aparece na lista de rotas geradas.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/firewall/page.tsx frontend/app/layout.tsx
git commit -m "feat: adiciona pagina de gestao de firewall (/firewall)"
```

(Verificação manual no navegador fica por conta do usuário, combinado nesta sessão.)

---

### Task 6: Deploy para produção

**Files:** nenhum (ação operacional, sem mudança de código)

**Atenção:** esta task só deve ser executada após confirmação explícita do usuário — dá ao script no host permissão de alterar o firewall (`ufw`) da VPS de produção. Um erro aqui, ao contrário das features anteriores, pode derrubar o acesso SSH ou o próprio monitor — por isso as portas 22/80/443 são travadas em 2 camadas (API e worker) e o teste manual desta task usa só uma porta alta.

- [ ] **Step 1: Push para o remoto**

```bash
git push origin main
```

- [ ] **Step 2: Criar o arquivo de estado inicial no host ANTES do deploy**

Necessário porque o mount de `FIREWALL_STATE_FILE` é de um **arquivo único** — se o arquivo não existir antes do primeiro `docker compose up` com esse mount, o Docker cria um **diretório** no lugar (comportamento padrão de bind mount), quebrando tudo depois.

```bash
ssh root@144.91.92.70 "echo '{\"regras\": []}' > /opt/vps-monitor-firewall-state.json"
```

- [ ] **Step 3: Deploy na VPS**

```bash
ssh root@144.91.92.70 "cd /opt/vps-monitor && git pull --ff-only && bash monitor/deploy.sh"
```

- [ ] **Step 4: Confirmar containers saudáveis e mount correto**

```bash
ssh root@144.91.92.70 "docker ps --filter name=monitor --filter name=vps-monitor --format '{{.Names}}\t{{.Status}}'"
ssh root@144.91.92.70 "docker inspect monitor-backend --format '{{range .Mounts}}{{if eq .Destination \"/opt/vps-monitor-firewall-state.json\"}}{{.Destination}} RW={{.RW}}{{end}}{{end}}'"
```

Expected: todos `Up`, sem `Restarting`; mount mostra `RW=false`.

- [ ] **Step 5: Confirmar `python3` disponível no host**

```bash
ssh root@144.91.92.70 "which python3"
```

Expected: caminho existente (normalmente já vem instalado no Ubuntu/Debian).

- [ ] **Step 6: Garantir permissão de execução do script**

```bash
ssh root@144.91.92.70 "chmod +x /opt/vps-monitor/monitor/scripts/firewall-worker.sh && ls -l /opt/vps-monitor/monitor/scripts/firewall-worker.sh"
```

- [ ] **Step 7: Rodar o worker manualmente uma vez, sem nenhum job pendente, só pra gerar o primeiro snapshot real**

```bash
ssh root@144.91.92.70 "/opt/vps-monitor/monitor/scripts/firewall-worker.sh && cat /opt/vps-monitor-firewall-state.json"
```

Expected: JSON com as regras atuais do UFW (22, 80, 443, 8080 — vistas no brainstorming — todas com `"protegida": true` pras 3 primeiras, `false` pra 8080).

- [ ] **Step 8: Instalar o cron job**

```bash
ssh root@144.91.92.70 "(crontab -l 2>/dev/null; echo '* * * * * /opt/vps-monitor/monitor/scripts/firewall-worker.sh >> /var/log/firewall-worker.log 2>&1') | crontab -"
ssh root@144.91.92.70 "crontab -l | grep firewall-worker"
```

- [ ] **Step 9: Teste manual end-to-end usando uma porta de teste alta (nunca 22/80/443)**

1. Pela UI (`/firewall`), criar uma regra: porta `8081`, TCP, Permitir, sem origem.
2. Aguardar até 1 minuto, confirmar que a regra aparece na lista (via `GET /rules`, refletindo o snapshot atualizado) e no `ufw status` real do host:
   ```bash
   ssh root@144.91.92.70 "ufw status numbered | grep 8081"
   ```
3. Excluir a regra pela UI, confirmar que some dos dois lugares depois do próximo ciclo.
4. **Confirmar que tentar excluir/alterar a porta 22 (ou 80/443) direto via API retorna 403/400**, nunca chegando a enfileirar nada:
   ```bash
   ssh root@144.91.92.70 "curl -sk -X POST https://monitor.dlsistemas.com.br/api/firewall/rules -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '{\"acao\":\"remove\",\"permitir\":true,\"porta\":22,\"protocolo\":\"tcp\",\"origem_ip\":null}'"
   ```
   (Precisa de um token válido — pode ser feito pela UI também, tentando excluir a porta 22 e confirmando que o botão de excluir nem aparece, já que ela vem marcada `protegida: true`.)
