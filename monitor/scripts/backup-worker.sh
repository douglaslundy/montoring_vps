#!/bin/bash
# Roda no HOST via cron (nao dentro de um container).
#
# monitor-backend so grava "intencoes" (linhas em backup_job/backup_schedule,
# no SQLite do proprio monitor) — nunca acessa diretamente volumes ou
# diretorios de outros projetos, o que exigiria montar /opt e
# /var/lib/docker/volumes inteiros no container (expondo segredos de todos
# os ~7 clientes da VPS de uma vez). Este script executa o trabalho de
# verdade a partir do host, onde tem acesso total: para os containers do
# projeto-alvo, copia volumes + diretorio de trabalho, sobe de novo.
#
# Le/escreve o SQLite do monitor direto no path do volume Docker no host
# (nao precisa de mount novo, e so um arquivo real no disco). O monitor ja
# roda em modo WAL (PRAGMA journal_mode=WAL, ver models/database.py),
# seguro para acesso concorrente de multiplos processos no mesmo host.
#
# Nao usa "set -e": o script precisa continuar apos uma falha (pra marcar o
# job como failed e tentar subir os containers de novo), entao os erros sao
# tratados explicitamente em cada funcao, nunca abortando o script inteiro.
#
# Pre-requisito (uma vez, fora deste repo): apt-get install -y sqlite3
#
# Instalacao do cron (uma vez, fora deste repo):
#   crontab -e
#   * * * * * /opt/vps-monitor/monitor/scripts/backup-worker.sh >> /var/log/backup-worker.log 2>&1
set -uo pipefail

DB_PATH="/var/lib/docker/volumes/vps-monitor_vps_monitor_data/_data/monitor.db"
BACKUPS_DIR="/opt/vps-monitor-backups"
RETENCAO_PADRAO=5
LOCK_FILE="/var/lock/backup-worker.lock"

mkdir -p "$BACKUPS_DIR"

# Impede que duas execucoes do cron rodem ao mesmo tempo (ex: um snapshot
# demorado ainda em andamento quando o minuto seguinte dispara de novo) —
# sem isso, dois projetos diferentes poderiam ter seus containers parados
# simultaneamente, multiplicando o downtime e a carga de I/O na VPS
# compartilhada. A proxima execucao do cron simplesmente sai e tenta de
# novo no minuto seguinte.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date -Iseconds) outra execucao do backup-worker.sh ja esta em andamento, saindo." >&2
  exit 0
fi

# $projeto so chega aqui depois de validado por _validar_nome() na API
# (regex ^[a-zA-Z0-9_-]+$), entao a interpolacao direta nas queries abaixo
# nunca contem aspas nem caracteres especiais de SQL.
sqlite3_exec() {
  # ".timeout" e um dot-command (nao emite linha de saida), diferente de
  # "PRAGMA busy_timeout=...", que imprime o valor como se fosse uma linha
  # de resultado — isso contaminaria toda captura via $(...) neste script
  # (ex: job_linha, pendente), fazendo o worker interpretar "5000" como
  # dado real. Descoberto e corrigido durante a revisao final.
  sqlite3 -cmd ".timeout 5000" "$DB_PATH" "$1"
}

