# Gestão de Fail2ban via UI

## Contexto

O usuário pediu uma interface no monitor pra gerenciar regras do fail2ban e do Traefik (hoje só editáveis via SSH direto nos arquivos de config). Diferente das features anteriores (containers, disco), fail2ban e Traefik não têm nenhuma API restrita tipo o docker-socket-proxy — exigem acesso direto ao sistema de arquivos do host e execução de comandos, um nível de acesso bem maior do que o monitor tem hoje.

Dado o tamanho e o mecanismo de validação distinto de cada ferramenta, esta spec cobre **só fail2ban** — Traefik fica pra uma spec separada depois.

## Objetivo

Permitir criar, editar e excluir jails do fail2ban pela UI do monitor, com validação real (dry-run do regex) antes de aplicar qualquer mudança, e desbanir IPs de qualquer jail (inclusive os criados manualmente).

## Fora de escopo

- Editar/excluir jails ou filtros que já existem manualmente (ex: `sshd`, `mecanicapro-ghost-subdomain`) — esses aparecem na UI só como leitura (nome, status, IPs banidos).
- Gestão do Traefik (spec separada).
- Qualquer mudança na config global do fail2ban (`/etc/fail2ban/fail2ban.conf`, `jail.conf`) — só jails/filtros individuais.

## Design

### Convenção de nomenclatura

O usuário informa um nome de exibição (ex: "Bloqueio de scraper de preços"); o backend gera um slug (`vps-monitor-bloqueio-scraper-precos`) usado como:
- Nome do jail no fail2ban.
- Arquivo `/etc/fail2ban/jail.d/{slug}.local`.
- Arquivo `/etc/fail2ban/filter.d/{slug}.conf`.

Todo jail/filtro criado pela UI tem o prefixo `vps-monitor-` — é assim que o backend distingue "gerenciado pelo monitor" (editável) de "manual" (só leitura), sem precisar de nenhum registro separado.

### Docker

`backend/Dockerfile` — adicionar `fail2ban` ao `apt-get install` já existente (imagem é `python:3.11-slim`, pacote padrão Debian, só usa o cliente).

`docker-compose.yml`, serviço `monitor-backend`, novos volumes:

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

### `backend/collector/fail2ban_client.py` (novo)

Wrapper assíncrono em torno do binário `fail2ban-client` (via `asyncio.create_subprocess_exec`, já que não é uma API HTTP como o Docker):

- `async def status_all() -> list[dict]`: roda `fail2ban-client status` (lista nomes de jails), depois `fail2ban-client status <nome>` pra cada um (parseia "Currently banned", "Total banned", "Banned IP list", "Currently failed"). Retorna todos os jails, com uma flag `managed: bool` (`nome.startswith("vps-monitor-")`).
- `async def dry_run_regex(sample_line: str, filter_path: str) -> tuple[bool, str]`: roda `fail2ban-regex <sample_line> <filter_path>`, procura por `Success` com pelo menos 1 match no stdout. Retorna `(bateu, saida_completa)`.
- `async def reload_jail(nome: str) -> None`: roda `fail2ban-client reload <nome>`.
- `async def stop_jail(nome: str) -> None`: roda `fail2ban-client stop <nome>` (remove o jail da memória do fail2ban).
- `async def unban_ip(nome: str, ip: str) -> None`: roda `fail2ban-client set <nome> unbanip <ip>`.

### `backend/models/database.py` — novo modelo de auditoria

```python
class Fail2banActionLog(Base):
    __tablename__ = "fail2ban_action_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    performed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    username = Column(String, nullable=False)
    jail_nome = Column(String, nullable=False)
    acao = Column(String, nullable=False)  # create, edit, delete, unban
    detalhes = Column(Text, nullable=True)
    sucesso = Column(Integer, default=1)
    erro = Column(Text, nullable=True)
```

### `backend/api/fail2ban.py` (novo)

