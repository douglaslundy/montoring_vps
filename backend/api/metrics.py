from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from sqlalchemy.orm import Session
from models.database import MetricsHistory, engine
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
def current_metrics():
    return get_last_metrics()


@metrics_router.get("/metrics/history")
def metrics_history(
    metric: str = Query("cpu"),
    range: str = Query("1h"),
):
    hours = RANGE_HOURS.get(range, 1)
    col = METRIC_MAP.get(metric, "cpu_percent")
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    with Session(engine) as session:
        rows = (
            session.query(MetricsHistory)
            .filter(MetricsHistory.collected_at >= cutoff)
            .order_by(MetricsHistory.collected_at.asc())
            .all()
        )

    return {
        "metric": metric,
        "range": range,
        "data": [
            {"ts": r.collected_at.isoformat() + "Z", "value": getattr(r, col)}
            for r in rows
        ],
    }
