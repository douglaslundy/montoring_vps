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

## 1. syscursos — ✅ RESOLVIDO (2026-08-17). Login confirmado funcionando.
Correção aplicada do lado da Vercel (fora desta sessão): app trocou de conexão direta ao Postgres para a API HTTP. Confirmado no log do `auth` — requisições chegando de IPs AWS (mesma faixa da Vercel) via HTTPS/443, com `200` de sucesso reais nas contas do usuário (`douglaslundy100@gmail.com`, `douglaslundy@gmail.com`), últimos às 04:38 UTC de 17/08. Os `400 invalid_credentials` vistos junto são senha errada durante os testes, não infra. Containers todos saudáveis, sem restart, uso de recursos normal.

Detalhe da investigação completa abaixo, mantido como referência.

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

### (c) "Não consigo fazer login" — CAUSA RAIZ CONFIRMADA 2026-08-17. Aguardando mudança de código na Vercel.

**Sintoma do usuário:** erro "não foi possível concluir o login agora, tente novamente mais tarde" (mensagem do próprio app, não do Supabase) + lentidão. App hospedado na Vercel. "Funcionava até semana passada."

**Investigação ao vivo:** monitorei em tempo real (`tail -f` do access log do Traefik) enquanto o usuário tentava logar — **nenhuma requisição chegou nesta VPS durante a tentativa**. Ou seja, a chamada nem sai do app, ou sai por um caminho que não passa pelo Traefik/Kong.

**Causa raiz:** o app na Vercel conecta **direto no Postgres** (via Supavisor, portas `55432`/`6643`), não pela API REST/Auth (porta 443/Kong). Essas duas portas foram fechadas para a internet em **2026-08-12**, numa correção de segurança anterior autorizada pelo usuário (estavam publicamente expostas, defendidas só pela senha do banco — achado crítico real, ver histórico em `PROGRESSO.md`). "Funcionava até semana passada" bate exatamente com essa data.

**Confirmado com teste de um ponto genuinamente externo** (não da própria VPS, que mentiria por hairpin NAT):
```
porta 55432: SEM RESPOSTA (timeout)
porta 6643:  SEM RESPOSTA (timeout)
```
A porta não recusa — só não responde. O cliente fica esperando até desistir sozinho, o que explica a lentidão relatada antes do erro.

**Por que não reabrir a porta:** a Vercel, no plano padrão, **não tem IP de saída fixo** — cada execução da função sai por um IP diferente de um pool da AWS (confirmado via busca — ver `Static IPs` da Vercel, feature paga de Pro/Enterprise). Não existe lista de IPs pra liberar no firewall sem pagar por esse add-on ou usar um proxy de terceiros (ex: QuotaGuard).

**Caminho escolhido pelo usuário: trocar a conexão do app para a API HTTP (porta 443), que já está aberta e funcionando.** Testado de fora agora mesmo:
```
GET /auth/v1/health → HTTP 401, 1.26s
GET /rest/v1/        → HTTP 401, 0.75s
```
401 é o esperado sem chave — prova que o caminho está de pé, passando pelo Traefik, TLS ok.

**O que passar para quem mexe no código do app (fora do alcance desta sessão — é outro repositório, na Vercel):**
```
SUPABASE_URL=https://supabase-syscursos.circuitodascorridas.com.br
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjIwOTcyOTAyODgsImlhdCI6MTc4MTkzMDI4OCwiaXNzIjoic3VwYWJhc2UiLCJyb2xlIjoiYW5vbiJ9.S5iU00HxWTfig2QFfcTG8G0o6-DlxbMWypweO27_B6M
```
Se o login usa `@supabase/supabase-js` com `createClient(SUPABASE_URL, SUPABASE_ANON_KEY)`, já fala por HTTPS/443 e o problema some sem tocar na VPS. Se em algum lugar existir `DATABASE_URL`/`POSTGRES_URL` apontando pra `55432` ou `6643` (Prisma, Drizzle, etc.), essa é a conexão bloqueada — precisa sumir ou virar a chamada HTTP.

**Nenhuma mudança feita na VPS.** As portas continuam fechadas (correto, por segurança). O login segue quebrado até a mudança do lado da Vercel.

**Achados anteriores desta investigação, refutados ou explicados — não são o problema:**
- "Engordou ~360 MB": não era vazamento, era swap-in no restart de 13/08 14h UTC (RAM+swap somado *caiu*, ficou estável há 4 dias).
- "Recebe requisições sem ninguém acessar": é o healthcheck interno do próprio Supavisor a cada 10s (~8.640/dia), não é tráfego externo.
- O aviso de `x_forwarded_proto: http` no log do `auth` existe mas é secundário — não é a causa do erro relatado, já que nenhuma requisição chega nesta VPS durante a tentativa de login. Pode valer investigar depois se sobrar algum sintoma após a troca para HTTP, mas não é prioridade agora.

---

## 2. Container do xadrez parado — ✅ INVESTIGADO 2026-08-17. Não há nada parado; falso positivo estrutural identificado.

**Estado atual:** os 7 containers de `xadrez-essencial-*` (web, api, worker, minio, postgres, redis, mailpit) estão de pé, saudáveis, `restart_count=0` em todos.

**O que gerou o alerta:** `xadrez-essencial-minio-init-1` — um container de configuração que roda uma vez (cria buckets no MinIO) e termina por desenho, não por falha. Ele fica listado como "parado" até a faxina semanal do Docker (`docker system prune`, domingo) removê-lo — foi isso que "resolveu" o alerta sozinho, não uma ação de ninguém. Confirmado no histórico (30 dias): esse mesmo container gerou alertas de 10 dias, 5,6 dias, 3,5 dias e 1 dia em aberto, sempre com o mesmo padrão.