fazer_snapshot() {
  local projeto="$1"
  local containers
  containers=$(docker ps -a --filter "label=com.docker.compose.project=$projeto" --format '{{.Names}}')
  if [ -z "$containers" ]; then
    echo "Nenhum container encontrado para o projeto '$projeto'" >&2
    return 1
  fi

  local primeiro working_dir
  primeiro=$(echo "$containers" | head -1)
  working_dir=$(docker inspect "$primeiro" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}')
  if [ -z "$working_dir" ] || [ ! -d "$working_dir" ]; then
    echo "working_dir invalido para o projeto '$projeto': '$working_dir'" >&2
    return 1
  fi

  local volumes
  volumes=$(for c in $containers; do
    docker inspect "$c" --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{"\n"}}{{end}}{{end}}'
  done | sort -u)

  local staging
  staging=$(mktemp -d)
  mkdir -p "$staging/volumes"

  local falhou=0
  if ! (cd "$working_dir" && docker compose stop); then
    echo "Falha ao parar containers de '$projeto'" >&2
    falhou=1
  fi

  if [ "$falhou" -eq 0 ]; then
    if ! cp -a "$working_dir" "$staging/workdir"; then
      falhou=1
    fi

    if [ "$falhou" -eq 0 ] && [ -n "$volumes" ]; then
      while IFS= read -r vol; do
        [ -z "$vol" ] && continue
        local vol_path="/var/lib/docker/volumes/$vol/_data"
        if [ -d "$vol_path" ]; then
          mkdir -p "$staging/volumes/$vol"
          if ! cp -a "$vol_path/." "$staging/volumes/$vol/"; then
            falhou=1
          fi
        fi
      done <<< "$volumes"
    fi
  fi

  # Sempre tenta subir os containers de novo, mesmo que o stop ou a copia
  # tenham falhado — nunca deixar um projeto de cliente parado por causa de
  # uma falha do script (mesma garantia que fazer_restore ja tinha).
  if ! (cd "$working_dir" && docker compose up -d); then
    echo "AVISO: falha ao subir containers de '$projeto' apos snapshot" >&2
    falhou=1
  fi

  if [ "$falhou" -ne 0 ]; then
    rm -rf "$staging"
    echo "Falha ao gerar snapshot do projeto '$projeto'" >&2
    return 1
  fi

  local destino_dir="$BACKUPS_DIR/$projeto"
  mkdir -p "$destino_dir"
  local timestamp arquivo_final
  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
  arquivo_final="$destino_dir/${timestamp}.tar.gz"

  if ! tar -czf "$arquivo_final" -C "$staging" .; then
    rm -rf "$staging" "$arquivo_final"
    echo "Falha ao compactar snapshot do projeto '$projeto'" >&2
    return 1
  fi

  rm -rf "$staging"
  echo "Snapshot criado: $arquivo_final"
}

