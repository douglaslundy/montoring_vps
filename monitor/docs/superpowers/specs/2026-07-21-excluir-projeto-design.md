# Excluir Projeto (teardown completo) via UI

## Contexto

Pedido do usuário, feito duas vezes durante a sessão anterior (deferido até a conclusão das features de monitoramento e firewall que já estavam em andamento): um botão que apaga **todos os traços** de um projeto docker-compose da VPS — containers, volumes, regras de firewall associadas e rotas do Traefik — com confirmação forte, já que é uma ação irreversível. Esclarecido anteriormente que "DNS" mencionado no pedido original se referia só ao roteamento do Traefik, não a um provedor de DNS externo.

Esta é a ação mais destrutiva de todas as features já implementadas neste projeto (apaga dados reais em volumes, não só configuração).

## Objetivo

Permitir, pela tela `/projetos`, excluir um projeto inteiro da VPS — parar e remover seus containers, apagar seus volumes Docker, remover rotas do Traefik associadas e sugerir remoção de regras de firewall associadas — com um fluxo de confirmação forte que exige um snapshot de backup recém-criado antes de liberar a exclusão definitiva.

## Fora de escopo

- Remoção automática de regras de firewall sem confirmação manual explícita (ver seção de Firewall abaixo — não existe vínculo confiável entre regra de firewall e projeto).
- Exclusão do projeto `vps-monitor` (o próprio monitor) — bloqueada no código, sem exceção.
- Qualquer forma de "restaurar depois de excluir" além do snapshot de backup já criado como pré-requisito (a restauração em si já é coberta pela feature de backup/restore existente).
- Suporte a projetos sem containers rodando no momento (a listagem de projetos hoje só existe a partir de containers ativos via `com.docker.compose.project`).

## Design

### Arquitetura geral

Mesmo padrão já usado 4x nesta base (fail2ban, Traefik, backup/restore, firewall): o `monitor-backend` nunca executa `docker compose down`, `docker volume rm`, ou qualquer ação destrutiva diretamente. Ele só grava um **pedido de exclusão** (`ProjectDeleteRequest`) no seu próprio SQLite. Um script novo no host, `scripts/project-delete-worker.sh` (cron, 1x/min, mesmo padrão dos outros 4 workers), processa o pedido pendente e executa as ações reais.

**Proteção dura: o projeto `vps-monitor` nunca pode ser alvo de exclusão.** Tanto `GET /delete-preview` quanto `POST /delete` retornam 400 imediatamente se `projeto == "vps-monitor"`, antes de qualquer outra validação — mesma filosofia da trava de portas 22/80/443 na feature de firewall.

Fluxo completo, na ordem:
1. Usuário clica "Excluir projeto" num card da tela `/projetos`.
2. Frontend chama `GET /api/projects/{projeto}/delete-preview`, que monta **na hora** (sem fila, síncrono): lista de containers do projeto, lista de volumes reais usados (via `docker_client.container_inspect()` de cada container — o Docker API já retorna `Mounts` no inspect, mesmo sem o socket-proxy habilitar operações de escrita em `/volumes`), candidatas de rotas Traefik (arquivos `vps-monitor-*.yml` cujo domínio/slug bate com o nome do projeto, mesma heurística de `_dominio_por_arquivo_dinamico`), e candidatas de regras de firewall (regras cuja porta bate com alguma porta publicada por um container do projeto).
3. Modal mostra o preview: containers e volumes que serão apagados (informativo, não editável), rotas Traefik candidatas **pré-marcadas** (usuário pode desmarcar), regras de firewall candidatas **não marcadas** (usuário precisa marcar manualmente as que quiser incluir).
4. Passo obrigatório: botão "Criar snapshot e continuar" — dispara a criação de um snapshot novo do projeto (reaproveita o job de backup já existente, `POST /api/backups/projects/{projeto}/snapshots`). Só depois desse snapshot terminar com sucesso (`status == "done"`, via polling) o passo seguinte é liberado.
5. Campo de confirmação final: digitar o nome do projeto exatamente (mesmo padrão client-side do restore de backup — comparação exata, botão desabilitado até bater).
6. `POST /api/projects/{projeto}/delete` grava o `ProjectDeleteRequest` com a lista de rotas/regras marcadas e o nome do arquivo de snapshot criado no passo 4.
7. Worker no host processa o pedido, na ordem: para os containers → remove os arquivos Traefik marcados (o watcher de commit já existente, `traefik-dynamic-commit-watcher.sh`, detecta a remoção e commita sozinho, sem mudança necessária nele) → enfileira as remoções de firewall marcadas na tabela `firewall_rule_request` já existente (reaproveitando o `firewall-worker.sh` já testado, incluindo sua trava de portas protegidas — não duplica lógica de `ufw` no worker novo) → descobre e remove os volumes reais (`docker volume rm`, mesma descoberta já usada em `backup-worker.sh` via `docker inspect --format`) → remove os containers.

