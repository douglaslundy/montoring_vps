import logging
from datetime import datetime

from sqlalchemy.orm import Session

from models.database import AlertLog, AlertRule, engine

logger = logging.getLogger(__name__)

_OPERATORS = {
    ">": lambda v, t: v > t,
    "<": lambda v, t: v < t,
    ">=": lambda v, t: v >= t,
    "<=": lambda v, t: v <= t,
    "==": lambda v, t: v == t,
}


def _get_metric_value(metrica: str, metrics: dict, containers: list):
    """Retorna o valor atual da métrica ou None se indisponível."""
    if metrica == "cpu_percent":
        return metrics.get("cpu", {}).get("percent")
    if metrica == "ram_percent":
        return metrics.get("ram", {}).get("percent")
    if metrica == "disk_percent":
        return metrics.get("disk", {}).get("percent")
    if metrica == "temperature_c":
        return metrics.get("temperature_c")
    if metrica == "load_1m":
        load = metrics.get("cpu", {}).get("load", [])
        return load[0] if load else None
    return None


def _evaluate_rule(session: Session, rule: AlertRule, value: float, mensagem: str, now: datetime):
    """Avalia uma regra simples (não container_stopped)."""
    op = _OPERATORS.get(rule.operador)
    if op is None or value is None:
        return

    condition_true = op(value, rule.threshold)

    # Busca AlertLog aberto para esta regra
    open_log = (
        session.query(AlertLog)
        .filter(AlertLog.rule_id == rule.id, AlertLog.resolved_at.is_(None))
        .first()
    )

    if condition_true and open_log is None:
        session.add(AlertLog(
            rule_id=rule.id,
            triggered_at=now,
            severidade=rule.severidade,
            metrica=rule.metrica,
            valor_no_disparo=value,
            threshold=rule.threshold,
            mensagem=mensagem,
        ))
    elif not condition_true and open_log is not None:
        open_log.resolved_at = now


def _evaluate_container_stopped(session: Session, rule: AlertRule, containers: list, now: datetime):
    """Avalia regra especial de container parado — uma instância por container."""
    for c in containers:
        if c.get("status") == "running":
            continue

        name = c.get("name", "unknown")
        container_mensagem = f"Container '{name}' parado"

        open_log = (
            session.query(AlertLog)
            .filter(
                AlertLog.rule_id == rule.id,
                AlertLog.resolved_at.is_(None),
                AlertLog.mensagem == container_mensagem,
            )
            .first()
        )

        if open_log is None:
            session.add(AlertLog(
                rule_id=rule.id,
                triggered_at=now,
                severidade=rule.severidade,
                metrica="container_stopped",
                valor_no_disparo=1,
                threshold=1,
                mensagem=container_mensagem,
            ))

    # Resolve containers que voltaram a running
    running_names = {c["name"] for c in containers if c.get("status") == "running"}
    open_container_logs = (
        session.query(AlertLog)
        .filter(AlertLog.rule_id == rule.id, AlertLog.resolved_at.is_(None))
        .all()
    )
    for log in open_container_logs:
        container_name = log.mensagem.replace("Container '", "").replace("' parado", "")
        if container_name in running_names:
            log.resolved_at = now


async def evaluate(metrics: dict, containers: list) -> list:
    """Avalia todas as regras ativas e retorna lista de alertas ativos."""
    now = datetime.utcnow()
    try:
        with Session(engine) as session:
            rules = session.query(AlertRule).filter(AlertRule.ativo == 1).all()

            for rule in rules:
                try:
                    if rule.metrica == "container_stopped":
                        _evaluate_container_stopped(session, rule, containers, now)
                    else:
                        value = _get_metric_value(rule.metrica, metrics, containers)
                        if value is None:
                            continue
                        mensagem = f"{rule.nome}: {value:.1f} {rule.operador} {rule.threshold}"
                        _evaluate_rule(session, rule, value, mensagem, now)
                except Exception:
                    logger.exception("Erro avaliando regra %s", rule.nome)

            session.commit()

            # Retorna alertas ativos
            active = (
                session.query(AlertLog)
                .filter(AlertLog.resolved_at.is_(None))
                .order_by(AlertLog.triggered_at.desc())
                .limit(50)
                .all()
            )
            return [
                {
                    "id": a.id,
                    "severidade": a.severidade,
                    "metrica": a.metrica,
                    "mensagem": a.mensagem,
                    "triggered_at": a.triggered_at.isoformat() + "Z",
                }
                for a in active
            ]
    except Exception:
        logger.exception("Erro no motor de alertas")
        return []
