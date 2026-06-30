import logging
import re
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
    op = _OPERATORS.get(rule.operador)
    if op is None or value is None:
        return

    condition_true = op(value, rule.threshold)

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
    elif condition_true and open_log is not None:
        # Verifica se deve notificar (duracao_minutos atingida e cooldown passou)
        duration_ok = rule.duracao_minutos == 0 or (
            (now - open_log.triggered_at).total_seconds() / 60 >= rule.duracao_minutos
        )
        cooldown_ok = (
            open_log.last_notified_at is None or
            (now - open_log.last_notified_at).total_seconds() / 60 >= rule.cooldown_minutos
        )
        if duration_ok and cooldown_ok:
            _notify_alert(session, open_log, rule, now)
    elif not condition_true and open_log is not None:
        open_log.resolved_at = now
        _notify_resolution(session, open_log, rule)


def _notify_alert(session: Session, log: AlertLog, rule: AlertRule, now: datetime):
    """Dispara notificação de alerta (email e/ou whatsapp)."""
    from api.config import get_config
    alert_dict = {
        "id": log.id, "severidade": log.severidade, "metrica": log.metrica,
        "mensagem": log.mensagem, "triggered_at": log.triggered_at.isoformat() + "Z",
        "valor_no_disparo": log.valor_no_disparo, "threshold": log.threshold,
    }
    if rule.canal_email and get_config(session, "smtp_enabled") == "1":
        try:
            from notifications.email_service import send_alert
            send_alert(alert_dict, session)
            log.notificado_email = 1
        except Exception as e:
            log.erro_email = str(e)
            logger.exception("Erro ao enviar e-mail de alerta")
    if rule.canal_whatsapp and get_config(session, "evolution_enabled") == "1":
        try:
            from notifications.whatsapp_service import send_alert as wa_send
            wa_send(alert_dict, session)
            log.notificado_whatsapp = 1
        except ImportError:
            pass
        except Exception as e:
            log.erro_whatsapp = str(e)
            logger.exception("Erro ao enviar WhatsApp de alerta")
    log.last_notified_at = now


def _notify_resolution(session: Session, log: AlertLog, rule: AlertRule):
    """Dispara notificação de resolução."""
    from api.config import get_config
    alert_dict = {
        "id": log.id, "severidade": log.severidade, "metrica": log.metrica,
        "mensagem": log.mensagem, "triggered_at": log.triggered_at.isoformat() + "Z",
        "resolved_at": log.resolved_at.isoformat() + "Z" if log.resolved_at else None,
    }
    if rule.canal_email and get_config(session, "smtp_enabled") == "1":
        try:
            from notifications.email_service import send_resolution
            send_resolution(alert_dict, session)
        except Exception:
            logger.exception("Erro ao enviar e-mail de resolução")
    if rule.canal_whatsapp and get_config(session, "evolution_enabled") == "1":
        try:
            from notifications.whatsapp_service import send_resolution as wa_res
            wa_res(alert_dict, session)
        except ImportError:
            pass
        except Exception:
            logger.exception("Erro ao enviar WhatsApp de resolução")


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
        m = re.search(r"Container '(.+)' parado", log.mensagem or "")
        if not m:
            continue
        container_name = m.group(1)
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
