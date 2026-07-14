import json
import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from models.database import AlertLog, AlertNotification, AlertRule, ContainerDiskUsage, engine

logger = logging.getLogger(__name__)

_OPERATORS = {
    ">": lambda v, t: v > t,
    "<": lambda v, t: v < t,
    ">=": lambda v, t: v >= t,
    "<=": lambda v, t: v <= t,
    "==": lambda v, t: v == t,
}


def _top_by(containers: list, key: str, n: int = 3) -> list:
    ranked = sorted(
        (c for c in containers if c.get(key) is not None),
        key=lambda c: c[key], reverse=True,
    )[:n]
    return [{"nome": c.get("name", "?"), "valor": round(c[key], 1)} for c in ranked]


def _top_by_rede(containers: list, n: int = 3) -> list:
    def trafego(c):
        return (c.get("net_rx_mb") or 0) + (c.get("net_tx_mb") or 0)
    ranked = sorted(containers, key=trafego, reverse=True)[:n]
    return [
        {"nome": c.get("name", "?"), "valor_mb": round(trafego(c), 1)}
        for c in ranked if trafego(c) > 0
    ]


def _top_disco(session: Session, n: int = 3) -> list:
    latest = (
        session.query(ContainerDiskUsage.collected_at)
        .order_by(ContainerDiskUsage.collected_at.desc())
        .first()
    )
    if latest is None:
        return []
    rows = (
        session.query(ContainerDiskUsage)
        .filter(ContainerDiskUsage.collected_at == latest[0])
        .order_by(ContainerDiskUsage.size_rw_mb.desc())
        .limit(n)
        .all()
    )
    return [{"nome": r.container_name, "valor_mb": round(r.size_rw_mb or 0, 1)} for r in rows]


def _build_metric_context(metrica: str, containers: list, session: Session) -> Optional[dict]:
    if metrica in ("cpu_percent", "load_1m"):
        ctx = {}
        top_cpu = _top_by(containers, "cpu_percent")
        top_rede = _top_by_rede(containers)
        if top_cpu:
            ctx["top_cpu"] = top_cpu
        if top_rede:
            ctx["top_rede"] = top_rede
        return ctx or None
    if metrica == "ram_percent":
        ctx = {}
        top_mem = _top_by(containers, "mem_percent")
        top_rede = _top_by_rede(containers)
        if top_mem:
            ctx["top_mem"] = top_mem
        if top_rede:
            ctx["top_rede"] = top_rede
        return ctx or None
    if metrica == "disk_percent":
        top_disco = _top_disco(session)
        return {"top_disco": top_disco} if top_disco else None
    return None


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


def _record_notification(session: Session, alert_log_id: int, canal: str, tipo: str, status: str, erro: Optional[str] = None) -> None:
    session.add(AlertNotification(
        alert_log_id=alert_log_id, canal=canal, tipo=tipo,
        status=status, erro=erro, tentativa_em=datetime.utcnow(),
    ))


def _evaluate_rule(session: Session, rule: AlertRule, value: float, mensagem: str, now: datetime, vps_name: str, containers: list):
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
        contexto = _build_metric_context(rule.metrica, containers, session)
        open_log = AlertLog(
            rule_id=rule.id,
            triggered_at=now,
            severidade=rule.severidade,
            metrica=rule.metrica,
            valor_no_disparo=value,
            threshold=rule.threshold,
            mensagem=mensagem,
            vps_name=vps_name,
            contexto=json.dumps(contexto) if contexto else None,
        )
        session.add(open_log)
        session.flush()  # garante open_log.id para o FK de AlertNotification

    if condition_true and open_log is not None:
        # Verifica se deve notificar (duracao_minutos atingida e cooldown passou).
        # Avaliado também na criação: duracao_minutos=0 já satisfaz duration_ok
        # de imediato, então o alerta notifica no mesmo ciclo em que é criado
        # (antes bug: só notificava a partir do 2º ciclo do alerta aberto).
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
    """Dispara notificação de alerta (email e/ou whatsapp) e grava cada tentativa em AlertNotification."""
    from api.config import get_config
    alert_dict = {
        "id": log.id, "severidade": log.severidade, "metrica": log.metrica,
        "mensagem": log.mensagem, "triggered_at": log.triggered_at.isoformat() + "Z",
        "valor_no_disparo": log.valor_no_disparo, "threshold": log.threshold,
    }
    if rule.canal_email:
        if get_config(session, "smtp_enabled") == "1":
            try:
                from notifications.email_service import send_alert
                send_alert(alert_dict, session)
                _record_notification(session, log.id, "email", "disparo", "enviado")
            except Exception as e:
                _record_notification(session, log.id, "email", "disparo", "falhou", str(e))
                logger.exception("Erro ao enviar e-mail de alerta")
        else:
            _record_notification(session, log.id, "email", "disparo", "desabilitado")
    if rule.canal_whatsapp:
        if get_config(session, "evolution_enabled") == "1":
            try:
                from notifications.whatsapp_service import send_alert as wa_send
                wa_send(alert_dict, session)
                _record_notification(session, log.id, "whatsapp", "disparo", "enviado")
            except ImportError:
                pass
            except Exception as e:
                _record_notification(session, log.id, "whatsapp", "disparo", "falhou", str(e))
                logger.exception("Erro ao enviar WhatsApp de alerta")
        else:
            _record_notification(session, log.id, "whatsapp", "disparo", "desabilitado")
    log.last_notified_at = now


