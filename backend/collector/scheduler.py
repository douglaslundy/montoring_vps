import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from collector.docker_client import DockerClient
from collector.host import collect_host_metrics
from models.database import ContainerMetrics, MetricsHistory, engine, get_session
from notifications.alert_engine import evaluate
from ws.stream import manager

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="UTC")
docker_client = DockerClient()
_last_metrics: dict = {}


async def collect_and_store():
    global _last_metrics
    try:
        loop = asyncio.get_event_loop()
        host_task = loop.run_in_executor(None, collect_host_metrics)
        docker_task = docker_client.collect_all()
        host, containers = await asyncio.gather(host_task, docker_task)

        now = datetime.utcnow()

        with Session(engine) as session:
            session.add(MetricsHistory(
                collected_at=now,
                cpu_percent=host["cpu"]["percent"],
                load_1m=host["cpu"]["load"][0],
                load_5m=host["cpu"]["load"][1],
                load_15m=host["cpu"]["load"][2],
                ram_total_mb=host["ram"]["total_mb"],
                ram_used_mb=host["ram"]["used_mb"],
                ram_percent=host["ram"]["percent"],
                disk_used_gb=host["disk"]["used_gb"],
                disk_total_gb=host["disk"]["total_gb"],
                disk_percent=host["disk"]["percent"],
                net_rx_bytes_s=host["net"]["rx_bytes_s"],
                net_tx_bytes_s=host["net"]["tx_bytes_s"],
                temperature_c=host["temperature_c"],
            ))
            for c in containers:
                session.add(ContainerMetrics(
                    collected_at=now,
                    container_id=c["id"],
                    container_name=c["name"],
                    cpu_percent=c["cpu_percent"],
                    mem_used_mb=c["mem_usage_mb"],
                    mem_limit_mb=c["mem_limit_mb"],
                    mem_percent=c["mem_percent"],
                    net_rx_bytes=c["net_rx_mb"],
                    net_tx_bytes=c["net_tx_mb"],
                    status=c["status"],
                    restart_count=c["restart_count"],
                ))
            session.commit()

        active_alerts = await evaluate(host, containers)

        payload = {
            "ts": now.isoformat() + "Z",
            "cpu": host["cpu"],
            "ram": host["ram"],
            "disk": host["disk"],
            "net": host["net"],
            "temperature_c": host["temperature_c"],
            "uptime": host["uptime"],
            "containers": containers,
            "active_alerts": active_alerts,
        }
        _last_metrics = payload
        await manager.broadcast(payload)
    except Exception:
        logger.exception("Erro na coleta de métricas")


async def _cleanup():
    import os
    from models.database import Config
    with get_session() as session:
        detailed_cfg = session.get(Config, "retention_detailed_days")
        detailed_days = int(detailed_cfg.value) if detailed_cfg else int(os.environ.get("RETENTION_DETAILED_DAYS", "7"))
        aggregated_cfg = session.get(Config, "retention_aggregated_days")
        aggregated_days = int(aggregated_cfg.value) if aggregated_cfg else int(os.environ.get("RETENTION_AGGREGATED_DAYS", "30"))

    detailed_cutoff = datetime.utcnow() - timedelta(days=detailed_days)
    aggregated_cutoff = datetime.utcnow() - timedelta(days=aggregated_days)

    with Session(engine) as session:
        session.query(MetricsHistory).filter(MetricsHistory.collected_at < detailed_cutoff).delete()
        session.query(ContainerMetrics).filter(ContainerMetrics.collected_at < aggregated_cutoff).delete()
        session.commit()


def get_last_metrics() -> dict:
    return _last_metrics


def start_scheduler():
    scheduler.add_job(collect_and_store, "interval", seconds=30, id="collect", replace_existing=True)
    scheduler.add_job(_cleanup, "interval", hours=1, id="cleanup", replace_existing=True)
    if not scheduler.running:
        scheduler.start()
    asyncio.ensure_future(collect_and_store())
