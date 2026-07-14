import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models.database as db_module
from api.auth import get_token_data
from collector.scheduler import docker_client, get_last_metrics
from models.database import ContainerActionLog

containers_router = APIRouter()


@containers_router.get("/containers")
def list_containers():
    metrics = get_last_metrics()
    return {"containers": metrics.get("containers", [])}


@containers_router.get("/containers/{container_id}/logs")
async def get_logs(container_id: str, tail: int = 100):
    logs = await docker_client.get_logs(container_id, tail=tail)
    return {"logs": logs}


def _container_name(container_id: str) -> str:
    metrics = get_last_metrics()
    for c in metrics.get("containers", []):
        if c.get("id") == container_id or c.get("id_full") == container_id:
            return c.get("name", container_id)
    return container_id


async def _run_action(container_id: str, acao: str, fn, token_data: dict) -> dict:
    container_name = _container_name(container_id)
    username = token_data.get("sub", "desconhecido")

    try:
        await fn(container_id)
    except httpx.HTTPStatusError as e:
        erro = str(e)
        status_code = 404 if e.response.status_code == 404 else 502
        with Session(db_module.engine) as session:
            session.add(ContainerActionLog(
                username=username, container_id=container_id, container_name=container_name,
                acao=acao, sucesso=0, erro=erro,
            ))
            session.commit()
        raise HTTPException(status_code=status_code, detail=f"Falha ao {acao} container: {erro}")

    with Session(db_module.engine) as session:
        session.add(ContainerActionLog(
            username=username, container_id=container_id, container_name=container_name,
            acao=acao, sucesso=1, erro=None,
        ))
        session.commit()
    return {"ok": True}


@containers_router.post("/containers/{container_id}/start")
async def start_container(container_id: str, token_data: dict = Depends(get_token_data)):
    return await _run_action(container_id, "start", docker_client.start_container, token_data)


@containers_router.post("/containers/{container_id}/stop")
async def stop_container(container_id: str, token_data: dict = Depends(get_token_data)):
    return await _run_action(container_id, "stop", docker_client.stop_container, token_data)


@containers_router.post("/containers/{container_id}/restart")
async def restart_container(container_id: str, token_data: dict = Depends(get_token_data)):
    return await _run_action(container_id, "restart", docker_client.restart_container, token_data)
