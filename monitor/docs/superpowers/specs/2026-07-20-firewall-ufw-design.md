# Gestão de Regras de Firewall (UFW) via UI

## Contexto

Novo item de backlog, motivado por uma sugestão externa recebida pelo usuário sobre usar nftables/firewalld/CrowdSec como camada de segurança orquestrada pelo monitor. Levantamento feito em produção durante o brainstorming mostrou que a sugestão não se aplica literalmente:

- A VPS **não usa nftables puro** — usa **UFW** (Uncomplicated Firewall), que por baixo traduz pra nftables via `iptables-nft`. A tabela que o UFW gerencia tem aviso explícito do próprio `nft`: `Warning: table ip filter is managed by iptables-nft, do not touch!`.
- Já existe uma **segunda tabela nftables separada**, nativa, criada pelo fail2ban (`table inet f2b-table`) — bans de IP automáticos, já gerenciados via `/seguranca`. Fora de escopo aqui.
- **Firewalld e CrowdSec foram descartados** nesta rodada: firewalld não agrega nada que o padrão já estabelecido (script no host chamando o CLI da ferramenta) não resolva; CrowdSec é uma peça de infraestrutura nova e maior, avaliada como item de backlog separado, não decidido ainda.

Hoje o UFW está ativo, política padrão nega entrada, com 4 regras liberadas: `22/tcp` (SSH), `80/tcp`, `443/tcp` e `8080/tcp` — espelhadas em IPv4 e IPv6.

## Objetivo

Permitir, pela UI do monitor, ver as regras atuais do UFW e criar/remover regras de porta (com origem IP/CIDR opcional), sem risco de a própria UI derrubar o acesso SSH ou as portas do monitor.

## Fora de escopo

- Bans automáticos de IP (já cobertos por `/seguranca`, tabela `f2b-table` separada).
- Mudar a política padrão do UFW (`ufw default deny/allow`) — só regras individuais.
- Firewalld, CrowdSec, ou qualquer substituição da stack de segurança atual — avaliados/descartados nesta rodada.
- Editor de comando UFW livre — só formulário estruturado (porta, protocolo, ação, origem).
- Regras de `forward`/roteamento — só tráfego de entrada (`in`), mesmo escopo das regras existentes.

## Design

### Arquitetura geral

Mesmo padrão já usado 3x nesta base de código (fail2ban, Traefik, backup/restore): o container `monitor-backend` nunca roda `ufw` diretamente — isso mexeria no firewall do **kernel do host**, não do container, e daria ao container um nível de acesso desproporcional. Em vez disso:

1. O backend só grava **pedidos** (`FirewallRuleRequest`) no seu próprio SQLite.
2. Um script novo no host, `scripts/firewall-worker.sh` (cron, 1x/min, mesmo padrão dos outros 3 workers), a cada execução:
   - Processa no máximo um pedido pendente por vez, aplicando via `ufw allow`/`ufw deny`/`ufw delete allow`/`ufw delete deny`.
   - Regenera um snapshot JSON do estado atual (`ufw status numbered` convertido), salvo em `FIREWALL_STATE_FILE` (default `/opt/vps-monitor-firewall-state.json`), montado **read-only** no `monitor-backend` — a API nunca roda `ufw` pra leitura, só lê esse arquivo.

**Portas protegidas — `{22, 80, 443}` — são bloqueadas no código do backend, sem exceção.** Qualquer `POST /api/firewall/rules` (criar OU remover) que envolva uma dessas portas retorna 400 antes de qualquer outra validação, antes mesmo de tocar no banco. Essa é a única garantia real de que a UI não consegue se auto-bloquear do SSH ou derrubar o acesso ao próprio monitor — não existe modal de confirmação que sobrescreva essa trava.

Remoção de regra é feita **por especificação** (porta + protocolo + permitir/negar + origem), nunca por número de posição — os números do `ufw status numbered` mudam a cada alteração, então usar posição seria uma corrida (race) entre o que a UI mostrou e o que existe de fato quando o worker rodar. `ufw delete allow/deny ...` (a mesma sintaxe usada pra criar, prefixada com `delete`) já resolve isso nativamente, sem precisar de número.

### Modelo de dados — `backend/models/database.py`

```python
class FirewallRuleRequest(Base):
    __tablename__ = "firewall_rule_request"
    id = Column(Integer, primary_key=True, autoincrement=True)
    acao = Column(String, nullable=False)          # add | remove
    permitir = Column(Integer, nullable=False)      # 1=allow, 0=deny
    porta = Column(Integer, nullable=False)
    protocolo = Column(String, nullable=False)      # tcp | udp
    origem_ip = Column(String, nullable=True)       # None = qualquer origem
    status = Column(String, nullable=False, default="pending")  # pending | done | failed
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)
    concluido_em = Column(DateTime, nullable=True)
    erro = Column(Text, nullable=True)
    username = Column(String, nullable=False)
```

### Backend — `/api/firewall`

Segue o padrão de `api/backups.py` (`APIRouter(prefix="/api/firewall", dependencies=[Depends(verify_token_header)])`).

