#!/bin/bash
set -e
echo "=== VPS Monitor Deploy ==="
cd /opt/vps-monitor
[ ! -f .env ] && cp .env.example .env && echo "ATENÇÃO: edite o arquivo .env antes de continuar" && exit 1
docker compose build --no-cache
docker compose up -d
echo "=== Deploy concluído ==="
echo "Acesse: https://monitor.dlsistemas.com.br"
