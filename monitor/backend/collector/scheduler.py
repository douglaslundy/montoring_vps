import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

import models.database as db_module
from collector.docker_client import DockerClient
from collector.host import collect_host_metrics
from models.database import AccessLog, AccessLogDaily, AccessLogHourly, AlertNotification, AlertRule, ContainerDiskUsage, ContainerMetrics, MetricsHistory, engine
from collector.access_log_tailer import tail_access_log
from notifications.alert_engine import _evaluate_rule, evaluate
from ws.stream import manager

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="UTC")
docker_client = DockerClient()
_last_metrics: dict = {}


async def collect_and_store():
    global _last_metrics
    try:
        loop = asyncio.get_running_loop()
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
                swap_used_mb=host["swap"]["used_mb"],
                swap_percent=host["swap"]["percent"],
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
                    net_rx_mb=c["net_rx_mb"],
                    net_tx_mb=c["net_tx_mb"],
                    status=c["status"],
                    restart_count=c["restart_count"],
                ))
            session.commit()

        active_alerts = await evaluate(host, containers, docker_client)

        payload = {
            "ts": now.isoformat() + "Z",
            "cpu": host["cpu"],
            "ram": host["ram"],
            "swap": host["swap"],
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


async def collect_disk_usage():
    try:
        containers = await docker_client.list_containers_with_size()
    except Exception:
        logger.exception("Erro ao coletar uso de disco dos containers")
        return

    now = datetime.utcnow()
    with Session(db_module.engine) as session:
        for c in containers:
            name = (c["Names"][0].lstrip("/") if c.get("Names") else c["Id"][:12])
            session.add(ContainerDiskUsage(
                collected_at=now,
                container_id=c["Id"][:12],
                container_name=name,
                size_rw_mb=round((c.get("SizeRw") or 0) / 1024 ** 2, 1),
                size_rootfs_mb=round((c.get("SizeRootFs") or 0) / 1024 ** 2, 1),
            ))
        session.commit()


async def _cleanup():
    import os
    from models.database import Config
    with Session(engine) as session:
        detailed_cfg = session.get(Config, "retention_detailed_days")
        detailed_days = int(detailed_cfg.value) if detailed_cfg else int(os.environ.get("RETENTION_DETAILED_DAYS", "7"))
        aggregated_cfg = session.get(Config, "retention_aggregated_days")
        aggregated_days = int(aggregated_cfg.value) if aggregated_cfg else int(os.environ.get("RETENTION_AGGREGATED_DAYS", "30"))

    detailed_cutoff = datetime.utcnow() - timedelta(days=detailed_days)
    aggregated_cutoff = datetime.utcnow() - timedelta(days=aggregated_days)

    with Session(engine) as session:
        session.query(MetricsHistory).filter(MetricsHistory.collected_at < detailed_cutoff).delete()
        session.query(ContainerMetrics).filter(ContainerMetrics.collected_at < aggregated_cutoff).delete()
        session.query(ContainerDiskUsage).filter(ContainerDiskUsage.collected_at < aggregated_cutoff).delete()
        session.query(AccessLog).filter(AccessLog.accessed_at < detailed_cutoff).delete()
        session.query(AccessLogHourly).filter(AccessLogHourly.hour < detailed_cutoff.strftime("%Y-%m-%d %H")).delete()
        session.query(AccessLogDaily).filter(AccessLogDaily.day < aggregated_cutoff.strftime("%Y-%m-%d")).delete()
        session.query(AlertNotification).filter(AlertNotification.tentativa_em < aggregated_cutoff).delete()
        session.commit()


def get_last_metrics() -> dict:
    return _last_metrics


async def check_docker_cleanup():
    try:
        await docker_client.prune_build_cache()
    except Exception:
        logger.exception("Erro ao limpar build cache do Docker")

    try:
        images = await docker_client.list_images()
    except Exception:
        logger.exception("Erro ao listar imagens Docker")
        return

    orfas = [img for img in images if (img.get("Containers") or 0) == 0]
    reclaimable_mb = sum((img.get("Size") or 0) for img in orfas) / 1024 ** 2

    now = datetime.utcnow()
    with Session(engine) as session:
        from api.config import get_config
        vps_name = get_config(session, "server_name", "VPS Monitor")
        rules = session.query(AlertRule).filter(
            AlertRule.ativo == 1, AlertRule.metrica == "docker_reclaimable_mb"
        ).all()
        if not rules:
            return

        extra_context = {
            "imagens_orfas": [
                {
                    "repo_tag": (img.get("RepoTags") or ["<none>:<none>"])[0],
                    "tamanho_mb": round((img.get("Size") or 0) / 1024 ** 2, 1),
                    "criada_em": img.get("Created"),
                }
                for img in orfas
            ]
        } if orfas else None

        for rule in rules:
            mensagem = f"{rule.nome}: {reclaimable_mb:.0f} MB em imagens sem container associado"
            _evaluate_rule(session, rule, reclaimable_mb, mensagem, now, vps_name, [], extra_context=extra_context)
        session.commit()


def start_scheduler():
    scheduler.add_job(collect_and_store, "interval", seconds=30, id="collect", replace_existing=True)
    scheduler.add_job(collect_disk_usage, "interval", minutes=10, id="disk_usage", replace_existing=True)
    scheduler.add_job(tail_access_log, "interval", seconds=15, id="access_log_tail", replace_existing=True)
    scheduler.add_job(_cleanup, "interval", hours=1, id="cleanup", replace_existing=True)
    scheduler.add_job(check_docker_cleanup, "interval", hours=6, id="docker_cleanup", replace_existing=True)
    if not scheduler.running:
        scheduler.start()
    asyncio.ensure_future(collect_and_store())
    asyncio.ensure_future(collect_disk_usage())
    asyncio.ensure_future(tail_access_log())
