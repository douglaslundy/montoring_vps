# Backup/Restore de Projetos da VPS via UI

## Contexto

Terceira e última tarefa do backlog definido pelo usuário em 2026-07-18 (ordem: listagem de projetos → Traefik → **backup/restore**). Hoje não existe nenhum backup sistemático na VPS — só dumps manuais avulsos e sem automação, espalhados em `/opt/corridas/backups`, `.sql`/`.tgz` soltos em `/opt` e `/root`. A VPS roda ~30 containers em ~7 stacks docker-compose distintas (vps-monitor, mecanicapro, corridas, evolution, supabase-corridas, supabase-syscursos, traefik+portainer), cada uma um projeto/cliente diferente.

Levantamento feito em produção durante o brainstorming:
- Disco com folga: 31G usados de 193G (17%), volumes hoje pequenos (o maior, `vps-monitor_vps_monitor_data`, tem 282MB).
- Cada projeto tem um diretório de trabalho identificável via label Docker `com.docker.compose.project.working_dir` — **não segue um padrão fixo tipo `/opt/<nome>`** (ex: o próprio vps-monitor está em `/opt/vps-monitor/monitor`, um subdiretório do nome do projeto).
- Os volumes nomeados de um projeto podem estar espalhados entre vários containers da mesma stack (ex: o Postgres do mecanicapro está só no container `mecanicapro-postgres-1`, não no `-backend-1`) — é preciso inspecionar todos os containers do projeto, não só um.

## Objetivo

Permitir, pela UI do monitor, criar snapshots (backup) de qualquer projeto docker-compose da VPS (volumes Docker + diretório de trabalho), agendar snapshots automáticos por projeto, restaurar um snapshot anterior, baixar o arquivo do snapshot, e ter retenção automática (manter os N mais recentes por projeto).

## Fora de escopo

- Armazenamento externo/offsite (S3 ou similar) — snapshots ficam só na própria VPS por agora. Download manual do `.tar.gz` pela UI é o único caminho pra cópia offsite nesta versão.
- Dumps lógicos específicos por tipo de banco (`pg_dump`, etc.) — o snapshot é uma cópia bruta dos volumes Docker + diretório de trabalho, independente do que roda dentro.
- Ação "backup de tudo agora" (todos os projetos de uma vez) — cada projeto é acionado individualmente (manual ou por agendamento próprio).
- Restaurar um snapshot em um projeto/VPS diferente de onde foi criado — restore sempre atua sobre o mesmo projeto de origem, na mesma VPS.
- Editar/gerenciar containers ou volumes fora do conceito de "projeto" (com label `com.docker.compose.project`) — containers sem esse label (avulsos) não aparecem nesta feature, mesmo tratamento já usado em `/projetos`.

## Design

### Arquitetura geral

Mesmo padrão já usado 2x nesta base de código (gestão de fail2ban, gestão de rotas do Traefik): o container `monitor-backend` **nunca** acessa diretamente volumes ou diretórios de outros projetos — isso exigiria montar `/opt` e `/var/lib/docker/volumes` inteiros no container, expondo segredos/dados de ~7 clientes diferentes de uma vez. Em vez disso:

1. O backend só grava/lê **intenções** (`BackupJob`) no seu próprio banco SQLite (`vps_monitor_data`, já existente).
2. Um script novo no host, `scripts/backup-worker.sh` (cron, 1x/min, mesmo cadência dos watchers existentes), lê essa fila e executa de verdade — parar containers do projeto-alvo, `tar` dos volumes + diretório de trabalho, subir de novo — com acesso total do host, sem nenhuma restrição de mount.
3. Snapshots ficam em `/opt/vps-monitor-backups/<projeto>/<timestamp>.tar.gz` no host; esse diretório é montado **read-only** no `monitor-backend` (`docker-compose.yml`), pra a API poder listar/baixar sem poder escrever ali diretamente (só o script no host escreve).

Tanto snapshot quanto restore **param os containers do projeto brevemente** (`docker compose stop` → operação → `docker compose up -d`) pra garantir consistência dos dados (evita copiar/restaurar um banco Postgres pela metade). Dado que os volumes são pequenos hoje, esse downtime deve ser curto.

### Como o script descobre "o que pertence a um projeto"

