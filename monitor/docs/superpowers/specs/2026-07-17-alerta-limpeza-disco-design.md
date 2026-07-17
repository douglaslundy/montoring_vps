# Alerta de Limpeza de Disco (build cache + imagens órfãs)

## Contexto

Em 2026-07-16, o disco da VPS de produção chegou a 81% de uso, quase todo por build cache Docker acumulado (136.9GB) e algumas imagens órfãs de outros projetos (5.2GB). A limpeza foi feita manualmente via SSH, com o usuário confirmando explicitamente cada exclusão de imagem depois de investigação (grep em scripts de deploy de cada projeto pra confirmar que nada dependia delas).

O usuário pediu uma rotina automatizada no monitor pra isso, com uma condição clara: build cache pode ser limpo automaticamente (sempre seguro — reclaimable de build cache é sempre 0% em uso, confirmado na limpeza manual), mas **nunca apagar imagem/container automaticamente** — em vez disso, gerar um alerta informando que há espaço reaproveitável, com a lista específica, para o usuário decidir manualmente.

## Objetivo

Um job periódico que (1) limpa build cache Docker automaticamente e (2) verifica imagens sem container associado, gerando um alerta (reaproveitando o sistema de alertas já existente) quando o espaço reaproveitável passa de um threshold configurável — sem tentar classificar sozinho o que é "seguro apagar" (isso continua sendo decisão humana, como na limpeza manual).

## Fora de escopo

- Excluir imagens ou containers automaticamente — o sistema só informa, nunca apaga imagem.
- Classificar imagens como "obsoleta" vs "rollback intencional" — o alerta mostra os dados brutos do Docker (imagens sem container, tamanho, idade); a decisão de investigar/apagar continua manual, como já é hoje via SSH.
- Qualquer UI nova dedicada — reaproveita 100% o formulário de regras de alerta e a tela de Alertas já existentes.

## Design

### Novas permissões no socket-proxy

`docker-compose.yml`, serviço `docker-socket-proxy`:

```yaml
    environment:
      - CONTAINERS=1
      - POST=1
      - DELETE=1
      - IMAGES=1
      - BUILD=1
```

`IMAGES=1` habilita `GET /images/json` (listar imagens). `BUILD=1` habilita `POST /build/prune` (limpar build cache).

### `backend/collector/docker_client.py` — dois métodos novos

```python
async def list_images(self) -> list[dict]:
    async with self._client() as c:
        r = await c.get("/images/json", params={"all": False})
        r.raise_for_status()
        return r.json()

async def prune_build_cache(self) -> dict:
    async with self._client() as c:
        r = await c.post("/build/prune", params={"all": "true"})
        r.raise_for_status()
        return r.json()
```

### `backend/collector/scheduler.py` — novo job periódico

Novo job `check_docker_cleanup`, registrado em `start_scheduler()` a cada 6 horas:

```python
scheduler.add_job(check_docker_cleanup, "interval", hours=6, id="docker_cleanup", replace_existing=True)
```

```python
async def check_docker_cleanup():
    try:
        await docker_client.prune_build_cache()
    except Exception:
        logger.exception("Erro ao limpar build cache do Docker")

    try:
        images = await docker_client.list_images()
    except Exception:
        logger.exception("Erro ao listar imagens Docker")
        return

    orfas = [img for img in images if (img.get("Containers") or 0) == 0]
    reclaimable_mb = sum((img.get("Size") or 0) for img in orfas) / 1024 ** 2

    now = datetime.utcnow()
    with Session(engine) as session:
        from api.config import get_config
        vps_name = get_config(session, "server_name", "VPS Monitor")
        rules = session.query(AlertRule).filter(
            AlertRule.ativo == 1, AlertRule.metrica == "docker_reclaimable_mb"
        ).all()
        if not rules:
            return

        extra_context = {
            "imagens_orfas": [
                {
                    "repo_tag": (img.get("RepoTags") or ["<none>:<none>"])[0],
                    "tamanho_mb": round((img.get("Size") or 0) / 1024 ** 2, 1),
                    "criada_em": img.get("Created"),
                }
                for img in orfas
            ]
        } if orfas else None

        for rule in rules:
            mensagem = f"{rule.nome}: {reclaimable_mb:.0f} MB em imagens sem container associado"
            _evaluate_rule(session, rule, reclaimable_mb, mensagem, now, vps_name, [], extra_context=extra_context)
        session.commit()
```