fazer_restore() {
  local projeto="$1"
  local arquivo="$2"
  local origem="$BACKUPS_DIR/$projeto/$arquivo"

  if [ ! -f "$origem" ]; then
    echo "Snapshot '$arquivo' nao encontrado para o projeto '$projeto'" >&2
    return 1
  fi

  local containers
  containers=$(docker ps -a --filter "label=com.docker.compose.project=$projeto" --format '{{.Names}}')
  if [ -z "$containers" ]; then
    echo "Nenhum container encontrado para o projeto '$projeto'" >&2
    return 1
  fi
  local primeiro working_dir
  primeiro=$(echo "$containers" | head -1)
  working_dir=$(docker inspect "$primeiro" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}')
  if [ -z "$working_dir" ] || [ ! -d "$working_dir" ]; then
    echo "working_dir invalido para o projeto '$projeto': '$working_dir'" >&2
    return 1
  fi

  local staging
  staging=$(mktemp -d)
  if ! tar -xzf "$origem" -C "$staging"; then
    rm -rf "$staging"
    echo "Falha ao extrair snapshot '$arquivo'" >&2
    return 1
  fi

  local falhou=0
  if ! (cd "$working_dir" && docker compose stop); then
    echo "Falha ao parar containers de '$projeto' antes do restore" >&2
    falhou=1
  fi

  if [ "$falhou" -eq 0 ] && [ -d "$staging/workdir" ]; then
    if ! rsync -a --delete "$staging/workdir/" "$working_dir/"; then
      falhou=1
    fi
  fi

  if [ "$falhou" -eq 0 ] && [ -d "$staging/volumes" ]; then
    for vol_dir in "$staging/volumes"/*/; do
      [ -d "$vol_dir" ] || continue
      local vol_nome vol_path
      vol_nome=$(basename "$vol_dir")
      vol_path="/var/lib/docker/volumes/$vol_nome/_data"
      if [ -d "$vol_path" ]; then
        if ! rsync -a --delete "$vol_dir" "$vol_path/"; then
          falhou=1
        fi
      fi
    done
  fi

  rm -rf "$staging"

  if ! (cd "$working_dir" && docker compose up -d); then
    echo "AVISO: falha ao subir containers de '$projeto' apos restore" >&2
    falhou=1
  fi

  if [ "$falhou" -ne 0 ]; then
    echo "Restore do projeto '$projeto' teve falhas (ver mensagens acima)" >&2
    return 1
  fi

  echo "Restore concluido para '$projeto' a partir de '$arquivo'"
}

fazer_delete() {
  local projeto="$1"
  local arquivo="$2"
  local caminho="$BACKUPS_DIR/$projeto/$arquivo"
  if [ ! -f "$caminho" ]; then
    echo "Snapshot '$arquivo' nao encontrado para o projeto '$projeto'" >&2
    return 1
  fi
  rm -f "$caminho"
  echo "Snapshot removido: $caminho"
}

aplicar_retencao() {
  local projeto="$1"
  local destino_dir="$BACKUPS_DIR/$projeto"
  local total
  total=$(ls -1 "$destino_dir"/*.tar.gz 2>/dev/null | wc -l)
  if [ "$total" -gt "$RETENCAO_PADRAO" ]; then
    ls -1t "$destino_dir"/*.tar.gz | tail -n "+$((RETENCAO_PADRAO + 1))" | while IFS= read -r antigo; do
      rm -f "$antigo"
      echo "Retencao: removido snapshot antigo $antigo"
    done
  fi
}

# ---------- 0. Libera jobs presos (worker interrompido no meio de uma execucao) ----------
# Se o worker morreu depois de marcar um job como "running" mas antes de
# concluir (reboot do host, cron matado, etc.), o job ficaria "running" pra
# sempre — bloqueando o projeto (409 em qualquer tentativa nova) e sendo
# pulado silenciosamente pelo agendamento. 2h e um limite bem generoso dado
# que snapshots hoje levam segundos/minutos (volumes pequenos).
sqlite3_exec "UPDATE backup_job SET status='failed', concluido_em=datetime('now'), erro='Job travado em running por mais de 2h - provavelmente o worker foi interrompido (reboot, cron matado) no meio da execucao.' WHERE status='running' AND criado_em < datetime('now', '-2 hours');"

# ---------- 1. Processa no maximo um job pendente por execucao ----------
job_linha=$(sqlite3_exec "SELECT id, projeto, tipo, IFNULL(arquivo, '') FROM backup_job WHERE status='pending' ORDER BY criado_em LIMIT 1;")

if [ -n "$job_linha" ]; then
  IFS='|' read -r job_id job_projeto job_tipo job_arquivo <<< "$job_linha"

  sqlite3_exec "UPDATE backup_job SET status='running' WHERE id=$job_id;"

  saida=""
  sucesso=1
  case "$job_tipo" in
    snapshot)
      if ! saida=$(fazer_snapshot "$job_projeto" 2>&1); then sucesso=0; fi
      ;;
    restore)
      if ! saida=$(fazer_restore "$job_projeto" "$job_arquivo" 2>&1); then sucesso=0; fi
      ;;
    delete)
      if ! saida=$(fazer_delete "$job_projeto" "$job_arquivo" 2>&1); then sucesso=0; fi
      ;;
    *)
      saida="Tipo de job desconhecido: $job_tipo"
      sucesso=0
      ;;
  esac

  echo "$saida"

  if [ "$sucesso" -eq 1 ]; then
    sqlite3_exec "UPDATE backup_job SET status='done', concluido_em=datetime('now') WHERE id=$job_id;"
    if [ "$job_tipo" = "snapshot" ]; then
      aplicar_retencao "$job_projeto"
    fi
  else
    erro_escapado=$(echo "$saida" | sed "s/'/''/g" | tr '\n' ' ')
    sqlite3_exec "UPDATE backup_job SET status='failed', concluido_em=datetime('now'), erro='$erro_escapado' WHERE id=$job_id;"
  fi
fi

# ---------- 2. Verifica agendamentos ----------
sqlite3_exec "SELECT projeto, frequencia, hora FROM backup_schedule WHERE frequencia != 'off';" | while IFS='|' read -r projeto frequencia hora; do
  [ -z "$projeto" ] && continue

  pendente=$(sqlite3_exec "SELECT COUNT(*) FROM backup_job WHERE projeto='$projeto' AND status IN ('pending','running');")
  if [ "$pendente" -gt 0 ]; then
    continue
  fi

  ultimo_snapshot_epoch=0
  destino_dir="$BACKUPS_DIR/$projeto"
  if [ -d "$destino_dir" ]; then
    ultimo_arquivo=$(ls -1t "$destino_dir"/*.tar.gz 2>/dev/null | head -1)
    if [ -n "$ultimo_arquivo" ]; then
      ultimo_snapshot_epoch=$(date -r "$ultimo_arquivo" +%s)
    fi
  fi

  agora_epoch=$(date +%s)
  agora_hora=$(date +%H)
  intervalo_segundos=86400
  if [ "$frequencia" = "weekly" ]; then
    intervalo_segundos=604800
  fi

  if [ "$((10#$agora_hora))" -eq "$hora" ] && [ $((agora_epoch - ultimo_snapshot_epoch)) -ge "$intervalo_segundos" ]; then
    sqlite3_exec "INSERT INTO backup_job (projeto, tipo, status, criado_em, username) VALUES ('$projeto', 'snapshot', 'pending', datetime('now'), 'agendado');"
  fi
done