Dado um nome de projeto (`com.docker.compose.project`), o `backup-worker.sh`:
1. Lista todos os containers com esse label: `docker ps -a --filter "label=com.docker.compose.project=$PROJETO" --format '{{.Names}}'`.
2. Pega o diretório de trabalho do label `com.docker.compose.project.working_dir` de qualquer um desses containers (todos os containers da mesma stack têm o mesmo valor).
3. Inspeciona os `Mounts` (`docker inspect`) de **cada** container listado no passo 1, coletando os nomes únicos de todo mount com `"Type": "volume"`.
4. Snapshot = `tar` do diretório de trabalho (passo 2) + `tar` de cada volume nomeado (passo 3, via `/var/lib/docker/volumes/<nome>/_data`), compactados juntos num único `.tar.gz` com uma estrutura interna previsível (`workdir/` e `volumes/<nome>/`).
5. Restore reverte o processo: extrai `workdir/` de volta pro diretório de trabalho original, e cada `volumes/<nome>/` de volta pro path do volume Docker correspondente.
6. Parar/subir os containers é sempre `cd <working_dir> && docker compose stop` / `docker compose up -d` — o nome do projeto é resolvido pelo próprio Compose (via a chave `name:` pinada no `docker-compose.yml`, quando existir, como já é o caso do vps-monitor, ou pelo nome do diretório), mesmo mecanismo já usado em `deploy.sh`. Nunca usar `docker stop/start` direto por container individual (evita perder a ordem de dependência que o Compose já resolve).

### Modelo de dados — `backend/models/database.py`

```python
class BackupSchedule(Base):
    __tablename__ = "backup_schedule"
    projeto = Column(String, primary_key=True)
    frequencia = Column(String, nullable=False, default="off")  # off | daily | weekly
    hora = Column(Integer, nullable=False, default=3)             # 0-23


class BackupJob(Base):
    __tablename__ = "backup_job"
    id = Column(Integer, primary_key=True, autoincrement=True)
    projeto = Column(String, nullable=False)
    tipo = Column(String, nullable=False)          # snapshot | restore | delete
    arquivo = Column(String, nullable=True)          # nome do snapshot; obrigatório quando tipo=restore ou delete
    status = Column(String, nullable=False, default="pending")  # pending | running | done | failed
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)
    concluido_em = Column(DateTime, nullable=True)
    erro = Column(Text, nullable=True)
    username = Column(String, nullable=False)
```

### Backend — `/api/backups`

Segue o padrão de `api/fail2ban.py`/`api/traefik.py` (`APIRouter(prefix="/api/backups", dependencies=[Depends(verify_token_header)])`).

- `GET /projects` — reaproveita a lógica de agrupamento por `com.docker.compose.project` já existente em `api/projects.py` (extrair um helper compartilhado se fizer sentido no momento do plano). Pra cada projeto, retorna: nome, `schedule` atual (`BackupSchedule`, ou default `off`/`3` se não configurado), lista de snapshots (lidos do diretório `BACKUPS_DIR`, env var default `/opt/vps-monitor-backups`, montado read-only), e o `BackupJob` mais recente não-finalizado (se houver, pra UI mostrar progresso).
- `PUT /projects/{projeto}/schedule` — corpo `{frequencia, hora}`. Upsert em `BackupSchedule`.
- `POST /projects/{projeto}/snapshot` — cria `BackupJob(projeto, tipo="snapshot", status="pending", username=...)`. 409 se já existe um job `pending`/`running` pro mesmo projeto.
- `POST /projects/{projeto}/snapshots/{arquivo}/restore` — cria `BackupJob(projeto, tipo="restore", arquivo=arquivo, status="pending", username=...)`. Mesma trava de 409. 404 se `arquivo` não existir em `BACKUPS_DIR/<projeto>/`.
- `GET /projects/{projeto}/snapshots/{arquivo}/download` — `StreamingResponse` do `.tar.gz` a partir de `BACKUPS_DIR` (read-only). 404 se não existir.
- `DELETE /projects/{projeto}/snapshots/{arquivo}` — remove o arquivo. Como o diretório é read-only pro container, isso não pode ser uma remoção direta de arquivo pelo backend — em vez disso, cria um `BackupJob(tipo="delete", arquivo=...)` que o watcher executa (mesma fila usada pra snapshot/restore). 404 se não existir.

Sanitização: `projeto` e `arquivo` nunca podem conter `/`, `..` ou caminho absoluto — validados com uma checagem simples (`re.fullmatch(r"[a-zA-Z0-9_-]+", valor)`) antes de montar qualquer path, mesmo cuidado já usado no Traefik pro nome de arquivo.

### Script no host — `scripts/backup-worker.sh`

