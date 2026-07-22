import os
import re
import json
from fastapi import APIRouter, HTTPException
from collector.scheduler import docker_client, get_last_metrics
from api._project_grouping import agrupar_por_projeto
from api.firewall import PORTAS_PROTEGIDAS

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
    # Tenta primeiro projeto.yml (manual)
    path = os.path.join(TRAEFIK_DYNAMIC_DIR, f"{projeto}.yml")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                conteudo = f.read()
            hosts = _HOST_RE.findall(conteudo)
            if hosts:
                return hosts[0]
        except OSError:
            pass

    # Depois tenta vps-monitor-projeto.yml (gerenciado pelo vps-monitor)
    path = os.path.join(TRAEFIK_DYNAMIC_DIR, f"vps-monitor-{projeto}.yml")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                conteudo = f.read()
            hosts = _HOST_RE.findall(conteudo)
            if hosts:
                return hosts[0]
        except OSError:
            pass

    return None


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
    firewall_state_file = os.environ.get("FIREWALL_STATE_FILE", "/opt/vps-monitor-firewall/state.json")
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

    dominio_projeto = _resolver_dominio(projeto, membros)

    return {
        "containers": [{"name": m.get("name"), "status": m.get("status")} for m in membros],
        "volumes": sorted(volumes),
        "rotas_candidatas": _rotas_candidatas(dominio_projeto),
        "regras_firewall_candidatas": _regras_firewall_candidatas(portas_publicadas),
    }
