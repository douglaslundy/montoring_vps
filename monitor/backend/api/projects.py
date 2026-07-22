import os
import re
import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from collector.scheduler import docker_client, get_last_metrics
from api._project_grouping import agrupar_por_projeto
import api.firewall as firewall_mod
from api.firewall import PORTAS_PROTEGIDAS

import models.database as db_module
from api.auth import get_token_data
from api.backups import BACKUPS_DIR, _ARQUIVO_VALIDO_RE
from models.database import ProjectDeleteRequest

router = APIRouter()

TRAEFIK_DYNAMIC_DIR = os.environ.get("TRAEFIK_DYNAMIC_DIR", "/opt/traefik/dynamic")
_TRAEFIK_RULE_LABEL_RE = re.compile(r"^traefik\.http\.routers\.[^.]+\.rule$")
_HOST_RE = re.compile(r"Host(?:Regexp)?\(`([^`]+)`\)")
_ROTA_TRAEFIK_VALIDA_RE = re.compile(r"^vps-monitor-[a-zA-Z0-9_-]+\.yml$")


def _dominio_por_labels(containers: list[dict]) -> str | None:
    for c in containers:
        labels = c.get("labels") or {}
        for key, rule in labels.items():
            if not _TRAEFIK_RULE_LABEL_RE.match(key):
                continue
            hosts = _HOST_RE.findall(rule)
            if hosts:
                return hosts[0]
    return None


def _dominio_por_arquivo_dinamico(projeto: str) -> str | None:
    path = os.path.join(TRAEFIK_DYNAMIC_DIR, f"{projeto}.yml")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            conteudo = f.read()
    except OSError:
        return None
    hosts = _HOST_RE.findall(conteudo)
    return hosts[0] if hosts else None


def _resolver_dominio(projeto: str, containers: list[dict]) -> str | None:
    return _dominio_por_labels(containers) or _dominio_por_arquivo_dinamico(projeto)


@router.get("/projects")
def list_projects():
    metrics = get_last_metrics()
    containers = metrics.get("containers", [])
    host_total_mb = (metrics.get("ram") or {}).get("total_mb", 0.0)

    grupos = agrupar_por_projeto(containers)

    projetos = []
    for nome, membros in grupos.items():
        mem_usage_mb = round(sum(m.get("mem_usage_mb", 0.0) for m in membros), 2)
        projetos.append({
            "nome": nome,
            "dominio": _resolver_dominio(nome, membros),
            "container_count": len(membros),
            "cpu_percent": round(sum(m.get("cpu_percent", 0.0) for m in membros), 2),
            "mem_usage_mb": mem_usage_mb,
            "mem_percent_do_host": (
                round(mem_usage_mb / host_total_mb * 100, 2) if host_total_mb else 0.0
            ),
            "containers": [
                {"name": m.get("name"), "status": m.get("status")} for m in membros
            ],
        })
    projetos.sort(key=lambda p: p["nome"])
    return {"projects": projetos}


PROJETO_PROTEGIDO = "vps-monitor"


def _projeto_ou_404(projeto: str) -> list[dict]:
    metrics = get_last_metrics()
    containers = metrics.get("containers", [])
    grupos = agrupar_por_projeto(containers)
    if projeto not in grupos:
        raise HTTPException(status_code=404, detail=f"Projeto '{projeto}' não encontrado.")
    return grupos[projeto]


def _portas_publicadas(inspect: dict) -> set[int]:
    portas: set[int] = set()
    ports = (inspect.get("NetworkSettings") or {}).get("Ports") or {}
    for bindings in ports.values():
        if not bindings:
            continue
        for b in bindings:
            host_port = b.get("HostPort")
            if host_port:
                try:
                    portas.add(int(host_port))
                except ValueError:
                    pass
    return portas


def _dominio_por_arquivo_vps_monitor(projeto: str) -> str | None:
    # Usado apenas pelo delete-preview: além do dominio resolvido por labels ou
    # por {projeto}.yml (manual), o dynamic file gerenciado pelo vps-monitor
    # (vps-monitor-{projeto}.yml) também pode ser a única fonte do dominio
    # quando o container não expõe mais labels de traefik. Não usar isso em
    # _resolver_dominio/_dominio_por_arquivo_dinamico, que são compartilhados
    # com GET /api/projects e devem manter o comportamento original.
    path = os.path.join(TRAEFIK_DYNAMIC_DIR, f"vps-monitor-{projeto}.yml")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            conteudo = f.read()
    except OSError:
        return None
    hosts = _HOST_RE.findall(conteudo)
    return hosts[0] if hosts else None


def _rotas_candidatas(dominio_projeto: str | None) -> list[str]:
    if not dominio_projeto or not os.path.isdir(TRAEFIK_DYNAMIC_DIR):
        return []
    candidatas = []
    for filename in sorted(os.listdir(TRAEFIK_DYNAMIC_DIR)):
        if not filename.startswith("vps-monitor-") or not filename.endswith(".yml"):
            continue
        path = os.path.join(TRAEFIK_DYNAMIC_DIR, filename)
        try:
            with open(path, encoding="utf-8") as f:
                conteudo = f.read()
        except OSError:
            continue
        if dominio_projeto in _HOST_RE.findall(conteudo):
            candidatas.append(filename)
    return candidatas


