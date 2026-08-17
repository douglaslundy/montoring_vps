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

## 1. syscursos — INVESTIGADO 2026-08-16. Duas das três queixas não eram problema; a terceira é real.

### (a) "Engordou ~360 MB" — NÃO É PROBLEMA. Hipótese anterior refutada.
A memória não cresceu: **saiu do swap e voltou para a RAM**. No momento exato do restart (13/08 14h UTC):

| hora | RAM | swap | soma |
|---|---|---|---|
| 13h | 5.660 MB | 2.972 MB | 8.633 MB |
| **14h** | **5.842 MB** | **2.365 MB** | **8.208 MB** |

A soma RAM+swap **caiu** 425 MB e ficou estável (8.520 → 8.561 na janela toda). Os containers estavam parados havia semanas, o kernel tinha paginado partes deles para o swap, e o restart trouxe tudo de volta para a RAM.

Confirmado que é **degrau, não vazamento**: kong 488 → ~700 MB em 13/08 e **estável em 695-707 desde então** (4 dias); realtime 55 → 227 MB, estável em 226-228. Nada continua crescendo.

**Correção do registro anterior:** o `PROGRESSO.md` dizia "degrau permanente de ~1 GB de RAM / containers engordaram". O mecanismo descrito (menos folga de RAM → o `drop_caches` horário dói mais) continua válido, mas a causa não era consumo novo — era swap-in. Consumo total nunca subiu.

### (b) "Recebe requisições sem ninguém acessar" — é healthcheck interno.
`supabase-syscursos-supavisor-1` faz `HEAD /api/health` **a cada 10 segundos** — ~8.640 requisições/dia, todas internas, nenhuma vinda da internet. É o que enche os 5 MB de log dele (o maior da stack). Comportamento normal do Supavisor, não é tráfego de usuário.

### (c) "Não consigo fazer login" — o login FUNCIONA. O problema é outro. **AÇÃO PENDENTE.**
No log inteiro do `auth`: **88 logins bem-sucedidos** com `douglaslundy100@gmail.com`, **41** com `douglaslundy@gmail.com`, e apenas **6 falhas de credencial**. O último sucesso foi **2026-08-17 00:05 UTC (16/08 21:05 local)**. O GoTrue está saudável.

**O defeito real:** `x_forwarded_proto: "http"` em **216 de 216** requisições. Nenhuma chega ao auth como `https`. Traefik/Kong não estão propagando o protocolo. Consequências: links de e-mail (confirmação, reset de senha) são gerados com `http://`, cookies `Secure` podem ser recusados pelo navegador, e redirect/callback quebram. O padrão visto no log — login `200` seguido de logout `204` ~6 segundos depois, repetidamente — é consistente com "autentica mas a sessão não gruda".

Aviso correlato, repetido no log: `GOTRUE_MAILER_EXTERNAL_HOSTS` não inclui `supabase-syscursos.circuitodascorridas.com.br`.

**Correção provável** (não aplicada — stack de outro projeto, precisa de autorização): garantir `X-Forwarded-Proto: https` do Traefik até o Kong, e setar `GOTRUE_MAILER_EXTERNAL_HOSTS` / `API_EXTERNAL_URL` com o domínio real em https. **Antes de mexer: confirmar com o usuário qual é o sintoma exato que ele vê no navegador** — a correção muda conforme seja "não entra", "entra e cai", ou "link do e-mail não abre".
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
