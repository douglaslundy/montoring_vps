import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

import models.database as db_module

logger = logging.getLogger(__name__)

_STATIC_EXTENSIONS = {
    "js", "css", "map", "png", "jpg", "jpeg", "gif", "svg", "ico",
    "woff", "woff2", "ttf", "webp", "avif",
}
_NOISE_PATHS = {"/favicon.ico", "/robots.txt", "/health", "/healthz"}
_warned_missing_file = False


def _log_path() -> str:
    return os.environ.get("TRAEFIK_ACCESS_LOG_PATH", "/var/log/traefik/access.log")


def _is_noise(path: str) -> bool:
    if path in _NOISE_PATHS or path.startswith("/.well-known/"):
        return True
    last_segment = path.rsplit("/", 1)[-1]
    if "." not in last_segment:
        return False
    ext = last_segment.rsplit(".", 1)[-1].lower()
    return ext in _STATIC_EXTENSIONS


def _parse_time(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return datetime.utcnow()


def _get_offset(session: Session) -> tuple[int, Optional[int]]:
    offset_row = session.get(db_module.Config, "access_log_offset")
    inode_row = session.get(db_module.Config, "access_log_inode")
    offset = int(offset_row.value) if offset_row else 0
    inode = int(inode_row.value) if inode_row else None
    return offset, inode


def _save_offset(session: Session, offset: int, inode: int) -> None:
    for key, value in (("access_log_offset", str(offset)), ("access_log_inode", str(inode))):
        row = session.get(db_module.Config, key)
        if row:
            row.value = value
        else:
            session.add(db_module.Config(key=key, value=value))
    session.commit()


def _upsert_daily(session: Session, day: str, ip: str, sistema: str) -> None:
    row = (
        session.query(db_module.AccessLogDaily)
        .filter_by(day=day, ip=ip, sistema=sistema)
        .first()
    )
    if row:
        row.count += 1
    else:
        session.add(db_module.AccessLogDaily(day=day, ip=ip, sistema=sistema, count=1))


def _upsert_hourly(session: Session, hour: str, sistema: str) -> None:
    row = (
        session.query(db_module.AccessLogHourly)
        .filter_by(hour=hour, sistema=sistema)
        .first()
    )
    if row:
        row.count += 1
    else:
        session.add(db_module.AccessLogHourly(hour=hour, sistema=sistema, count=1))


def _process_line(session: Session, line: str) -> None:
    line = line.strip()
    if not line:
        return
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return

    if not isinstance(entry, dict):
        return

    path = entry.get("RequestPath") or ""
    if not path or _is_noise(path):
        return

    ip = entry.get("ClientHost")
    sistema = entry.get("RequestHost")
    if not ip or not sistema:
        return

    accessed_at = _parse_time(entry.get("time", ""))
    session.add(db_module.AccessLog(
        accessed_at=accessed_at,
        ip=ip,
        sistema=sistema,
        path=path,
        method=entry.get("RequestMethod") or "",
        status_code=entry.get("DownstreamStatus"),
        user_agent=entry.get("request_User-Agent"),
    ))
    _upsert_daily(session, accessed_at.strftime("%Y-%m-%d"), ip, sistema)
    _upsert_hourly(session, accessed_at.strftime("%Y-%m-%d %H"), sistema)


async def tail_access_log() -> None:
    global _warned_missing_file
    path_obj = Path(_log_path())

    if not path_obj.exists():
        if not _warned_missing_file:
            logger.warning("Access log do Traefik não encontrado em %s", path_obj)
            _warned_missing_file = True
        return
    _warned_missing_file = False

    current_stat = path_obj.stat()
    current_inode = current_stat.st_ino

    with Session(db_module.engine) as session:
        offset, saved_inode = _get_offset(session)
        if saved_inode is not None and saved_inode != current_inode:
            offset = 0
        elif offset > current_stat.st_size:
            # Rotacao via logrotate `copytruncate`: mesmo inode, mas o arquivo
            # foi truncado para um tamanho menor que o offset salvo.
            offset = 0

        with open(path_obj, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            for line in f:
                try:
                    _process_line(session, line)
                except Exception:
                    logger.warning("Falha ao processar linha do access log, pulando", exc_info=True)
            new_offset = f.tell()

        session.commit()
        _save_offset(session, new_offset, current_inode)