### Modelo de dados — `backend/models/database.py`

```python
class ProjectDeleteRequest(Base):
    __tablename__ = "project_delete_request"
    id = Column(Integer, primary_key=True, autoincrement=True)
    projeto = Column(String, nullable=False)
    rotas_traefik_selecionadas = Column(Text, nullable=False)   # JSON: lista de filenames
    regras_firewall_selecionadas = Column(Text, nullable=False) # JSON: lista de {porta, protocolo, permitir, origem_ip}
    snapshot_arquivo = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending | running | done | failed
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)
    concluido_em = Column(DateTime, nullable=True)
    erro = Column(Text, nullable=True)
    username = Column(String, nullable=False)
```

### Backend — `/api/projects/{projeto}/delete-preview` e `/api/projects/{projeto}/delete`

Adicionado ao router já existente em `api/projects.py` (mesmo prefixo `/api/projects`).

- **`GET /api/projects/{projeto}/delete-preview`**:
  1. 400 se `projeto == "vps-monitor"`.
  2. 404 se o projeto não aparece em `agrupar_por_projeto` (não existe/nenhum container ativo).
  3. Monta e retorna: `{containers: [...], volumes: [...], rotas_candidatas: [...], regras_firewall_candidatas: [...]}`.
     - `volumes`: nomes únicos extraídos de `Mounts` (`Type == "volume"`) de cada container do projeto, via `docker_client.container_inspect()`.
     - `rotas_candidatas`: arquivos `vps-monitor-*.yml` em `TRAEFIK_DYNAMIC_DIR` cujo domínio resolvido bate com o nome do projeto (reaproveitando a lógica de `_dominio_por_arquivo_dinamico`).
     - `regras_firewall_candidatas`: regras do snapshot de firewall (`FIREWALL_STATE_FILE`) cuja porta bate com alguma porta publicada (`Ports`) por um container do projeto — **não protegidas** (22/80/443 nunca aparecem como candidatas, já que nunca poderiam ser removidas mesmo se selecionadas).
- **`POST /api/projects/{projeto}/delete`** — corpo `{snapshot_arquivo: str, rotas_selecionadas: list[str], regras_selecionadas: list[{porta, protocolo, permitir, origem_ip}]}`.
  1. 400 se `projeto == "vps-monitor"`, antes de qualquer outra checagem.
  2. 404 se o projeto não existe (mesma checagem do preview).
  3. Valida que `snapshot_arquivo` corresponde a um snapshot existente e `status == "done"` desse projeto (reaproveitando a listagem de snapshots já existente em `api/backups.py`) — 400 se não bater.
  4. Valida que cada filename em `rotas_selecionadas` começa com `vps-monitor-` (mesma trava já usada em `api/traefik.py` — nunca aceita apagar uma rota manual).
  5. Valida que cada regra em `regras_selecionadas` não tem porta em `{22, 80, 443}` (mesma trava da feature de firewall, checada de novo aqui como defesa em profundidade).
  6. 409 se já existe um `ProjectDeleteRequest` não finalizado (`pending`/`running`) para esse projeto.
  7. Cria o `ProjectDeleteRequest`, retorna 202 com `{request_id}`.

Sem endpoint de cancelamento — uma vez confirmado, o pedido é processado pelo worker (mesmo padrão das outras 4 features).

### Script no host — `scripts/project-delete-worker.sh`

