from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from api.time_buckets import daily_buckets, hourly_buckets
from models.database import ContainerMetrics, MetricsHistory, get_session
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


def _avg(values: list) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _bucket_point(ts: str, rows: list) -> dict:
    return {
        "ts": ts,
        "cpu_percent": _avg([r.cpu_percent for r in rows]),
        "mem_percent": _avg([r.mem_percent for r in rows]),
        "net_rx_mb": _avg([r.net_rx_mb for r in rows]),
        "net_tx_mb": _avg([r.net_tx_mb for r in rows]),
    }


@metrics_router.get("/metrics/container-history")
def container_history(
    container_name: str,
    granularity: str = Query("hour"),
    day: Optional[str] = None,
    month: Optional[str] = None,
    auth=Depends(verify_token_header),
    session: Session = Depends(get_session),
):
    if granularity == "day":
        buckets = daily_buckets(month)
        start = datetime.strptime(buckets[0], "%Y-%m-%d")
        end = datetime.strptime(buckets[-1], "%Y-%m-%d") + timedelta(days=1)
        rows = (
            session.query(ContainerMetrics)
            .filter(
                ContainerMetrics.container_name == container_name,
                ContainerMetrics.collected_at >= start,
                ContainerMetrics.collected_at < end,
            )
            .all()
        )
        by_bucket: dict[str, list] = {b: [] for b in buckets}
        for r in rows:
            by_bucket[r.collected_at.strftime("%Y-%m-%d")].append(r)
        return {
            "granularity": "day",
            "data": [_bucket_point(b, by_bucket[b]) for b in buckets],
        }

    hours = hourly_buckets(day)
    keys = [h.strftime("%Y-%m-%d %H") for h in hours]
    start = hours[0]
    end = hours[-1] + timedelta(hours=1)
    rows = (
        session.query(ContainerMetrics)
        .filter(
            ContainerMetrics.container_name == container_name,
            ContainerMetrics.collected_at >= start,
            ContainerMetrics.collected_at < end,
        )
        .all()
    )
    by_bucket = {k: [] for k in keys}
    for r in rows:
        by_bucket[r.collected_at.strftime("%Y-%m-%d %H")].append(r)
    return {
        "granularity": "hour",
        "data": [
            _bucket_point(h.strftime("%Y-%m-%dT%H:00:00") + "Z", by_bucket[key])
            for h, key in zip(hours, keys)
        ],
    }