Roda via cron (1x/min). Não precisa de nenhum mount novo pra ler/escrever o SQLite do monitor — o volume `vps-monitor_vps_monitor_data` já é um diretório real no host (`/var/lib/docker/volumes/vps-monitor_vps_monitor_data/_data/monitor.db`), acessado com o binário `sqlite3` (instalar no host se ainda não estiver disponível).

A cada execução:
1. **Processa jobs pendentes**: `SELECT * FROM backup_job WHERE status='pending' ORDER BY criado_em LIMIT 1` (um por vez, pra não sobrecarregar a VPS compartilhada com backups simultâneos). Marca `running`, executa (snapshot/restore/delete conforme `tipo`), marca `done` ou `failed` (com `erro` preenchido).
   - `restore`: mesmo se a extração falhar no meio, tenta `docker compose up -d` antes de marcar `failed` — nunca deixa o projeto do cliente parado indefinidamente por causa de uma falha no script.
2. **Verifica agendamentos**: pra cada linha em `backup_schedule` com `frequencia != 'off'`, calcula se o projeto está "devido" (comparando o snapshot mais recente existente em `BACKUPS_DIR/<projeto>/` com a frequência/hora configurada) e, se sim, e não há job `pending`/`running` pro mesmo projeto, insere um novo `backup_job(tipo='snapshot')`.
3. **Retenção**: após um snapshot `done` com sucesso, apaga os arquivos mais antigos em `BACKUPS_DIR/<projeto>/` além do limite configurado (constante `RETENCAO_PADRAO = 5`, mesmo valor pra todos os projetos nesta versão — sem configuração por projeto). Nunca roda se o snapshot novo falhou (não apaga o último bom).

### Frontend — nova página `/backups`

Segue o padrão visual de `/seguranca`/`/traefik`:

- Lista de projetos (mesmo agrupamento de `/projetos`): nome, dropdown de agendamento (Desligado/Diário/Semanal + seletor de hora), botão "Criar snapshot agora" (desabilitado se já há job `pending`/`running`, com indicação visual do progresso).
- Por projeto, lista de snapshots existentes: data/hora, tamanho, botões **Baixar**, **Restaurar**, **Excluir**.
- Modal de restore: campo obrigatório "digite `<nome-do-projeto>` pra confirmar" — o botão de confirmar só habilita quando o texto digitado bate exatamente com o nome do projeto. Mesmo padrão usado em ferramentas como GitHub/Vercel pra ações destrutivas irreversíveis.
- Modal de exclusão de snapshot: confirmação simples (mesmo padrão já usado em `/seguranca` e `/traefik`).
- Polling leve (a cada 5s, só enquanto existir algum job `pending`/`running` na resposta de `GET /projects`) pra refletir o progresso sem o usuário precisar recarregar a página manualmente.

### Testes

Backend (TDD, mockando filesystem/DB, sem subprocess real — mesmo padrão do Traefik):
- `PUT /projects/{projeto}/schedule`: upsert funciona, valores inválidos de `frequencia` retornam 400.
- `POST /projects/{projeto}/snapshot`: cria job `pending`; 409 se já existe job não-finalizado pro mesmo projeto.
- `POST /projects/{projeto}/snapshots/{arquivo}/restore`: cria job; 404 se arquivo não existe; 409 se já há job pendente.
- `DELETE /projects/{projeto}/snapshots/{arquivo}`: cria job tipo delete; 404 se arquivo não existe.
- Sanitização: nomes de projeto/arquivo com `/`, `..` retornam 400 antes de montar qualquer path.
- `GET /projects`: agrega corretamente schedule + snapshots + job em andamento por projeto.

Frontend: `npm run build` limpo.

Script no host: sem teste automatizado (mesmo caso dos watchers existentes) — verificado manualmente em produção: agendar um projeto de baixo risco, confirmar snapshot criado e retenção funcionando; testar um ciclo completo de restore nesse mesmo projeto de teste antes de liberar a feature pra projetos reais de clientes.

## Arquivos afetados

- **Novo:** `backend/api/backups.py`, `frontend/app/backups/page.tsx`, `scripts/backup-worker.sh`
- **Modificado:** `backend/models/database.py` (`BackupSchedule`, `BackupJob`), `backend/main.py` (registrar router), `docker-compose.yml` (novo mount read-only de `BACKUPS_DIR`), `frontend/app/layout.tsx` (NAV)
- **Novo (testes):** `backend/tests/test_backups_api.py`