Mesmo esqueleto dos outros 4 workers: `flock` no topo, `sqlite3 -cmd ".timeout 5000"` (nunca `PRAGMA busy_timeout`), sweep de jobs presos em `running` há mais de 30 minutos (operação mais longa que firewall/traefik — remoção de volumes grandes pode demorar — mas ainda bem mais rápida que um restore completo de backup, então o limite fica entre o do firewall/traefik e o do backup).

A cada execução, processa no máximo um `ProjectDeleteRequest` pendente:
1. Descobre os IDs dos containers do projeto via `docker ps --filter "label=com.docker.compose.project=<projeto>" -q`.
2. Repete a checagem de proteção do `vps-monitor` **também no worker** (defesa em profundidade).
3. `docker stop` nos containers descobertos.
4. Remove cada arquivo em `rotas_traefik_selecionadas` de `TRAEFIK_DYNAMIC_DIR` (`rm`) — o `traefik-dynamic-commit-watcher.sh` já existente detecta a remoção no próximo ciclo e faz o commit sozinho, sem mudança necessária nele.
5. Para cada regra em `regras_firewall_selecionadas`, insere uma linha em `firewall_rule_request` (`acao="remove"`, mesmos campos) — o `firewall-worker.sh` já existente processa essas linhas nos próximos ciclos, com sua própria trava de portas protegidas já testada.
6. Descobre os volumes reais de cada container (`docker inspect <id> --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{end}}{{end}}'`, mesma lógica já usada em `backup-worker.sh`) e roda `docker volume rm` em cada um.
7. `docker rm` nos containers descobertos no passo 1.
8. Marca o pedido como `done` (ou `failed` com o erro, se qualquer passo falhar — sem rollback automático, já que a maioria das ações não é reversível de qualquer forma; o erro fica registrado pra investigação manual).

### Frontend — modal de exclusão em `frontend/app/projetos/page.tsx`

- Botão "Excluir projeto" em cada card de projeto (exceto o card do próprio `vps-monitor`, que não mostra o botão).
- Passo 1 (preview): ao clicar, chama `GET /delete-preview` e mostra containers/volumes (informativo), rotas candidatas (checkboxes pré-marcados) e regras de firewall candidatas (checkboxes não marcados). Botão "Criar snapshot e continuar".
- Passo 2 (aguardando snapshot): mostra "criando snapshot..." com polling no job de backup já existente, até `status == "done"`.
- Passo 3 (confirmação final): campo de texto "Digite **{projeto}** pra confirmar", botão "Excluir definitivamente" desabilitado até bater exatamente (mesmo padrão do restore de backup).
- Após confirmar: `POST /delete`, modal fecha, card do projeto mostra "excluindo..." (polling leve, mesmo padrão das outras features) até o projeto sumir da listagem (quando os containers forem removidos de fato).

### Testes

Backend (TDD, mockando filesystem/DB/docker_client — sem subprocess real):
- `GET /delete-preview`: 400 se `projeto == "vps-monitor"`; 404 se projeto não existe; monta containers/volumes/candidatas corretamente a partir de mocks do `docker_client` e do snapshot de firewall.
- `POST /delete`: 400 se `projeto == "vps-monitor"` (checado antes de qualquer outra coisa); 404 se projeto não existe; 400 se `snapshot_arquivo` não existe ou não está `done`; 400 se alguma rota selecionada não começa com `vps-monitor-`; 400 se alguma regra selecionada tem porta protegida (22/80/443); 409 se já existe pedido pendente/rodando para o projeto; sucesso cria o `ProjectDeleteRequest` e retorna 202.

Script no host: sem teste automatizado (mesmo padrão dos outros 4 workers). Verificação manual em produção planejada num projeto de teste descartável (não no `vps-monitor` nem em projeto de cliente real).

## Arquivos afetados

- **Novo:** `scripts/project-delete-worker.sh`
- **Modificado:** `backend/models/database.py` (`ProjectDeleteRequest`), `backend/api/projects.py` (2 endpoints novos), `frontend/app/projetos/page.tsx` (modal de exclusão)
- **Novo (testes):** `backend/tests/test_project_delete_api.py`
