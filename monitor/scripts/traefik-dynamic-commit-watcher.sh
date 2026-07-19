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
