# Histórico de notificações de alertas + correção de alerta perdido

## Contexto

Vários alertas de disco (`Disco Alto` / `Disco Crítico`, regras padrão com
`duracao_minutos = 0`) aparecem no histórico de `/alertas`, mas nenhuma
notificação por WhatsApp foi recebida para eles — enquanto alertas de
CPU/RAM (que exigem 3–5 min sustentados) notificam normalmente.

**Causa raiz identificada** (`backend/notifications/alert_engine.py`,
`_evaluate_rule`): ao criar um `AlertLog` novo, a função só grava o registro
— nunca chama `_notify_alert` nesse mesmo ciclo. A notificação só é avaliada
na *próxima* execução do agendador (30s depois), quando o alerta já está
aberto. Se o valor cair abaixo do threshold antes desse segundo ciclo (comum
em disco: rotação de log, limpeza temporária), o alerta é criado e resolvido
sem nunca passar pelo branch que notifica — mesmo a regra pedindo
"notifique sem esperar" (`duracao_minutos = 0`).

Além disso, o sistema já grava `notificado_email`, `notificado_whatsapp`,
`erro_email`, `erro_whatsapp` em `AlertLog`, mas:
- a API (`/api/alerts/history`, `/api/alerts/active`) nunca devolve esses
  campos;
- o frontend nunca os exibe;
- um único campo de erro por alerta é sobrescrito a cada nova tentativa
  (cooldown), então não há histórico de tentativas reais — só o estado da
  última.

Esta entrega corrige o bug de notificação perdida e substitui os campos
soltos por um histórico de tentativas de envio de verdade, visível na UI.

## 1. Fix: notificar já na criação do alerta

Em `_evaluate_rule` (`backend/notifications/alert_engine.py`), ao criar o
`AlertLog` (branch `condition_true and open_log is None`), passa a checar
imediatamente se a duração mínima já foi satisfeita — para
`duracao_minutos == 0` isso é sempre verdade — e, se sim, chama
`_notify_alert` no mesmo ciclo, usando o objeto recém-criado como
`open_log`. Cooldown não bloqueia essa primeira notificação (`last_notified_at`
ainda é `None`).

Refatoração: as duas branches (`open_log is None` e `open_log is not None`)
passam a convergir num único fluxo — "garanta que existe um open_log, então
avalie duração/cooldown e notifique se aplicável" — eliminando a duplicação
de lógica de duração/cooldown.

## 2. Nova tabela: histórico de tentativas de envio

`backend/models/database.py`:

```python
class AlertNotification(Base):
    __tablename__ = "alert_notification"
    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_log_id = Column(Integer, ForeignKey("alert_log.id"), nullable=False)
    canal = Column(String, nullable=False)       # "email" | "whatsapp"
    tipo = Column(String, nullable=False)        # "disparo" | "resolucao"
    status = Column(String, nullable=False)      # "enviado" | "falhou" | "desabilitado"
    erro = Column(Text, nullable=True)
    tentativa_em = Column(DateTime, nullable=False, default=datetime.utcnow)

Index("ix_alert_notification_alert_log_id", AlertNotification.alert_log_id)
```

Tabela nova → criada automaticamente por `Base.metadata.create_all(engine)`
em `init_db()`, sem necessidade de `ALTER TABLE` manual.

Os campos `notificado_email`, `notificado_whatsapp`, `erro_email`,
`erro_whatsapp` em `AlertLog` deixam de ser escritos (ficam como colunas
mortas no schema — sem custo, evita lidar com `DROP COLUMN` no SQLite).
`last_notified_at` continua sendo escrito e usado para o cálculo de
cooldown.

### Semântica de `status`

- **`enviado`**: chamada a `send_alert`/`send_resolution` do canal retornou
  sem exceção.
- **`falhou`**: a regra tem o canal marcado (`canal_email`/`canal_whatsapp`
  = 1) e o switch global está ligado (`smtp_enabled`/`evolution_enabled` =
  "1"), mas a chamada lançou exceção (SMTP recusou, Evolution API
  indisponível, etc.). `erro` grava `str(exception)`.
- **`desabilitado`**: a regra tem o canal marcado, mas o switch global
  correspondente está desligado — a tentativa nem chega a ser feita.
  `erro` fica `null`.

**Não** grava linha quando a regra não marca aquele canal (`canal_email = 0`
ou `canal_whatsapp = 0`) — isso é configuração intencional da regra, não uma
falha a ser reportada.

## 3. Motor de alertas: sempre registrar a tentativa

`_notify_alert` e `_notify_resolution` (`backend/notifications/alert_engine.py`)
passam a inserir um `AlertNotification` por canal marcado na regra, em vez
de só setar `log.notificado_email = 1` / `log.erro_email = ...`:

