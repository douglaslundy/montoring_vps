# Backlog — VPS Monitor

Ordem definida pelo usuário em 2026-08-16. Fazer uma de cada vez, na ordem.

---

## 0. Alertas de load: flapping + aviso na subida + histórico de picos — ✅ CONCLUÍDA (2026-08-16)
Implementada, revisada task a task, 322 testes passando, deployada em produção, e os dois scripts do host removidos com backup. Detalhes e medições em `PROGRESSO.md`.

Inclui uma Task 8 extra, fora do plano original, autorizada após a revisão final: a regra "Container Parado" também passou a exigir janela sustentada (2 min), fechando ~64% de um ruído independente (425 alertas em 30 dias, 273 deles resolvidos em menos de 2 min — deploys, não incidentes).

Resta só a verificação de 24h (contagem de alertas e ausência de alerta silencioso no `alert_log`) e a conferência visual da `/historico` pelo usuário.

### Contexto necessário para a próxima tarefa (item 1, syscursos)
Nada do trabalho de alertas acima é necessário. O que importa levar:
- O degrau de RAM de 2026-08-13 14h UTC e a coincidência no minuto com o restart de `supabase-syscursos-realtime-1` (`2026-08-13T14:04:55Z`) — ver `PROGRESSO.md`.
- Crescimento por container 13/08→15/08: `syscursos-kong` 522→704 MB, `syscursos-realtime` 130→228 MB, `syscursos-storage` 60→112 MB, `syscursos-db` 66→98 MB.
- O usuário não consegue fazer login no syscursos.

---

## 1. syscursos: engordou e recebe requisições sem ninguém acessar — e não loga
**Sintoma relatado:** o usuário não consegue fazer login no syscursos; ele "nem está funcionando". Mesmo assim a stack cresceu ~360 MB de RAM e aparece consumindo.

**Evidência já coletada nesta sessão (não investigada a fundo ainda):**
- Degrau permanente de RAM começando **2026-08-13 14h UTC**, coincidindo no minuto com o restart de `supabase-syscursos-realtime-1` (`2026-08-13T14:04:55Z`, `RestartCount=1`).
- Crescimento por container entre 13/08 e 15/08: `supabase-syscursos-kong-1` 522→704 MB, `realtime-1` 130→228 MB, `storage-1` 60→112 MB, `db-1` 66→98 MB.
- `supabase-syscursos-kong-1` é um dos maiores consumidores de CPU da VPS (3,6% contínuo).

**Hipóteses a testar (não confirmadas):** loop de retry do `realtime` (websocket reconectando sem parar) gerando requisições internas contra o Kong; ou algo externo martelando a API. Verificar logs do `realtime` e do `kong`, e se o crescimento de memória é vazamento ou só cache.

---

## 2. Container do xadrez parado
Investigar qual container da stack `xadrez-essencial-*` está parado, por quê (exit code / OOM), e se deve subir de volta ou ser removido.

Nota: em 16/08 19:02 UTC houve um alerta `container_stopped` que resolveu sozinho em 28s — era o `corridas-app` (recriado 16:02 local), provavelmente não relacionado.

---

## 3. Requisições diárias a subdomínios que não existem (ex: `zap.dlsistemas.com.br`)
**Pedido:** entender de onde vêm e resolver "de uma vez por todas".

**Contexto que já existe no projeto:** já houve trabalho de "bloqueio de subdomínios fantasmas no mecanicapro" (ver `PROGRESSO.md`, histórico) e existe um jail `mecanicapro-ghost-subdomain` no fail2ban. Avaliar se a solução certa é generalizar isso para todo o domínio via Traefik (regra catch-all que rejeita host desconhecido) em vez de jail por projeto.

Fontes de dados já disponíveis: tabela `access_log` do monitor (host, IP, path), página `/acessos`, e o access log do Traefik em `/var/log/traefik`.
