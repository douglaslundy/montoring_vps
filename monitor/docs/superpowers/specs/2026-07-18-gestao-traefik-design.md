# Gestão de Rotas Dinâmicas do Traefik via UI

## Contexto

Segunda tarefa do backlog definido pelo usuário em 2026-07-18 (ordem: listagem de projetos → **Traefik** → backup/restore). A spec de fail2ban (`2026-07-17-gestao-fail2ban-design.md`) já havia deixado explícito que Traefik ficaria pra uma spec separada, pelo mecanismo de validação ser diferente.

Traefik roda como uma stack própria na VPS, em `/opt/traefik` — um repositório git separado deste (`vps-monitor`), fora do escopo de código deste repo. Ele já expõe duas formas de roteamento:

- **Labels Docker** (`providers.docker`, auto-discovery via `docker-socket-proxy`) — usado por quase todos os projetos da VPS (ex: `monitor-nginx`, `portainer`). Fora de escopo aqui: mexeria em `docker-compose.yml` de outros projetos, não controlados por este repo.
- **Arquivos dinâmicos** (`providers.file`, diretório `/opt/traefik/dynamic/*.yml`, com `watch: true`) — usado hoje só por `mecanicapro.yml`, pra uma rota de subdomínio wildcard (`HostRegexp`) que labels Docker não conseguem expressar. **Esse é o alvo desta feature.**

`monitor-backend` já monta `/opt/traefik/dynamic` como `:ro` desde a feature de listagem de projetos (usado pra resolver o domínio de projetos sem label direta). Esta feature muda esse mount pra `:rw` e adiciona CRUD via UI.

### Descoberta de produção (validada com teste ao vivo, revertido em seguida)

Diferente do fail2ban, o Traefik **não precisa de um watcher no host pra aplicar a config**: com `file.watch: true`, ele observa `/opt/traefik/dynamic` e recarrega sozinho a cada mudança. Testado em produção (2026-07-18): um arquivo `.yml` com erro de sintaxe gera só um log de erro isolado (`Error occurred during watcher callback: ... providerName=file`) — as demais rotas (testado: Portainer via label Docker) continuam funcionando normalmente. Não existe o problema do fail2ban (cliente validando o estado de TODOS os jails antes de aplicar).

Isso significa: **escrever o arquivo já é suficiente pra aplicar a mudança** — não precisa de nenhum comando adicional pro Traefik pegar a config.

## Objetivo

Permitir criar, editar e excluir arquivos de rota dinâmica do Traefik pela UI do monitor (editor de YAML bruto), com validação de sintaxe antes de gravar, e histórico de mudanças via commits automáticos no git que já existe em `/opt/traefik`.

## Fora de escopo

- Gerenciar rotas via labels Docker de outros projetos (mexeria em `docker-compose.yml` de stacks não controladas por este repo).
- Editor estruturado (formulário com campos por tipo de rota) — decidido por YAML bruto, mais flexível, cobre qualquer config do file provider.
- Editar ou excluir `mecanicapro.yml` (criado à mão, fora da UI) — aparece na UI só como leitura, mesmo padrão do fail2ban pra jails manuais (`sshd`, etc.).
- Qualquer mudança na config estática do Traefik (`traefik.yml`, `docker-compose.yml` da stack do Traefik) — só arquivos individuais em `dynamic/`.
- Validação semântica do conteúdo Traefik (ex: checar se a `rule` referencia um `service` que existe) — só sintaxe YAML. Erros semânticos ficam visíveis nos logs do Traefik (fora do escopo desta UI verificar), consistente com o comportamento isolado já confirmado.

## Design

### Convenção de nomenclatura

Mesmo padrão do fail2ban: usuário informa um nome de exibição (ex: "Novo Cliente Wildcard"), backend gera slug (`vps-monitor-novo-cliente-wildcard`), arquivo final `/opt/traefik/dynamic/{slug}.yml`. O prefixo `vps-monitor-` é o que distingue "gerenciado pela UI" (editável/excluível) de "manual" (só leitura) — reaproveitar a função `_slugify` já existente em `api/fail2ban.py` (mover pra um helper compartilhado, ex: `api/_slug.py`, já que agora tem 2 usos).

### Docker

`docker-compose.yml`, serviço `monitor-backend` — mudar o mount existente de `:ro` pra `:rw`:

```yaml
      - /opt/traefik/dynamic:/opt/traefik/dynamic:rw
```

Nenhum outro volume novo (sem montar `.git` nem `certs/` — motivo detalhado na seção do watcher).

### `backend/api/traefik.py` (novo)

Prefixo de rota: `/api/traefik`, protegido por `verify_token_header` (mesmo padrão de `api/fail2ban.py`).

- `GET /api/traefik/routes` — lista arquivos `*.yml` em `TRAEFIK_DYNAMIC_DIR` (env var, default `/opt/traefik/dynamic` — já existe como `TRAEFIK_DYNAMIC_DIR` em `api/projects.py`, reaproveitar). Cada item: `{filename, managed: bool, content: str}`. `managed = filename.startswith("vps-monitor-")`.
- `POST /api/traefik/routes` — corpo: `{nome_exibicao: str, yaml_content: str}`.
  1. Gera slug com prefixo `vps-monitor-` → `filename = f"{slug}.yml"`.
  2. 409 se já existe um arquivo com esse nome.
  3. Valida `yaml.safe_load(yaml_content)` — 400 com a mensagem de erro do parser se inválido.
  4. Escreve o arquivo em `TRAEFIK_DYNAMIC_DIR/{filename}`.
  5. Grava log de auditoria (`acao="create"`).
  6. Retorna `{filename}`, status 201.
