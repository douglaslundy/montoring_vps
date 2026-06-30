from datetime import datetime, timedelta
from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from models.database import MetricsHistory, get_session
from api.auth import verify_token_header
from collector.scheduler import get_last_metrics

metrics_router = APIRouter()

RANGE_HOURS = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
METRIC_MAP = {
    "cpu": "cpu_percent",
    "ram": "ram_percent",
    "disk": "disk_percent",
    "load": "load_1m",
    "net_rx": "net_rx_bytes_s",
    "net_tx": "net_tx_bytes_s",
    "temperature": "temperature_c",
}


@metrics_router.get("/metrics/current")
def current_metrics(auth=Depends(verify_token_header), session: Session = Depends(get_session)):
    return get_last_metrics()


@metrics_router.get("/metrics/history")
def metrics_history(
    metric: str = Query("cpu"),
    hours: int = Query(24),
    auth=Depends(verify_token_header),
    session: Session = Depends(get_session),
):
    hours = min(hours, 168)
    col = METRIC_MAP.get(metric, "cpu_percent")
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    rows = (
        session.query(MetricsHistory)
        .filter(MetricsHistory.collected_at >= cutoff)
        .order_by(MetricsHistory.collected_at.asc())
        .all()
    )

    return {
        "metric": metric,
        "hours": hours,
        "data": [
            {"ts": r.collected_at.isoformat() + "Z", "value": getattr(r, col)}
            for r in rows
        ],
    }
