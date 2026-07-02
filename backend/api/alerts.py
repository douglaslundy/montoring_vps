from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import verify_token_header
from models.database import AlertLog, AlertRule, get_session

router = APIRouter(prefix="/api/alerts", dependencies=[Depends(verify_token_header)])


class RuleIn(BaseModel):
    nome: str
    metrica: str
    operador: str
    threshold: float
    duracao_minutos: int = 5
    severidade: str
    canal_email: int = 1
    canal_whatsapp: int = 1
    cooldown_minutos: int = 30
    ativo: int = 1


@router.get("/rules")
def list_rules(session: Session = Depends(get_session)):
    rules = session.query(AlertRule).order_by(AlertRule.id).all()
    return [
        {
            "id": r.id, "nome": r.nome, "metrica": r.metrica,
            "operador": r.operador, "threshold": r.threshold,
            "duracao_minutos": r.duracao_minutos, "severidade": r.severidade,
            "canal_email": r.canal_email, "canal_whatsapp": r.canal_whatsapp,
            "cooldown_minutos": r.cooldown_minutos, "ativo": r.ativo,
            "criado_em": r.criado_em.isoformat() if r.criado_em else None,
        }
        for r in rules
    ]


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, session: Session = Depends(get_session)):
    rule = AlertRule(**body.model_dump())
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return {"id": rule.id}


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, body: RuleIn, session: Session = Depends(get_session)):
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada")
    for k, v in body.model_dump().items():
        setattr(rule, k, v)
    session.commit()
    return {"ok": True}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, session: Session = Depends(get_session)):
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada")
    session.delete(rule)
    session.commit()
    return {"ok": True}


@router.post("/rules/{rule_id}/toggle")
def toggle_rule(rule_id: int, session: Session = Depends(get_session)):
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Regra não encontrada")
    rule.ativo = 0 if rule.ativo else 1
    session.commit()
    return {"ativo": rule.ativo}


@router.get("/active")
def active_alerts(session: Session = Depends(get_session)):
    logs = (
        session.query(AlertLog)
        .filter(AlertLog.resolved_at.is_(None))
        .order_by(AlertLog.triggered_at.desc())
        .all()
    )
    return [_log_dict(a) for a in logs]


@router.get("/history")
def alert_history(
    from_dt: Optional[str] = None,
    to_dt: Optional[str] = None,
    severidade: Optional[str] = None,
    metrica: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    q = session.query(AlertLog).order_by(AlertLog.triggered_at.desc())
    if from_dt:
        try:
            q = q.filter(AlertLog.triggered_at >= datetime.fromisoformat(from_dt))
        except ValueError:
            raise HTTPException(status_code=422, detail="from_dt inválido")
    if to_dt:
        try:
            q = q.filter(AlertLog.triggered_at <= datetime.fromisoformat(to_dt))
        except ValueError:
            raise HTTPException(status_code=422, detail="to_dt inválido")
    if severidade:
        q = q.filter(AlertLog.severidade == severidade)
    if metrica:
        q = q.filter(AlertLog.metrica == metrica)
    logs = q.limit(min(limit, 500)).all()
    return [_log_dict(a) for a in logs]


def _log_dict(a: AlertLog) -> dict:
    return {
        "id": a.id,
        "rule_id": a.rule_id,
        "triggered_at": a.triggered_at.isoformat() + "Z" if a.triggered_at else None,
        "resolved_at": a.resolved_at.isoformat() + "Z" if a.resolved_at else None,
        "severidade": a.severidade,
        "metrica": a.metrica,
        "valor_no_disparo": a.valor_no_disparo,
        "threshold": a.threshold,
        "mensagem": a.mensagem,
        "vps_name": a.vps_name,
    }
