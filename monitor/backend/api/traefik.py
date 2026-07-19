import os
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models.database as db_module
from api._slug import slugify
from api.auth import get_token_data, verify_token_header
from models.database import TraefikActionLog

TRAEFIK_DYNAMIC_DIR = os.environ.get("TRAEFIK_DYNAMIC_DIR", "/opt/traefik/dynamic")

router = APIRouter(prefix="/api/traefik", dependencies=[Depends(verify_token_header)])


def _log_action(username: str, filename: str, acao: str, sucesso: int = 1, erro: Optional[str] = None):
    with Session(db_module.engine) as session:
        session.add(TraefikActionLog(
            username=username, filename=filename, acao=acao,
            sucesso=sucesso, erro=erro,
        ))
        session.commit()


class RouteCreateIn(BaseModel):
    nome_exibicao: str
    yaml_content: str


class RouteUpdateIn(BaseModel):
    yaml_content: str


def _validar_yaml(yaml_content: str) -> Optional[str]:
    try:
        yaml.safe_load(yaml_content)
        return None
    except yaml.YAMLError as e:
        return str(e)


@router.get("/routes")
def list_routes():
    if not os.path.isdir(TRAEFIK_DYNAMIC_DIR):
        return []
    rotas = []
    for filename in sorted(os.listdir(TRAEFIK_DYNAMIC_DIR)):
        if not filename.endswith(".yml"):
            continue
        path = os.path.join(TRAEFIK_DYNAMIC_DIR, filename)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        rotas.append({
            "filename": filename,
            "managed": filename.startswith("vps-monitor-"),
            "content": content,
        })
    return rotas


@router.post("/routes", status_code=201)
def create_route(body: RouteCreateIn, token_data: dict = Depends(get_token_data)):
    username = token_data.get("sub", "desconhecido")
    filename = f"{slugify(body.nome_exibicao)}.yml"
    path = os.path.join(TRAEFIK_DYNAMIC_DIR, filename)

    if os.path.exists(path):
        raise HTTPException(status_code=409, detail=f"Já existe uma rota com o nome '{filename}'.")

    erro = _validar_yaml(body.yaml_content)
    if erro:
        _log_action(username, filename, "create", sucesso=0, erro=erro)
        raise HTTPException(status_code=400, detail=f"YAML inválido: {erro}")

    with open(path, "w", encoding="utf-8") as f:
        f.write(body.yaml_content)

    _log_action(username, filename, "create", sucesso=1)
    return {"filename": filename}


@router.put("/routes/{filename}")
def update_route(filename: str, body: RouteUpdateIn, token_data: dict = Depends(get_token_data)):
    if not filename.startswith("vps-monitor-"):
        raise HTTPException(status_code=403, detail="Só é possível editar rotas criadas pelo monitor.")
    username = token_data.get("sub", "desconhecido")

    path = os.path.join(TRAEFIK_DYNAMIC_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Rota não encontrada.")

    erro = _validar_yaml(body.yaml_content)
    if erro:
        _log_action(username, filename, "edit", sucesso=0, erro=erro)
        raise HTTPException(status_code=400, detail=f"YAML inválido: {erro}")

    with open(path, "w", encoding="utf-8") as f:
        f.write(body.yaml_content)

    _log_action(username, filename, "edit", sucesso=1)
    return {"ok": True}


@router.delete("/routes/{filename}")
def delete_route(filename: str, token_data: dict = Depends(get_token_data)):
    if not filename.startswith("vps-monitor-"):
        raise HTTPException(status_code=403, detail="Só é possível excluir rotas criadas pelo monitor.")
    username = token_data.get("sub", "desconhecido")

    path = os.path.join(TRAEFIK_DYNAMIC_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Rota não encontrada.")

    os.remove(path)
    _log_action(username, filename, "delete", sucesso=1)
    return {"ok": True}
