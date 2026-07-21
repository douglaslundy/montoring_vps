import ipaddress
import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models.database as db_module
from api.auth import get_token_data, verify_token_header
from models.database import FirewallRuleRequest

FIREWALL_STATE_FILE = os.environ.get("FIREWALL_STATE_FILE", "/opt/vps-monitor-firewall/state.json")
PORTAS_PROTEGIDAS = {22, 80, 443}
_PROTOCOLOS_VALIDOS = {"tcp", "udp"}
_ACOES_VALIDAS = {"add", "remove"}
_STATUS_ATIVOS = ["pending", "running"]

router = APIRouter(prefix="/api/firewall", dependencies=[Depends(verify_token_header)])


class RuleIn(BaseModel):
    acao: str
    permitir: bool
    porta: int
    protocolo: str
    origem_ip: Optional[str] = None


def _validar_regra(body: RuleIn) -> None:
    if body.porta in PORTAS_PROTEGIDAS:
        raise HTTPException(status_code=400, detail=f"Porta {body.porta} é protegida e não pode ser alterada pela UI.")
    if body.acao not in _ACOES_VALIDAS:
        raise HTTPException(status_code=400, detail=f"Ação inválida: '{body.acao}'.")
    if body.protocolo not in _PROTOCOLOS_VALIDOS:
        raise HTTPException(status_code=400, detail=f"Protocolo inválido: '{body.protocolo}'.")
    if not (1 <= body.porta <= 65535):
        raise HTTPException(status_code=400, detail="Porta deve estar entre 1 e 65535.")
    if body.origem_ip:
        try:
            ipaddress.ip_network(body.origem_ip, strict=False)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Origem IP/CIDR inválido: '{body.origem_ip}'.")


def _job_pendente_existe(session: Session, body: RuleIn) -> bool:
    return session.query(FirewallRuleRequest).filter(
        FirewallRuleRequest.status.in_(_STATUS_ATIVOS),
        FirewallRuleRequest.acao == body.acao,
        FirewallRuleRequest.permitir == int(body.permitir),
        FirewallRuleRequest.porta == body.porta,
        FirewallRuleRequest.protocolo == body.protocolo,
        FirewallRuleRequest.origem_ip == body.origem_ip,
    ).count() > 0


def _ler_estado() -> list[dict]:
    if not os.path.isfile(FIREWALL_STATE_FILE):
        return []
    try:
        with open(FIREWALL_STATE_FILE, encoding="utf-8") as f:
            estado = json.load(f)
        return estado.get("regras", [])
    except (json.JSONDecodeError, OSError):
        return []


@router.get("/rules")
def list_rules():
    regras = _ler_estado()
    with Session(db_module.engine) as session:
        pendentes = session.query(FirewallRuleRequest).filter(
            FirewallRuleRequest.status.in_(_STATUS_ATIVOS)
        ).all()
        jobs = [
            {
                "id": j.id, "acao": j.acao, "permitir": bool(j.permitir),
                "porta": j.porta, "protocolo": j.protocolo, "origem_ip": j.origem_ip,
                "status": j.status,
            }
            for j in pendentes
        ]
    return {"regras": regras, "jobs_pendentes": jobs}


@router.post("/rules", status_code=202)
def create_rule(body: RuleIn, token_data: dict = Depends(get_token_data)):
    _validar_regra(body)
    username = token_data.get("sub", "desconhecido")

    with Session(db_module.engine) as session:
        if _job_pendente_existe(session, body):
            raise HTTPException(status_code=409, detail="Já existe um pedido idêntico em andamento.")
        job = FirewallRuleRequest(
            acao=body.acao, permitir=int(body.permitir), porta=body.porta,
            protocolo=body.protocolo, origem_ip=body.origem_ip, status="pending",
            username=username,
        )
        session.add(job)
        session.commit()
        return {"request_id": job.id}
