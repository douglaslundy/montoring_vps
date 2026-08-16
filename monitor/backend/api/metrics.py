from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from api.time_buckets import daily_buckets, hourly_buckets
from models.database import AlertLog, AlertRule, ContainerMetrics, MetricsHistory, get_session
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

# Metricas que ganham uma segunda serie no grafico. load_5m ao lado de
# load_1m deixa visivel a diferenca entre um pico curto e uma tendencia real.
METRIC_COMPANION = {"load": "load_5m"}


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

    companion = METRIC_COMPANION.get(metric)

    def ponto(r):
        p = {"ts": r.collected_at.isoformat() + "Z", "value": getattr(r, col)}
        if companion:
            p["value2"] = getattr(r, companion)
        return p

    return {
        "metric": metric,
        "hours": hours,
        "companion": companion,
        "data": [ponto(r) for r in rows],
    }


def _regra_primeira_linha(regras: list):
    """A primeira linha que o usuario cruza: menor threshold para >/>=,
    maior para </<=. Empate ou operadores mistos -> menor id."""
    if not regras:
        return None
    maiores = [r for r in regras if r.operador in (">", ">=")]
    menores = [r for r in regras if r.operador in ("<", "<=")]
    if maiores and not menores:
        return min(maiores, key=lambda r: (r.threshold, r.id))
    if menores and not maiores:
        return max(menores, key=lambda r: (r.threshold, -r.id))
    return min(regras, key=lambda r: r.id)


@metrics_router.get("/metrics/history/annotations")
def metrics_history_annotations(
    metric: str = Query("cpu"),
    hours: int = Query(24),
    auth=Depends(verify_token_header),
    session: Session = Depends(get_session),
):
    """Threshold da regra ativa e disparos do periodo, para desenhar a linha
    d'agua e os marcadores no grafico de /historico."""
    hours = min(hours, 168)
    col = METRIC_MAP.get(metric)
    if col is None:
        return {"metric": metric, "regra": None, "threshold": None, "alertas": []}

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    regras = session.query(AlertRule).filter(
        AlertRule.ativo == 1, AlertRule.metrica == col
    ).all()
    regra = _regra_primeira_linha(regras)

    alertas = (
        session.query(AlertLog)
        .filter(AlertLog.metrica == col, AlertLog.triggered_at >= cutoff)
        .order_by(AlertLog.triggered_at.asc())
        .all()
    )
    return {
        "metric": metric,
        "regra": regra.nome if regra else None,
        "threshold": regra.threshold if regra else None,
        "alertas": [
            {
                "triggered_at": a.triggered_at.isoformat() + "Z",
                "resolved_at": a.resolved_at.isoformat() + "Z" if a.resolved_at else None,
                "valor": a.valor_no_disparo,
                "severidade": a.severidade,
            }
            for a in alertas
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
