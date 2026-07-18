import os
import re
import unicodedata
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models.database as db_module
from api.auth import get_token_data, verify_token_header
from collector import fail2ban_client
from models.database import Fail2banActionLog

FAIL2BAN_JAIL_DIR = os.environ.get("FAIL2BAN_JAIL_DIR", "/etc/fail2ban/jail.d")
FAIL2BAN_FILTER_DIR = os.environ.get("FAIL2BAN_FILTER_DIR", "/etc/fail2ban/filter.d")

router = APIRouter(prefix="/api/fail2ban", dependencies=[Depends(verify_token_header)])


def _slugify(nome_exibicao: str) -> str:
    nfkd = unicodedata.normalize("NFKD", nome_exibicao)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_str.lower()).strip("-")
    return f"vps-monitor-{slug}"


def _log_action(username: str, jail_nome: str, acao: str, sucesso: int = 1, erro: Optional[str] = None, detalhes: Optional[str] = None):
    with Session(db_module.engine) as session:
        session.add(Fail2banActionLog(
            username=username, jail_nome=jail_nome, acao=acao,
            sucesso=sucesso, erro=erro, detalhes=detalhes,
        ))
        session.commit()


class JailIn(BaseModel):
    nome_exibicao: str
    log_path: str
    sample_log_line: str
    regex: str
    maxretry: int = 5
    findtime: int = 600
    bantime: int = 3600
    port: str = "http,https"


class UnbanIn(BaseModel):
    ip: str


def _write_jail_file(slug: str, body: JailIn) -> str:
    jail_path = os.path.join(FAIL2BAN_JAIL_DIR, f"{slug}.local")
    with open(jail_path, "w", encoding="utf-8") as f:
        f.write(
            f"[{slug}]\nenabled = true\nbackend = auto\nfilter = {slug}\n"
            f"logpath = {body.log_path}\nport = {body.port}\n"
            f"maxretry = {body.maxretry}\nfindtime = {body.findtime}\n"
            f"bantime = {body.bantime}\nbanaction = nftables\n"
        )
    return jail_path


@router.get("/jails")
async def list_jails():
    return await fail2ban_client.status_all()


@router.post("/jails", status_code=201)
async def create_jail(body: JailIn, token_data: dict = Depends(get_token_data)):
    slug = _slugify(body.nome_exibicao)
    username = token_data.get("sub", "desconhecido")

    try:
        re.compile(body.regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Regex inválido: {e}")

    filter_path = os.path.join(FAIL2BAN_FILTER_DIR, f"{slug}.conf")
    with open(filter_path, "w", encoding="utf-8") as f:
        f.write(f"[Definition]\nfailregex = {body.regex}\nignoreregex =\n")

    matched, saida = await fail2ban_client.dry_run_regex(body.sample_log_line, filter_path)
    if not matched:
        os.remove(filter_path)
        _log_action(username, slug, "create", sucesso=0, erro=saida)
        raise HTTPException(status_code=400, detail=f"O regex não bateu com a linha de exemplo fornecida: {saida}")

    jail_path = _write_jail_file(slug, body)

    try:
        # reload_all (sem nome de jail), nao reload_jail: o jail acabou de
        # ser criado e o fail2ban em execucao ainda nao o conhece —
        # "reload <jail>" falha com "does not exist" pra jails que o
        # servidor nunca carregou. So o reload geral pega jails novos.
        await fail2ban_client.reload_all()
    except RuntimeError as e:
        os.remove(filter_path)
        os.remove(jail_path)
        _log_action(username, slug, "create", sucesso=0, erro=str(e))
        raise HTTPException(status_code=500, detail=f"Falha ao ativar o jail no fail2ban: {e}")

    _log_action(username, slug, "create", sucesso=1)
    return {"slug": slug}


@router.put("/jails/{slug}")
async def update_jail(slug: str, body: JailIn, token_data: dict = Depends(get_token_data)):
    if not slug.startswith("vps-monitor-"):
        raise HTTPException(status_code=403, detail="Só é possível editar jails criados pelo monitor.")
    username = token_data.get("sub", "desconhecido")

    try:
        re.compile(body.regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Regex inválido: {e}")

    filter_path = os.path.join(FAIL2BAN_FILTER_DIR, f"{slug}.conf")
    jail_path = os.path.join(FAIL2BAN_JAIL_DIR, f"{slug}.local")
    filter_backup = open(filter_path, encoding="utf-8").read() if os.path.exists(filter_path) else None
    jail_backup = open(jail_path, encoding="utf-8").read() if os.path.exists(jail_path) else None

    with open(filter_path, "w", encoding="utf-8") as f:
        f.write(f"[Definition]\nfailregex = {body.regex}\nignoreregex =\n")

    matched, saida = await fail2ban_client.dry_run_regex(body.sample_log_line, filter_path)
    if not matched:
        if filter_backup is not None:
            with open(filter_path, "w", encoding="utf-8") as f:
                f.write(filter_backup)
        _log_action(username, slug, "edit", sucesso=0, erro=saida)
        raise HTTPException(status_code=400, detail=f"O regex não bateu com a linha de exemplo fornecida: {saida}")

    _write_jail_file(slug, body)

    try:
        await fail2ban_client.reload_jail(slug)
    except RuntimeError as e:
        if filter_backup is not None:
            with open(filter_path, "w", encoding="utf-8") as f:
                f.write(filter_backup)
        if jail_backup is not None:
            with open(jail_path, "w", encoding="utf-8") as f:
                f.write(jail_backup)
        try:
            await fail2ban_client.reload_jail(slug)
        except RuntimeError:
            pass
        _log_action(username, slug, "edit", sucesso=0, erro=str(e))
        raise HTTPException(status_code=500, detail=f"Falha ao aplicar a edição no fail2ban: {e}")

    _log_action(username, slug, "edit", sucesso=1)
    return {"ok": True}


@router.delete("/jails/{slug}")
async def delete_jail(slug: str, token_data: dict = Depends(get_token_data)):
    if not slug.startswith("vps-monitor-"):
        raise HTTPException(status_code=403, detail="Só é possível excluir jails criados pelo monitor.")
    username = token_data.get("sub", "desconhecido")

    await fail2ban_client.stop_jail(slug)

    jail_path = os.path.join(FAIL2BAN_JAIL_DIR, f"{slug}.local")
    filter_path = os.path.join(FAIL2BAN_FILTER_DIR, f"{slug}.conf")
    if os.path.exists(jail_path):
        os.remove(jail_path)
    if os.path.exists(filter_path):
        os.remove(filter_path)

    _log_action(username, slug, "delete", sucesso=1)
    return {"ok": True}


@router.post("/jails/{slug}/unban")
async def unban(slug: str, body: UnbanIn, token_data: dict = Depends(get_token_data)):
    username = token_data.get("sub", "desconhecido")
    await fail2ban_client.unban_ip(slug, body.ip)
    _log_action(username, slug, "unban", detalhes=body.ip, sucesso=1)
    return {"ok": True}