- `PUT /api/traefik/routes/{filename}` — corpo: `{yaml_content: str}`.
  1. 403 se `filename` não começar com `vps-monitor-` (protege `mecanicapro.yml`).
  2. 404 se o arquivo não existir.
  3. Valida `yaml.safe_load(yaml_content)` — 400 se inválido (arquivo original não é tocado).
  4. Sobrescreve o arquivo.
  5. Grava log de auditoria (`acao="edit"`).
- `DELETE /api/traefik/routes/{filename}` — 403 se não começar com `vps-monitor-`, 404 se não existir, senão remove e grava log de auditoria (`acao="delete"`).

Todas as rotas de escrita (`POST`/`PUT`/`DELETE`) usam `Depends(get_token_data)` pra registrar `username` no log, igual ao fail2ban.

### `backend/models/database.py` — novo modelo de auditoria

```python
class TraefikActionLog(Base):
    __tablename__ = "traefik_action_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    performed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    username = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    acao = Column(String, nullable=False)  # create, edit, delete
    sucesso = Column(Integer, default=1)
    erro = Column(Text, nullable=True)
```

Esse log audita **quem/quando pela sessão autenticada no monitor**; o commit automático no git de `/opt/traefik` (seção seguinte) audita **o conteúdo/diff** — são complementares, não redundantes (o git não sabe qual usuário do monitor fez a mudança, só que "a UI mudou algo").

### Watcher no host — `scripts/traefik-dynamic-commit-watcher.sh` (novo)

Mesmo padrão do `scripts/fail2ban-reload-watcher.sh` (cron, roda fora de qualquer container), mas por um motivo diferente: não é sobre visibilidade de host (o Traefik já recarrega sozinho, isso já foi confirmado), é sobre onde é seguro rodar `git commit`. Montar `/opt/traefik/.git` e o restante da árvore de trabalho (incluindo `/opt/traefik/certs/acme.json`, que tem permissão 600 e guarda chave privada) dentro do container do monitor pra permitir `git commit` de lá seria expor uma chave privada num container que não precisa dela. O host já tem o repo inteiro, git instalado, e a identidade `VPS Local History` configurada — rodar o commit de lá é mais simples e não exige nenhuma mudança na imagem Docker do backend (que hoje não tem `git` instalado).

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

Nota: `git add dynamic/` é escopado só a esse diretório — nunca `git add -A`/`-a`, pra não arrastar mudanças manuais pendentes em `traefik.yml`/`docker-compose.yml` (confirmado que existem, não relacionadas a esta feature, não devem ser commitadas por automação).

### Frontend — nova página `/traefik`

Segue o padrão visual de `/seguranca`:

- Lista de arquivos (via `GET /api/traefik/routes`): nome do arquivo, badge "gerenciado" ou "manual (só-leitura)".
- Arquivos manuais: conteúdo exibido num bloco read-only, sem botões de editar/excluir.
- Arquivos gerenciados: botão "Editar" (abre textarea com o YAML atual, Salvar/Cancelar) e "Excluir" (modal de confirmação, mesmo padrão de outras páginas).
- Botão "+ Nova Rota": campo nome de exibição + textarea de YAML, pré-preenchida com um esqueleto comentado (`http.routers` / `http.services`) baseado na estrutura de `mecanicapro.yml`, pra não começar em branco.
- Erros de validação (400 do backend, YAML inválido) aparecem inline no formulário, sem descartar o texto digitado.

### Testes

Backend (TDD, seguindo o padrão de `test_fail2ban_api.py` — fixture `auth_client` com `tmp_path` substituindo `TRAEFIK_DYNAMIC_DIR` via `monkeypatch.setenv`):

- `GET /api/traefik/routes`: lista arquivos do diretório temporário, marca `managed` corretamente pelo prefixo.
- `POST /api/traefik/routes`: sucesso cria o arquivo com o slug esperado e grava auditoria; YAML inválido retorna 400 e não cria arquivo; nome duplicado retorna 409.
- `PUT /api/traefik/routes/{filename}`: 403 se não tiver o prefixo `vps-monitor-`; 404 se não existir; YAML inválido retorna 400 sem alterar o arquivo original; sucesso sobrescreve e grava auditoria.
- `DELETE /api/traefik/routes/{filename}`: 403 se não tiver o prefixo; sucesso remove o arquivo e grava auditoria.
- Todos os testes usam arquivos reais num `tmp_path` (não há subprocess/binário externo envolvido, diferente do fail2ban — só leitura/escrita de arquivo e parse de YAML).

Frontend: `npm run build` limpo. Watcher: sem teste automatizado (shell script simples, mesmo caso do `fail2ban-reload-watcher.sh`), verificado manualmente em produção após o deploy — criar rota de teste via UI → confirmar que o Traefik aplica (curl) → confirmar commit no git de `/opt/traefik` → excluir → confirmar remoção.

## Arquivos afetados

- **Novo:** `backend/api/traefik.py`, `frontend/app/traefik/page.tsx`, `scripts/traefik-dynamic-commit-watcher.sh`
- **Modificado:** `backend/models/database.py` (novo modelo `TraefikActionLog`), `backend/main.py` (registrar router novo), `docker-compose.yml` (mount `:ro` → `:rw`)
- **Possívelmente modificado:** `backend/api/fail2ban.py` (se `_slugify` for extraído pra um helper compartilhado, ex: `backend/api/_slug.py`)
- **Novo (testes):** `backend/tests/test_traefik_api.py`