(import `_evaluate_rule` de `notifications.alert_engine`, mesma função já usada por `evaluate()`)

### `backend/notifications/alert_engine.py` — `_evaluate_rule` ganha `extra_context`

```python
def _evaluate_rule(session: Session, rule: AlertRule, value: float, mensagem: str, now: datetime, vps_name: str, containers: list, extra_context: Optional[dict] = None):
    ...
    if condition_true and open_log is None:
        contexto = extra_context if extra_context is not None else _build_metric_context(rule.metrica, containers, session)
        ...
```

Única mudança na função existente — o resto do fluxo (duração, cooldown, notificação, resolução) é reaproveitado sem alteração.

### `backend/models/database.py` — regra padrão + backfill

Adicionar a `_DEFAULT_RULES`:

```python
{"nome": "Espaço em Disco Reaproveitável", "metrica": "docker_reclaimable_mb", "operador": ">", "threshold": 500, "duracao_minutos": 0, "severidade": "aviso", "cooldown_minutos": 1440},
```

E, em `init_db()`, além do bloco `if session.query(AlertRule).count() == 0`, um backfill idempotente pra bancos que já existem (incluindo produção):

```python
if not session.query(AlertRule).filter_by(nome="Espaço em Disco Reaproveitável").first():
    session.add(AlertRule(
        nome="Espaço em Disco Reaproveitável", metrica="docker_reclaimable_mb",
        operador=">", threshold=500, duracao_minutos=0,
        severidade="aviso", cooldown_minutos=1440,
    ))
```

### Frontend — `frontend/app/alertas/page.tsx`

Adicionar à lista já existente:

```tsx
const METRICAS = [..., 'docker_reclaimable_mb'];
const METRICA_LABELS: Record<string, string> = {
  ...,
  docker_reclaimable_mb: 'Espaço Reaproveitável (Docker)',
};
```

E um branch novo em `renderContexto`:

```tsx
if (ctx.imagens_orfas) {
  linhas.push(
    <div key="imagens_orfas">
      <strong>Imagens sem container associado: </strong>
      {ctx.imagens_orfas.map((i: any) => `${i.repo_tag} (${i.tamanho_mb} MB)`).join(', ')}
    </div>
  );
}
```

### Testes

Backend (TDD):
- `docker_client.list_images()` chama `GET /images/json` com os params corretos.
- `docker_client.prune_build_cache()` chama `POST /build/prune` com os params corretos.
- `check_docker_cleanup()`: soma corretamente o tamanho das imagens com `Containers=0`, ignora as com `Containers>0`; chama `prune_build_cache` sempre; não quebra se `list_images` falhar (só loga); dispara alerta quando `reclaimable_mb > threshold` da regra ativa; não dispara se não houver regra ativa com essa métrica.
- `_evaluate_rule` com `extra_context` fornecido usa esse contexto em vez de chamar `_build_metric_context`.
- Backfill: `init_db()` roda duas vezes seguidas, regra "Espaço em Disco Reaproveitável" existe só uma vez (idempotente).

Frontend: `npm run build` limpo + verificação manual (criar/editar a regra pelo formulário já existente, confirmar que a métrica aparece na lista).

## Arquivos afetados

- **Modificado:** `backend/collector/docker_client.py`, `backend/collector/scheduler.py`, `backend/notifications/alert_engine.py`, `backend/models/database.py`, `docker-compose.yml`, `frontend/app/alertas/page.tsx`
- **Modificado (testes):** `backend/tests/test_docker_client.py`, `backend/tests/test_scheduler.py`, `backend/tests/test_alert_engine.py`, `backend/tests/test_database.py`
