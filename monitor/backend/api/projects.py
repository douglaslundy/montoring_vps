import os
import re
from fastapi import APIRouter
from collector.scheduler import get_last_metrics
from api._project_grouping import agrupar_por_projeto

router = APIRouter()

TRAEFIK_DYNAMIC_DIR = os.environ.get("TRAEFIK_DYNAMIC_DIR", "/opt/traefik/dynamic")
_TRAEFIK_RULE_LABEL_RE = re.compile(r"^traefik\.http\.routers\.[^.]+\.rule$")
_HOST_RE = re.compile(r"Host(?:Regexp)?\(`([^`]+)`\)")


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
