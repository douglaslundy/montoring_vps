#!/bin/bash
# Roda no HOST via cron (nao dentro de um container).
#
# O fail2ban-client, quando invocado de dentro do container monitor-backend,
# valida do lado do cliente os logpaths de TODOS os jails configurados (nao
# so o que esta sendo criado/editado) — e o container nao enxerga arquivos
# de log de outros projetos (ex: /opt/mecanicapro/..., /var/log/auth.log),
# entao "fail2ban-client reload" falha mesmo com o fail2ban saudavel.
# Este script detecta mudancas nos jails gerenciados pelo monitor
# (vps-monitor-*.local) e aplica o reload real a partir do host, onde ele
# enxerga tudo.
#
# Instalacao (uma vez, fora deste repo):
#   crontab -e
#   * * * * * /opt/vps-monitor/monitor/scripts/fail2ban-reload-watcher.sh >> /var/log/fail2ban-reload-watcher.log 2>&1
set -euo pipefail

JAIL_DIR="/etc/fail2ban/jail.d"
STATE_FILE="/opt/vps-monitor/.fail2ban-managed-state"

current_state=$(ls -la "$JAIL_DIR"/vps-monitor-*.local 2>/dev/null || true)

if [ ! -f "$STATE_FILE" ]; then
  echo "$current_state" > "$STATE_FILE"
  exit 0
fi

previous_state=$(cat "$STATE_FILE")

if [ "$current_state" != "$previous_state" ]; then
  fail2ban-client reload
  echo "$current_state" > "$STATE_FILE"
  echo "$(date -Iseconds) reload aplicado (mudanca detectada em jails vps-monitor-*)"
fi