- `GET /rules` — lê `FIREWALL_STATE_FILE` (lista de regras ativas: porta, protocolo, permitir/negar, origem) + os `FirewallRuleRequest` não finalizados (pra UI mostrar "aplicando..."). Marca cada regra ativa com `protegida: bool` (`porta in {22, 80, 443}`).
- `POST /rules` — corpo `{acao: "add"|"remove", permitir: bool, porta: int, protocolo: "tcp"|"udp", origem_ip: str | null}`.
  1. **Primeira checagem, sempre**: se `porta in {22, 80, 443}` → 400, sem exceção.
  2. Valida `protocolo` (`tcp`/`udp`), `porta` (1-65535), e `origem_ip` (se informado, precisa ser um IP ou CIDR válido — usar `ipaddress.ip_network(valor, strict=False)` e capturar `ValueError` → 400).
  3. 409 se já existe um `FirewallRuleRequest` com o mesmo `(acao, permitir, porta, protocolo, origem_ip)` em status `pending`.
  4. Cria o `FirewallRuleRequest`, retorna 202 com `{request_id}`.

Sem endpoint de exclusão por ID — remover uma regra existente é um `POST /rules` com `acao: "remove"` e os mesmos campos da regra a ser removida (o frontend monta esse corpo a partir da linha exibida, o usuário não digita nada de novo).

### Script no host — `scripts/firewall-worker.sh`

Mesmo esqueleto do `scripts/backup-worker.sh` (já com os fixes de robustez da revisão anterior): `flock` no topo (evita duas execuções simultâneas mexendo no firewall ao mesmo tempo), `sqlite3 -cmd ".timeout 5000"` (não `PRAGMA busy_timeout`, que suja a saída capturada), sweep de jobs presos em `running` há mais de 1 hora (aplicar regra de firewall é quase instantâneo — bem mais rápido que um snapshot — então esse limite pode ser bem menor que o do backup-worker).

A cada execução:
1. Processa no máximo um `FirewallRuleRequest` pendente:
   - `add` + `permitir=1`: `ufw allow [from <origem_ip>] to any port <porta> proto <protocolo>` (omite `from` se `origem_ip` for nulo).
   - `add` + `permitir=0`: mesma coisa trocando `allow` por `deny`.
   - `remove`: mesmo comando prefixado com `delete` (`ufw delete allow ...` / `ufw delete deny ...`).
   - Repete a checagem de porta protegida **também no worker** (defesa em profundidade — não confia só na validação já feita pela API antes de inserir a linha).
2. Regenera o snapshot: `ufw status numbered`, parseado linha a linha, escrito como JSON em `FIREWALL_STATE_FILE`.

### Frontend — nova página `/firewall`

Segue o padrão visual de `/seguranca`/`/traefik`/`/backups`:

- Lista de regras ativas (do snapshot): porta, protocolo, permitir/negar, origem (`Qualquer` se `origem_ip` nulo). Regras com `protegida: true` mostram um selo "Protegida" e não têm botão de excluir.
- Botão "+ Nova regra": formulário com porta (number), protocolo (select tcp/udp), permitir/negar (select), origem IP/CIDR (opcional). Se a porta digitada estiver em `{22, 80, 443}`, o formulário já desabilita o botão de salvar no cliente (a validação real e definitiva continua sendo a do backend).
- Excluir regra: botão só nas regras não-protegidas, modal de confirmação simples (mesmo padrão do resto do app — sem exigir digitar nome, já que o risco aqui é mitigado pela trava de portas protegidas, não precisa do mesmo nível de fricção do restore de backup).
- `FirewallRuleRequest` pendente aparece com indicação "aplicando..." até o próximo ciclo do worker (polling leve, mesmo padrão de `/backups`).

### Testes

Backend (TDD, mockando filesystem/DB — sem subprocess real, mesmo padrão do Traefik/backups):
- `GET /rules`: lê o snapshot mockado, marca `protegida` corretamente pras portas 22/80/443.
- `POST /rules` (`add`): sucesso cria o request; 400 se `porta` for protegida (testar as 3: 22, 80, 443); 400 se `protocolo` inválido; 400 se `origem_ip` não for IP/CIDR válido; 409 se já existe request idêntico pendente.
- `POST /rules` (`remove`): mesma trava de porta protegida — 400 mesmo pra remover.
- Sanitização de `origem_ip`: valores como `999.999.999.999` ou `"; rm -rf /"` são rejeitados antes de qualquer persistência.

Script no host: sem teste automatizado (mesmo padrão dos outros 3 workers). Verificação manual em produção: criar/remover uma regra numa porta de teste alta (ex: 8081) — **nunca 22/80/443** — confirmando que a regra aparece/some do `ufw status` real e do snapshot lido pela API.

## Arquivos afetados

- **Novo:** `backend/api/firewall.py`, `frontend/app/firewall/page.tsx`, `scripts/firewall-worker.sh`
- **Modificado:** `backend/models/database.py` (`FirewallRuleRequest`), `backend/main.py` (registrar router), `docker-compose.yml` (mount read-only de `FIREWALL_STATE_FILE`), `frontend/app/layout.tsx` (NAV)
- **Novo (testes):** `backend/tests/test_firewall_api.py`