def _regras_firewall_candidatas(portas_projeto: set[int]) -> list[dict]:
    firewall_state_file = firewall_mod.FIREWALL_STATE_FILE
    if not portas_projeto or not os.path.isfile(firewall_state_file):
        return []
    try:
        with open(firewall_state_file, encoding="utf-8") as f:
            estado = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    candidatas = []
    for regra in estado.get("regras", []):
        if regra.get("porta") in PORTAS_PROTEGIDAS:
            continue
        if regra.get("porta") in portas_projeto:
            candidatas.append({
                "porta": regra["porta"], "protocolo": regra["protocolo"],
                "permitir": regra["permitir"], "origem_ip": regra.get("origem_ip"),
            })
    return candidatas


@router.get("/projects/{projeto}/delete-preview")
async def delete_preview(projeto: str):
    if projeto == PROJETO_PROTEGIDO:
        raise HTTPException(status_code=400, detail=f"O projeto '{PROJETO_PROTEGIDO}' não pode ser excluído.")
    membros = _projeto_ou_404(projeto)

    volumes: set[str] = set()
    portas_publicadas: set[int] = set()
    for m in membros:
        id_full = m.get("id_full")
        if not id_full:
            continue
        inspect = await docker_client.container_inspect(id_full)
        for mount in inspect.get("Mounts", []):
            if mount.get("Type") == "volume" and mount.get("Name"):
                volumes.add(mount["Name"])
        portas_publicadas |= _portas_publicadas(inspect)

    dominio_projeto = _resolver_dominio(projeto, membros) or _dominio_por_arquivo_vps_monitor(projeto)

    return {
        "containers": [{"name": m.get("name"), "status": m.get("status")} for m in membros],
        "volumes": sorted(volumes),
        "rotas_candidatas": _rotas_candidatas(dominio_projeto),
        "regras_firewall_candidatas": _regras_firewall_candidatas(portas_publicadas),
    }


_STATUS_ATIVOS_DELETE = ["pending", "running"]


class RegraSelecionada(BaseModel):
    porta: int
    protocolo: str
    permitir: bool
    origem_ip: Optional[str] = None


class ProjectDeleteIn(BaseModel):
    snapshot_arquivo: str
    rotas_selecionadas: list[str] = []
    regras_selecionadas: list[RegraSelecionada] = []


def _job_delete_pendente_existe(session: Session, projeto: str) -> bool:
    return session.query(ProjectDeleteRequest).filter(
        ProjectDeleteRequest.projeto == projeto,
        ProjectDeleteRequest.status.in_(_STATUS_ATIVOS_DELETE),
    ).count() > 0


@router.post("/projects/{projeto}/delete", status_code=202)
def delete_project(projeto: str, body: ProjectDeleteIn, token_data: dict = Depends(get_token_data)):
    if projeto == PROJETO_PROTEGIDO:
        raise HTTPException(status_code=400, detail=f"O projeto '{PROJETO_PROTEGIDO}' não pode ser excluído.")
    _projeto_ou_404(projeto)

    if not _ARQUIVO_VALIDO_RE.match(body.snapshot_arquivo):
        raise HTTPException(status_code=400, detail="Nome de arquivo de snapshot inválido.")
    caminho_snapshot = os.path.join(BACKUPS_DIR, projeto, body.snapshot_arquivo)
    if not os.path.isfile(caminho_snapshot):
        raise HTTPException(status_code=400, detail="Snapshot informado não existe para este projeto.")

    for filename in body.rotas_selecionadas:
        if not _ROTA_TRAEFIK_VALIDA_RE.fullmatch(filename):
            raise HTTPException(status_code=400, detail=f"Rota '{filename}' não é gerenciada pelo monitor.")

    for regra in body.regras_selecionadas:
        if regra.porta in PORTAS_PROTEGIDAS:
            raise HTTPException(status_code=400, detail=f"Porta {regra.porta} é protegida e não pode ser removida.")

    username = token_data.get("sub", "desconhecido")

    with Session(db_module.engine) as session:
        if _job_delete_pendente_existe(session, projeto):
            raise HTTPException(status_code=409, detail=f"Já existe uma exclusão em andamento para '{projeto}'.")
        req = ProjectDeleteRequest(
            projeto=projeto,
            rotas_traefik_selecionadas=json.dumps(body.rotas_selecionadas),
            regras_firewall_selecionadas=json.dumps([r.dict() for r in body.regras_selecionadas]),
            snapshot_arquivo=body.snapshot_arquivo,
            status="pending",
            username=username,
        )
        session.add(req)
        session.commit()
        return {"request_id": req.id}
