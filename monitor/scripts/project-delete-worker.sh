#!/bin/bash
# Roda no HOST via cron (nao dentro de um container).
#
# monitor-backend so grava "intencoes" (linhas em project_delete_request, no
# SQLite do proprio monitor) — nunca roda `docker stop/rm`, `docker volume rm`
# diretamente, o que exigiria acesso total ao Docker do host (bem alem do que
# o socket-proxy do container libera hoje: so CONTAINERS/POST/DELETE/IMAGES,
# sem VOLUMES). Este script executa a exclusao de verdade a partir do host.
#
# Reaproveita duas filas/watchers ja existentes em vez de duplicar logica:
# - Remocao de rotas Traefik: so apaga o arquivo .yml marcado. O
#   scripts/traefik-dynamic-commit-watcher.sh ja existente detecta a mudanca
#   no proximo ciclo e comita sozinho — nenhuma mudanca necessaria nele.
# - Remocao de regras de firewall: insere linhas em firewall_rule_request
#   (acao=remove) em vez de rodar `ufw` direto — o scripts/firewall-worker.sh
#   ja existente processa essas linhas, com sua propria trava de portas
#   protegidas ja testada (nao duplicamos essa logica aqui).
#
# Nao usa "set -e": precisa continuar apos falha pra marcar o job como
# failed, tratamento de erro explicito em cada etapa. Sem rollback
# automatico — a maioria das acoes (rm de volume, rm de arquivo) nao e
# reversivel de qualquer forma; o erro fica registrado pra investigacao
# manual.
#
# Instalacao do cron (uma vez, fora deste repo):
#   crontab -e
#   * * * * * /opt/vps-monitor/monitor/scripts/project-delete-worker.sh >> /var/log/project-delete-worker.log 2>&1
set -uo pipefail

DB_PATH="/var/lib/docker/volumes/vps-monitor_vps_monitor_data/_data/monitor.db"
TRAEFIK_DYNAMIC_DIR="/opt/traefik/dynamic"
PROJETO_PROTEGIDO="vps-monitor"
LOCK_FILE="/var/lock/project-delete-worker.lock"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$(date -Iseconds) outra execucao do project-delete-worker.sh ja esta em andamento, saindo." >&2
  exit 0
fi

sqlite3_exec() {
  # ".timeout" (dot-command) nao emite linha de saida, diferente de
  # "PRAGMA busy_timeout=...", que contaminaria a captura via $(...) — erro
  # ja cometido e corrigido no backup-worker.sh, nao repetir aqui.
  sqlite3 -cmd ".timeout 5000" "$DB_PATH" "$1"
}

remover_rotas_traefik() {
  local rotas_json="$1"
  echo "$rotas_json" | python3 -c '
import json, re, sys, os

DYNAMIC_DIR = "'"$TRAEFIK_DYNAMIC_DIR"'"
NOME_VALIDO_RE = re.compile(r"^vps-monitor-[a-zA-Z0-9_-]+\.yml$")
arquivos = json.load(sys.stdin)
for nome in arquivos:
    if not NOME_VALIDO_RE.match(nome):
        print(f"Recusado: {nome} nao e um nome de rota valido gerenciada pelo monitor.")
        continue
    caminho = os.path.join(DYNAMIC_DIR, nome)
    if os.path.isfile(caminho):
        os.remove(caminho)
        print(f"Rota removida: {caminho}")
'
}

enfileirar_remocoes_firewall() {
  local regras_json="$1"
  echo "$regras_json" | python3 -c '
import json, sys

PORTAS_PROTEGIDAS = {22, 80, 443}
regras = json.load(sys.stdin)
for r in regras:
    porta = r["porta"]
    if porta in PORTAS_PROTEGIDAS:
        continue
    protocolo = r["protocolo"]
    protocolo_escapado = protocolo.replace("\x27", "\x27\x27")
    permitir = 1 if r["permitir"] else 0
    origem = r.get("origem_ip")
    if origem:
        origem_escapado = origem.replace("\x27", "\x27\x27")
        origem_sql = "\x27" + origem_escapado + "\x27"
    else:
        origem_sql = "NULL"
    print(
        "INSERT INTO firewall_rule_request "
        "(acao, permitir, porta, protocolo, origem_ip, status, criado_em, username) VALUES "
        "(\x27remove\x27, " + str(permitir) + ", " + str(porta) + ", \x27" + protocolo_escapado + "\x27, "
        + origem_sql + ", \x27pending\x27, datetime(\x27now\x27), \x27project-delete-worker\x27);"
    )
' | while IFS= read -r stmt; do
    sqlite3_exec "$stmt"
  done
}

