# VPS Monitor

Painel web de monitoramento para servidor Linux com Docker.
Acesse: https://monitor.dlsistemas.com.br

## Pré-requisitos

- Docker 24+ e Docker Compose v2+
- Traefik rodando na rede Docker `proxy` com certresolver `letsencrypt`
- (Opcional) Evolution API auto-hospedada para notificações WhatsApp

## Instalação

```bash
cd /opt
git clone <repo> vps-monitor
cd vps-monitor
cp .env.example .env
nano .env   # defina JWT_SECRET, MONITOR_USER, MONITOR_PASSWORD, PUBLIC_URL
bash deploy.sh
```

## Configuração do Domínio

O Traefik detecta automaticamente o container `monitor-nginx` na rede `proxy`.
Certifique-se de que o DNS de `monitor.dlsistemas.com.br` aponta para o IP da VPS.

## SMTP (E-mail)

Configure em Configurações > SMTP no painel. Teste com "Enviar e-mail de teste".

## WhatsApp (Evolution API)

1. Configure URL da API, API Key e nome da instância em Configurações > WhatsApp
2. Clique "Criar Instância" → depois "Conectar (QR)"
3. Escaneie o QR code com o WhatsApp do celular

## Regras de Alerta

Em Alertas > Regras, 9 regras padrão já estão configuradas.
Edite thresholds ou adicione novas regras conforme necessário.

## Troubleshooting

**Container não inicia:** `docker compose logs monitor-backend`
**Métricas zeradas:** verifique se `/proc` e `/sys` estão montados (`docker compose exec monitor-backend ls /host/proc`)
**WebSocket não conecta:** verifique o nginx.conf e os headers de Upgrade
**WhatsApp QR expira rápido:** normal — o sistema solicita novo QR automaticamente
