#!/bin/bash
set -e
echo "=== VPS Monitor Deploy ==="
cd /opt/vps-monitor/monitor
[ ! -f .env ] && cp .env.example .env && echo "ATENÇÃO: edite o arquivo .env antes de continuar" && exit 1
docker compose build --no-cache
docker compose up -d
# monitor-nginx guarda em cache o IP interno do monitor-frontend/monitor-backend.
# Como o compose acima so recria os containers que mudaram, o nginx pode ficar
# com um IP antigo apos o rebuild, causando 502. Reinicia-lo garante que ele
# resolva o DNS de novo para os containers recem-criados.
docker compose restart monitor-nginx
echo "=== Deploy concluído ==="
echo "Acesse: https://monitor.dlsistemas.com.br"
