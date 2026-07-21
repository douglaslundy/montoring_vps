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
FIREWALL_STATE_FILE="/opt/vps-monitor-firewall/state.json"
mkdir -p "$(dirname "$FIREWALL_STATE_FILE")"
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