def _notify_resolution(session: Session, log: AlertLog, rule: AlertRule):
    """Dispara notificação de resolução e grava cada tentativa em AlertNotification."""
    from api.config import get_config
    alert_dict = {
        "id": log.id, "severidade": log.severidade, "metrica": log.metrica,
        "mensagem": log.mensagem, "triggered_at": log.triggered_at.isoformat() + "Z",
        "resolved_at": log.resolved_at.isoformat() + "Z" if log.resolved_at else None,
    }
    if rule.canal_email:
        if get_config(session, "smtp_enabled") == "1":
            try:
                from notifications.email_service import send_resolution
                send_resolution(alert_dict, session)
                _record_notification(session, log.id, "email", "resolucao", "enviado")
            except Exception as e:
                _record_notification(session, log.id, "email", "resolucao", "falhou", str(e))
                logger.exception("Erro ao enviar e-mail de resolução")
        else:
            _record_notification(session, log.id, "email", "resolucao", "desabilitado")
    if rule.canal_whatsapp:
        if get_config(session, "evolution_enabled") == "1":
            try:
                from notifications.whatsapp_service import send_resolution as wa_res
                wa_res(alert_dict, session)
                _record_notification(session, log.id, "whatsapp", "resolucao", "enviado")
            except ImportError:
                pass
            except Exception as e:
                _record_notification(session, log.id, "whatsapp", "resolucao", "falhou", str(e))
                logger.exception("Erro ao enviar WhatsApp de resolução")
        else:
            _record_notification(session, log.id, "whatsapp", "resolucao", "desabilitado")


async def _evaluate_container_stopped(session: Session, rule: AlertRule, containers: list, now: datetime, vps_name: str, docker_client=None):
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
            contexto = None
            container_id = c.get("id_full") or c.get("id")
            if docker_client is not None and container_id:
                try:
                    inspect = await docker_client.container_inspect(container_id)
                    state = inspect.get("State", {})
                    contexto = {
                        "exit_code": state.get("ExitCode"),
                        "oom_killed": state.get("OOMKilled"),
                        "erro": state.get("Error") or None,
                        "finalizado_em": state.get("FinishedAt"),
                    }
                except Exception:
                    logger.exception("Erro ao inspecionar container parado %s", name)
                    contexto = None

            session.add(AlertLog(
                rule_id=rule.id,
                triggered_at=now,
                severidade=rule.severidade,
                metrica="container_stopped",
                valor_no_disparo=1,
                threshold=1,
                mensagem=container_mensagem,
                vps_name=vps_name,
                contexto=json.dumps(contexto) if contexto else None,
            ))

    # Resolve containers que voltaram a running OU que foram removidos
    # (recriados com outro nome/ID em vez de reiniciados — nesse caso o nome
    # antigo nunca mais vai reaparecer na lista, então o alerta ficaria preso)
    running_names = {c["name"] for c in containers if c.get("status") == "running"}
    known_names = {c["name"] for c in containers}
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
        if container_name in running_names or container_name not in known_names:
            log.resolved_at = now


async def evaluate(metrics: dict, containers: list, docker_client=None) -> list:
    """Avalia todas as regras ativas e retorna lista de alertas ativos."""
    now = datetime.utcnow()
    try:
        with Session(engine) as session:
            from api.config import get_config
            vps_name = get_config(session, "server_name", "VPS Monitor")

            rules = session.query(AlertRule).filter(AlertRule.ativo == 1).all()

            for rule in rules:
                try:
                    if rule.metrica == "container_stopped":
                        await _evaluate_container_stopped(session, rule, containers, now, vps_name, docker_client)
                    else:
                        value = _get_metric_value(rule.metrica, metrics, containers)
                        if value is None:
                            continue
                        mensagem = f"{rule.nome}: {value:.1f} {rule.operador} {rule.threshold}"
                        _evaluate_rule(session, rule, value, mensagem, now, vps_name, containers)
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
                    "vps_name": a.vps_name,
                }
                for a in active
            ]
    except Exception:
        logger.exception("Erro no motor de alertas")
        return []