fazer_delete_projeto() {
  local projeto="$1"
  local rotas_json="$2"
  local regras_json="$3"

  if [ "$projeto" = "$PROJETO_PROTEGIDO" ]; then
    echo "Recusado: projeto '$PROJETO_PROTEGIDO' e protegido, nunca excluido via worker." >&2
    return 1
  fi

  local containers_raw
  containers_raw=$(docker ps -a --filter "label=com.docker.compose.project=$projeto" --format '{{.Names}}')
  if [ -z "$containers_raw" ]; then
    echo "Nenhum container encontrado para o projeto '$projeto'" >&2
    return 1
  fi
  local containers_array=()
  mapfile -t containers_array <<< "$containers_raw"

  if ! docker stop "${containers_array[@]}"; then
    echo "Falha ao parar containers de '$projeto'" >&2
    return 1
  fi

  local falhou=0

  remover_rotas_traefik "$rotas_json"
  if [ "${PIPESTATUS[0]}" -ne 0 ]; then
    echo "AVISO: falha ao remover rotas Traefik" >&2
    falhou=1
  fi

  enfileirar_remocoes_firewall "$regras_json"
  if [ "${PIPESTATUS[0]}" -ne 0 ]; then
    echo "AVISO: falha ao enfileirar remocoes de firewall" >&2
    falhou=1
  fi

  local volumes
  volumes=$(for c in "${containers_array[@]}"; do
    docker inspect "$c" --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{"\n"}}{{end}}{{end}}'
  done | sort -u)

  if ! docker rm "${containers_array[@]}"; then
    echo "AVISO: falha ao remover containers de '$projeto'" >&2
    return 1
  fi

  if [ -n "$volumes" ]; then
    while IFS= read -r vol; do
      [ -z "$vol" ] && continue
      if ! docker volume rm "$vol"; then
        echo "AVISO: falha ao remover volume '$vol'" >&2
        falhou=1
      fi
    done <<< "$volumes"
  fi

  if [ "$falhou" -eq 1 ]; then
    return 1
  fi

  echo "Projeto '$projeto' excluido: containers e volumes removidos, rotas/regras marcadas processadas."
}

# ---------- 0. Libera jobs presos (worker interrompido no meio de uma execucao) ----------
# 30 minutos: mais generoso que o do firewall (aplicar regra e quase
# instantaneo) mas bem menor que o do backup (2h) — remocao de volumes
# grandes pode demorar, mas nao deveria levar tanto quanto uma copia completa
# de backup.
sqlite3_exec "UPDATE project_delete_request SET status='failed', concluido_em=datetime('now'), erro='Job travado em running por mais de 30min - worker provavelmente interrompido.' WHERE status='running' AND criado_em < datetime('now', '-30 minutes');"

# ---------- 1. Processa no maximo um pedido pendente por execucao ----------
job_linha=$(sqlite3_exec "SELECT id, projeto, rotas_traefik_selecionadas, regras_firewall_selecionadas FROM project_delete_request WHERE status='pending' ORDER BY criado_em LIMIT 1;")

if [ -n "$job_linha" ]; then
  IFS='|' read -r job_id job_projeto job_rotas job_regras <<< "$job_linha"

  sqlite3_exec "UPDATE project_delete_request SET status='running' WHERE id=$job_id;"

  if saida=$(fazer_delete_projeto "$job_projeto" "$job_rotas" "$job_regras" 2>&1); then
    sqlite3_exec "UPDATE project_delete_request SET status='done', concluido_em=datetime('now') WHERE id=$job_id;"
    echo "$saida"
  else
    erro_escapado=$(echo "$saida" | sed "s/'/''/g" | tr '\n' ' ')
    sqlite3_exec "UPDATE project_delete_request SET status='failed', concluido_em=datetime('now'), erro='$erro_escapado' WHERE id=$job_id;"
    echo "$saida" >&2
  fi
fi
