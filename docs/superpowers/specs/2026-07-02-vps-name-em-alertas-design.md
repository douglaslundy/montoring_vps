# Nome da VPS nos alertas

## Contexto

O sistema hoje monitora uma VPS por instância. Quando várias VPSs rodam esse
mesmo sistema (cada uma com seu próprio backend/frontend), notificações de
e-mail/WhatsApp já incluem o nome do servidor (config `server_name`, rodapé da
mensagem), mas o texto do alerta em si (`AlertLog.mensagem`) não identifica de
qual VPS ele veio. Isso dificulta saber, olhando o dashboard, o KPI "Saúde
Geral" ou as abas Ativas/Histórico, a qual VPS um alerta se refere quando há
mais de uma instância monitorada por quem opera o sistema.

## Decisão: reaproveitar `server_name`

Já existe o campo `server_name` ("Nome do servidor") em Configurações > Geral,
hoje usado só no rodapé de e-mail/WhatsApp. Não será criado um campo novo —
esse mesmo valor passa a ser gravado também em cada `AlertLog`, como campo
próprio (não concatenado na `mensagem`).

## Mudanças

### 1. Schema — `AlertLog.vps_name`

- Nova coluna `vps_name` (String, nullable) em `AlertLog`.
- Migração leve em `init_db()`, mesmo padrão já usado para `last_notified_at`:
  `ALTER TABLE alert_log ADD COLUMN vps_name VARCHAR` dentro de
  `try/except` (ignora erro se a coluna já existir).
- **Backfill automático**: logo após a migração, todo `alert_log` com
  `vps_name IS NULL` é atualizado com o `server_name` configurado atualmente
  (ou o default `"VPS Monitor"` se não houver config ainda). Isso cobre tanto
  alertas antigos já resolvidos quanto os que estão em aberto — sem precisar
  de intervenção manual no banco. Roda uma vez, no próximo restart do backend.

### 2. Motor de alertas (`notifications/alert_engine.py`)

- `evaluate()` lê `server_name` uma vez por ciclo via
  `get_config(session, "server_name", "VPS Monitor")` (mesma função já usada
  em `_notify_alert`/`_notify_resolution`).
- Esse valor é passado para `_evaluate_rule` e `_evaluate_container_stopped`,
  que passam a preencher `vps_name=server_name` em todo `AlertLog` novo
  (tanto os de métrica quanto os de container parado).
- Não afeta a lógica de resolução existente (matching por `mensagem`
  continua igual).

### 3. API (`api/alerts.py`)

- `_log_dict()` passa a incluir `"vps_name": a.vps_name`.
- Isso propaga automaticamente para `/api/alerts/active` e
  `/api/alerts/history`.
- O dict retornado por `evaluate()` (usado no payload do WebSocket que
  alimenta o dashboard) também inclui `"vps_name"`.

### 4. Frontend

- `frontend/app/page.tsx` — bloco "Alertas Ativos": badge com o nome da VPS
  ao lado da mensagem de cada alerta.
- `frontend/app/alertas/page.tsx`:
  - Aba "Ativas": mesmo badge nos cards de alerta.
  - Aba "Histórico": nova coluna "VPS" na tabela.
  - Interface TS `AlertLog` ganha `vps_name: string | null`.

### 5. Testes

- `test_alert_engine.py`: alerta de métrica grava `vps_name` correto; alerta
  de container parado grava `vps_name` correto; múltiplos ciclos não
  duplicam nem perdem o valor.
- Teste de backfill: linhas antigas com `vps_name IS NULL` recebem o
  `server_name` configurado após rodar a migração/backfill.
- `test_alerts_api.py`: `/alerts/active` e `/alerts/history` retornam
  `vps_name` no payload.

## Fora de escopo

- Não é uma feature de agregação multi-VPS (um dashboard único mostrando
  alertas de várias VPSs) — cada VPS continua com seu próprio
  backend/frontend/banco. O nome serve só para identificação visual dentro
  da própria instância (útil ao comparar telas/screenshots/notificações de
  instâncias diferentes).
- Não altera o formato de `mensagem` nem as notificações de e-mail/WhatsApp
  (que já mostram o nome no rodapé).
