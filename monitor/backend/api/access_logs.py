from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.auth import verify_token_header
from collector.geoip import lookup_ip
from models.database import AccessLog, AccessLogDaily, get_session

router = APIRouter(prefix="/api/access-logs", dependencies=[Depends(verify_token_header)])


def _cutoff_day(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")


@router.get("/summary")
def summary(
    sistema: Optional[str] = None,
    ip: Optional[str] = None,
    days: int = Query(30),
    session: Session = Depends(get_session),
):
    cutoff = _cutoff_day(days)
    q = session.query(AccessLogDaily).filter(AccessLogDaily.day >= cutoff)
    if sistema:
        q = q.filter(AccessLogDaily.sistema == sistema)
    if ip:
        q = q.filter(AccessLogDaily.ip.like(f"{ip}%"))
    rows = q.all()

    by_ip: dict[str, dict] = {}
    for r in rows:
        entry = by_ip.setdefault(r.ip, {
            "ip": r.ip, "total_acessos": 0, "sistemas": {},
            "primeiro_acesso": r.day, "ultimo_acesso": r.day,
        })
        entry["total_acessos"] += r.count
        entry["sistemas"][r.sistema] = entry["sistemas"].get(r.sistema, 0) + r.count
        if r.day < entry["primeiro_acesso"]:
            entry["primeiro_acesso"] = r.day
        if r.day > entry["ultimo_acesso"]:
            entry["ultimo_acesso"] = r.day

    result = [
        {
            "ip": v["ip"],
            "total_acessos": v["total_acessos"],
            "sistemas": [
                {"sistema": s, "count": c}
                for s, c in sorted(v["sistemas"].items(), key=lambda x: -x[1])
            ],
            "primeiro_acesso": v["primeiro_acesso"],
            "ultimo_acesso": v["ultimo_acesso"],
        }
        for v in by_ip.values()
    ]
    result.sort(key=lambda x: -x["total_acessos"])
    return result


@router.get("/sistemas")
def sistemas(session: Session = Depends(get_session)):
    rows = session.query(AccessLogDaily.sistema).distinct().order_by(AccessLogDaily.sistema).all()
    return [r[0] for r in rows]


@router.get("/ip/{ip}")
async def ip_detail(
    ip: str,
    days: int = Query(30),
    session: Session = Depends(get_session),
):
    cutoff = _cutoff_day(days)
    daily_rows = (
        session.query(AccessLogDaily)
        .filter(AccessLogDaily.ip == ip, AccessLogDaily.day >= cutoff)
        .all()
    )

    sistemas_map: dict[str, int] = {}
    for r in daily_rows:
        sistemas_map[r.sistema] = sistemas_map.get(r.sistema, 0) + r.count

    recentes = (
        session.query(AccessLog)
        .filter(AccessLog.ip == ip)
        .order_by(AccessLog.accessed_at.desc())
        .limit(200)
        .all()
    )

    ultimo_por_sistema: dict[str, str] = {}
    for row in recentes:
        ts = row.accessed_at.isoformat() + "Z"
        if row.sistema not in ultimo_por_sistema:
            ultimo_por_sistema[row.sistema] = ts

    geo = await lookup_ip(ip, session)

    return {
        "ip": ip,
        "geo": geo,
        "total_acessos": sum(sistemas_map.values()),
        "sistemas": [
            {"sistema": s, "count": c, "ultimo_acesso": ultimo_por_sistema.get(s)}
            for s, c in sorted(sistemas_map.items(), key=lambda x: -x[1])
        ],
        "acessos_recentes": [
            {
                "sistema": r.sistema,
                "path": r.path,
                "method": r.method,
                "status_code": r.status_code,
                "accessed_at": r.accessed_at.isoformat() + "Z",
            }
            for r in recentes
        ],
    }
