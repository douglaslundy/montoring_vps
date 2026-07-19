import os
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models.database as db_module
from api._project_grouping import agrupar_por_projeto
from api.auth import get_token_data, verify_token_header
from collector.scheduler import get_last_metrics
from models.database import BackupJob, BackupSchedule

BACKUPS_DIR = os.environ.get("BACKUPS_DIR", "/opt/vps-monitor-backups")
_NOME_VALIDO_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_ARQUIVO_VALIDO_RE = re.compile(r"^[a-zA-Z0-9_-]+\.tar\.gz$")
_FREQUENCIAS_VALIDAS = {"off", "daily", "weekly"}
_STATUS_ATIVOS = ["pending", "running"]

router = APIRouter(prefix="/api/backups", dependencies=[Depends(verify_token_header)])


def _validar_nome(valor: str, campo: str) -> None:
    if not _NOME_VALIDO_RE.match(valor):
        raise HTTPException(status_code=400, detail=f"{campo} inválido: '{valor}'.")


def _validar_arquivo(valor: str) -> None:
    if not _ARQUIVO_VALIDO_RE.match(valor):
        raise HTTPException(status_code=400, detail=f"Nome de arquivo inválido: '{valor}'.")


def _job_pendente_existe(session: Session, projeto: str) -> bool:
    return session.query(BackupJob).filter(
        BackupJob.projeto == projeto, BackupJob.status.in_(_STATUS_ATIVOS)
    ).count() > 0


def _listar_snapshots(projeto: str) -> list[dict]:
    if not _NOME_VALIDO_RE.match(projeto):
        return []
    destino_dir = os.path.join(BACKUPS_DIR, projeto)
    if not os.path.isdir(destino_dir):
        return []
    snapshots = []
    for arquivo in sorted(os.listdir(destino_dir), reverse=True):
        if not arquivo.endswith(".tar.gz"):
            continue
        caminho = os.path.join(destino_dir, arquivo)
        snapshots.append({
            "arquivo": arquivo,
            "tamanho_mb": round(os.path.getsize(caminho) / 1024 / 1024, 2),
        })
    return snapshots


class ScheduleIn(BaseModel):
    frequencia: str
    hora: int = 3


@router.get("/projects")
def list_projects():
    metrics = get_last_metrics()
    containers = metrics.get("containers", [])
    grupos = agrupar_por_projeto(containers)

    with Session(db_module.engine) as session:
        schedules = {s.projeto: s for s in session.query(BackupSchedule).all()}
        jobs_ativos: dict[str, BackupJob] = {}
        for job in session.query(BackupJob).filter(BackupJob.status.in_(_STATUS_ATIVOS)).all():
            jobs_ativos.setdefault(job.projeto, job)

        resultado = []
        for nome in sorted(grupos.keys()):
            if nome == "(sem projeto)":
                continue
            schedule = schedules.get(nome)
            job = jobs_ativos.get(nome)
            resultado.append({
                "nome": nome,
                "frequencia": schedule.frequencia if schedule else "off",
                "hora": schedule.hora if schedule else 3,
                "snapshots": _listar_snapshots(nome),
                "job_ativo": {"id": job.id, "tipo": job.tipo, "status": job.status} if job else None,
            })
    return {"projects": resultado}


@router.put("/projects/{projeto}/schedule")
def set_schedule(projeto: str, body: ScheduleIn):
    _validar_nome(projeto, "Nome do projeto")
    if body.frequencia not in _FREQUENCIAS_VALIDAS:
        raise HTTPException(status_code=400, detail=f"Frequência inválida: '{body.frequencia}'.")
    if not (0 <= body.hora <= 23):
        raise HTTPException(status_code=400, detail="Hora deve estar entre 0 e 23.")

    with Session(db_module.engine) as session:
        existente = session.get(BackupSchedule, projeto)
        if existente:
            existente.frequencia = body.frequencia
            existente.hora = body.hora
        else:
            session.add(BackupSchedule(projeto=projeto, frequencia=body.frequencia, hora=body.hora))
        session.commit()
    return {"ok": True}


@router.post("/projects/{projeto}/snapshot", status_code=202)
def create_snapshot(projeto: str, token_data: dict = Depends(get_token_data)):
    _validar_nome(projeto, "Nome do projeto")
    username = token_data.get("sub", "desconhecido")

    with Session(db_module.engine) as session:
        if _job_pendente_existe(session, projeto):
            raise HTTPException(status_code=409, detail=f"Já existe uma operação em andamento para '{projeto}'.")
        job = BackupJob(projeto=projeto, tipo="snapshot", status="pending", username=username)
        session.add(job)
        session.commit()
        return {"job_id": job.id}


@router.post("/projects/{projeto}/snapshots/{arquivo}/restore", status_code=202)
def restore_snapshot(projeto: str, arquivo: str, token_data: dict = Depends(get_token_data)):
    _validar_nome(projeto, "Nome do projeto")
    _validar_arquivo(arquivo)
    username = token_data.get("sub", "desconhecido")

    caminho = os.path.join(BACKUPS_DIR, projeto, arquivo)
    if not os.path.isfile(caminho):
        raise HTTPException(status_code=404, detail="Snapshot não encontrado.")

    with Session(db_module.engine) as session:
        if _job_pendente_existe(session, projeto):
            raise HTTPException(status_code=409, detail=f"Já existe uma operação em andamento para '{projeto}'.")
        job = BackupJob(projeto=projeto, tipo="restore", arquivo=arquivo, status="pending", username=username)
        session.add(job)
        session.commit()
        return {"job_id": job.id}


@router.get("/projects/{projeto}/snapshots/{arquivo}/download")
def download_snapshot(projeto: str, arquivo: str):
    _validar_nome(projeto, "Nome do projeto")
    _validar_arquivo(arquivo)

    caminho = os.path.join(BACKUPS_DIR, projeto, arquivo)
    if not os.path.isfile(caminho):
        raise HTTPException(status_code=404, detail="Snapshot não encontrado.")

    def _stream():
        with open(caminho, "rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk

    return StreamingResponse(_stream(), media_type="application/gzip", headers={
        "Content-Disposition": f'attachment; filename="{arquivo}"'
    })


@router.delete("/projects/{projeto}/snapshots/{arquivo}", status_code=202)
def delete_snapshot(projeto: str, arquivo: str, token_data: dict = Depends(get_token_data)):
    _validar_nome(projeto, "Nome do projeto")
    _validar_arquivo(arquivo)
    username = token_data.get("sub", "desconhecido")

    caminho = os.path.join(BACKUPS_DIR, projeto, arquivo)
    if not os.path.isfile(caminho):
        raise HTTPException(status_code=404, detail="Snapshot não encontrado.")

    with Session(db_module.engine) as session:
        if _job_pendente_existe(session, projeto):
            raise HTTPException(status_code=409, detail=f"Já existe uma operação em andamento para '{projeto}'.")
        job = BackupJob(projeto=projeto, tipo="delete", arquivo=arquivo, status="pending", username=username)
        session.add(job)
        session.commit()
        return {"job_id": job.id}