```python
def _record_notification(session, alert_log_id, canal, tipo, status, erro=None):
    session.add(AlertNotification(
        alert_log_id=alert_log_id, canal=canal, tipo=tipo,
        status=status, erro=erro, tentativa_em=datetime.utcnow(),
    ))
```

Para cada canal marcado na regra:
1. Se o switch global está desligado → `_record_notification(..., status="desabilitado")`, não tenta enviar.
2. Senão, tenta enviar; sucesso → `status="enviado"`; exceção → `status="falhou", erro=str(e)` (mantém o `logger.exception` existente).

`log.last_notified_at = now` continua sendo setado em `_notify_alert`
independentemente do resultado (mesmo comportamento atual de cooldown).

## 4. API (`backend/api/alerts.py`)

`_log_dict` passa a incluir uma lista `notificacoes`, ordenada por
`tentativa_em` desc:

```json
{
  "id": 42,
  "...": "...",
  "notificacoes": [
    {"canal": "whatsapp", "tipo": "disparo", "status": "enviado", "erro": null, "tentativa_em": "2026-07-14T10:03:00Z"},
    {"canal": "email", "tipo": "disparo", "status": "desabilitado", "erro": null, "tentativa_em": "2026-07-14T10:03:00Z"}
  ]
}
```

Implementado com uma query `AlertNotification` por `alert_log_id` (histórico
já limita a 500 linhas, N+1 aceitável nesse volume — mesmo padrão simples já
usado no restante do arquivo).

## 5. Frontend (`frontend/app/alertas/page.tsx`)

- Aba **Histórico**: ao expandir uma linha (mecanismo já existente via
  `expandedAlert`), abaixo de `renderContexto`, nova seção "Notificações"
  listando cada entrada de `notificacoes`: ícone do canal (✉️/📱), badge de
  status (verde "Enviado" / vermelho "Falhou" com `erro` em tooltip/texto
  abaixo / cinza "Desabilitado"), horário formatado com `formatDt`. Lista
  vazia (regra sem nenhum canal marcado) → "Nenhuma notificação configurada
  para esta regra."
- Aba **Ativas**: cada card de alerta ativo ganha uma linha compacta de
  badges por canal (mesmo ícone + status), usando a notificação mais recente
  de cada canal em `notificacoes` — dá visibilidade imediata sem precisar
  trocar de aba.

## 6b. Fix adicional: "Container Parado" nunca notifica

Durante a implementação foi identificado um segundo bug de mesma natureza:
`_evaluate_container_stopped` (`backend/notifications/alert_engine.py`) cria
e resolve `AlertLog` para containers parados, mas **nunca chama
`_notify_alert` nem `_notify_resolution`** — a regra padrão "Container
Parado" (severidade crítico) não notifica em nenhuma circunstância, não só
em flapping rápido. Corrigido no mesmo esforço, por ser a mesma causa raiz
(caminho de avaliação que não aciona o motor de notificação) e reaproveitar
a mesma infraestrutura de `AlertNotification`:
- Ao criar o `AlertLog` de um container parado, chama `_notify_alert`
  imediatamente (regra tem `duracao_minutos = 0` e `cooldown_minutos = 0`,
  então duration_ok e cooldown_ok já são satisfeitos na criação).
- Ao marcar `resolved_at` (container voltou a rodar ou foi removido), chama
  `_notify_resolution`.

## 6. Retenção

`_cleanup()` (`backend/collector/scheduler.py`) passa a apagar
`AlertNotification` mais antigo que `retention_aggregated_days` (mesmo
padrão de `ContainerDiskUsage`/`ContainerMetrics` — é dado de auditoria, não
métrica fina).

## Testes

- `backend/tests/test_alert_engine.py`: regra com `duracao_minutos = 0` que
  dispara e resolve entre dois ciclos consecutivos ainda assim gera uma
  notificação (`status="enviado"`) antes de resolver; regra com canal
  desligado globalmente grava `status="desabilitado"`; canal com exceção no
  envio grava `status="falhou"` com `erro` preenchido; canal não marcado na
  regra não gera nenhuma linha.
- `backend/tests/test_alerts_api.py`: `/alerts/history` e `/alerts/active`
  devolvem `notificacoes` no formato esperado, ordenadas por `tentativa_em`
  desc.

## Fora de escopo

- Reenvio manual de uma notificação falhada pela UI.
- Alertas de e-mail (usuário usa só WhatsApp por ora) — o design cobre
  ambos os canais igualmente, mas não há verificação adicional específica
  de SMTP nesta entrega.
- Endpoint dedicado de histórico de notificações fora do já existente
  `/alerts/history` — a lista vem embutida em cada alerta.