- `GET /api/fail2ban/jails` — retorna todos os jails (`status_all()`), marcando `managed`.
- `POST /api/fail2ban/jails` — corpo: `{nome_exibicao, log_path, sample_log_line, regex, maxretry, findtime, bantime, port}`.
  1. Gera o slug (com prefixo `vps-monitor-`).
  2. Valida que o regex compila (`re.compile`).
  3. Escreve o arquivo de filtro em `/etc/fail2ban/filter.d/{slug}.conf`.
  4. Roda `dry_run_regex(sample_log_line, filtro)` — se não bater (`Success: 0` ou erro), apaga o arquivo de filtro recém-criado e retorna 400 com a saída do dry-run.
  5. Escreve o arquivo de jail em `/etc/fail2ban/jail.d/{slug}.local` (`enabled=true`, `backend=auto`, `filter={slug}`, `logpath`, `port`, `maxretry`, `findtime`, `bantime`, `banaction=nftables`).
  6. `reload_jail(slug)`.
  7. Confirma com `status_all()` que o jail aparece ativo.
  8. Grava `Fail2banActionLog` (`acao="create"`).
- `PUT /api/fail2ban/jails/{slug}` — só permitido se `slug.startswith("vps-monitor-")` (senão 403). Mesmo fluxo de validação do POST, sobrescrevendo os arquivos existentes.
- `DELETE /api/fail2ban/jails/{slug}` — só permitido se `slug.startswith("vps-monitor-")` (senão 403). `stop_jail(slug)`, depois apaga os dois arquivos.
- `POST /api/fail2ban/jails/{slug}/unban` — corpo: `{ip}`. Permitido pra **qualquer** jail (inclusive manuais, como `sshd`) — ação de baixo risco, sempre reversível. `unban_ip(slug, ip)`.

Todas as rotas gravam em `Fail2banActionLog` (sucesso ou falha).

### Frontend — nova página `/seguranca`

- Lista todos os jails (via `GET /api/fail2ban/jails`): nome, status (ativo/inativo), quantidade de IPs banidos, lista de IPs banidos com botão "Desbanir" ao lado de cada um (disponível em qualquer jail).
- Jails com `managed: true` (prefixo `vps-monitor-`) ganham botões "Editar" e "Excluir" (com modal de confirmação, mesmo padrão já usado em outras páginas).
- Jails com `managed: false` não têm esses botões — só a lista de IPs banidos + botão de desbanir.
- Botão "+ Novo Jail" abre um modal/formulário: nome de exibição, caminho do log, linha de exemplo (obrigatória, usada no dry-run), regex, maxretry, findtime, bantime, porta.
- Se o dry-run falhar, mostra a saída do `fail2ban-regex` na tela pro usuário ajustar o regex antes de tentar de novo.

### Testes

Backend (TDD):
- `fail2ban_client.dry_run_regex`: regex que bate com a linha de exemplo → `(True, ...)`; regex que não bate → `(False, ...)`.
- `fail2ban_client.status_all`: parseia corretamente saída mockada de `fail2ban-client status` e `status <jail>`, marca `managed` corretamente pelo prefixo.
- `POST /api/fail2ban/jails`: sucesso grava os 2 arquivos + chama reload + grava auditoria; falha no dry-run não escreve o arquivo de jail e retorna 400; regex inválido (não compila) retorna 400 antes de escrever qualquer arquivo.
- `PUT`/`DELETE /api/fail2ban/jails/{slug}`: 403 se o slug não tiver o prefixo `vps-monitor-`.
- `POST /api/fail2ban/jails/{slug}/unban`: funciona pra qualquer jail, inclusive sem o prefixo.
- Todos os testes mockam o subprocess do `fail2ban-client` (nunca chamam o binário de verdade).

Frontend: `npm run build` limpo. Verificação manual fica por conta do usuário (combinado nesta sessão).

## Arquivos afetados

- **Novo:** `backend/collector/fail2ban_client.py`, `backend/api/fail2ban.py`, `frontend/app/seguranca/page.tsx`
- **Modificado:** `backend/models/database.py`, `backend/main.py` (registrar o router novo), `backend/Dockerfile`, `docker-compose.yml`
- **Novo (testes):** `backend/tests/test_fail2ban_client.py`, `backend/tests/test_fail2ban_api.py`