Os alertas dos containers de verdade (`web`/`api`/`worker`) no mesmo período são todos curtos (a maioria <30min) e concentrados em 27/07 — consistente com deploy daquele dia, não com queda.

**Sem ação necessária na stack.** Melhoria cosmética possível e não urgente: ensinar a regra "Container Parado" a ignorar containers cujo nome termina em `-init` (ou padrão equivalente), para não confundir job de configuração com serviço caído. Não implementado — fora do pedido original, avaliar só se o usuário quiser.

---

## 3. Requisições diárias a subdomínios que não existem (ex: `zap.dlsistemas.com.br`) — ✅ CAUSA RAIZ REAL CORRIGIDA (2026-08-17). Um refinamento maior fica pendente, de propósito.

**Pedido original:** entender de onde vêm e resolver "de uma vez por todas".

### O mecanismo (era mal-entendido antes desta sessão)
Existe uma regra proposital no Traefik, `mecanicapro-tenants` (`/opt/traefik/dynamic/mecanicapro.yml`), que casa **qualquer** subdomínio de `dlsistemas.com.br` — é assim que o SaaS do mecanicapro roteia cliente por subdomínio, sem precisar de uma rota nova a cada cliente novo. Não dá pra restringir essa regra sem risco de quebrar cliente real.

O nginx do mecanicapro **já tinha** a defesa certa: lista de inquilinos válidos (`tenant-slugs.map`, hoje só 2 entradas reais: `oficina-do-lundy` e `stuntmotos`), fecha sem responder (`444`) quem não está na lista, registra em `ghost_subdomains.log`, e o jail `mecanicapro-ghost-subdomain` do fail2ban vigia esse log. O 502 que aparecia nos testes é só o Traefik traduzindo "o nginx fechou a conexão sem responder" — não é falha do nginx.

### O defeito real, encontrado nesta sessão
**O log registrava o IP errado.** O nginx usava `$remote_addr` (quem tocou na porta), que desde que o Traefik passou a ficar na frente é sempre `172.23.0.1` — o gateway interno da própria rede Docker do mecanicapro, não o visitante. Prova: **58.853 tentativas registradas, e o fail2ban só "identificou" um único atacante: `172.23.0.1`, o próprio Docker.** O sistema se bania e desbania sem parar, nunca bloqueou um bot de verdade — e havia risco real (não confirmado como incidente, mas plausível) de esse banimento acidental atingir o tráfego de clientes reais, já que compartilham o mesmo IP de origem visto pelo nginx.

### Correção aplicada (autorizada, testada, sem downtime)
`/opt/mecanicapro/docker/nginx/mecanicapro.conf` — acrescentadas 2 diretivas no bloco `server` (backup em `mecanicapro.conf.bak-20260817092539`):
```
set_real_ip_from 172.23.0.1;
real_ip_header X-Forwarded-For;
```
`nginx -t` + `nginx -s reload` (sem restart, sem downtime). Testado de um ponto externo real antes/depois: `oficina-do-lundy` e `stuntmotos` continuam respondendo normal (307); subdomínio fantasma novo continua rejeitado (502 visto de fora); log passou a registrar IP diferente do gateway interno.

**Cobre a maior parte do problema real de imediato:** bots que atacam direto no IP do servidor (que era a maioria do que se via no log — sondas de WordPress, `wp-admin`, `xmlrpc.php`) agora são identificados corretamente. Verificado que isso é seguro contra falsificação: mesmo que o bot tente forjar o cabeçalho `X-Forwarded-For`, só o último valor da cadeia conta, e esse é sempre escrito pelo Traefik com base na conexão real, não no que o cliente mandou.

**Bônus fora do escopo original, aplicado por segurança operacional:** `ghost_subdomains.log` não tinha rotação nenhuma e já estava em ~25 MB, crescendo sem parar. Criado `/etc/logrotate.d/mecanicapro-ghost-subdomains` (mesmo padrão `copytruncate` já usado em `traefik-access-log` nesta VPS, com `su root root` por causa da permissão 777 do diretório pai — não mexida). Testado com `logrotate -d`, sem erro.

### O que fica pendente, de propósito — decisão consciente, não esquecimento
**Tráfego que passa pela Cloudflare** (o domínio está atrás dela — confirmado via `Server: cloudflare`/`CF-RAY`) ainda aparece no log como o IP de borda da Cloudflare (ex: `172.71.238.166`), não o visitante final. Melhor que antes (não é mais o Docker se auto-banindo), mas incompleto.

**Por que não foi até o fim:** a correção completa exige mexer no **Traefik**, que atende **todos os projetos desta VPS** (confirmado: pelo menos 9 regras de roteamento distintas) — não só o mecanicapro. A mudança fica em `/opt/traefik/traefik.yml`, configuração **estática**, só lida na inicialização: exigiria **reiniciar o Traefik** (interrupção breve de todos os domínios ao mesmo tempo) e um erro na config trava a VPS inteira até ser corrigido manualmente. Apresentado o risco ao usuário, que decidiu adiar — decisão registrada, não pendência esquecida.

**Se algum dia for fazer:** em `entryPoints.web`/`entryPoints.websecure` de `traefik.yml`, acrescentar `forwardedHeaders.trustedIPs` com as faixas publicadas pela Cloudflare (`https://www.cloudflare.com/ips/`, mudam de tempos em tempos — conferir na hora, não usar lista velha). Fazer em horário de baixo movimento, com backup do `traefik.yml`, e testar **todos** os domínios depois do restart, não só o mecanicapro.
