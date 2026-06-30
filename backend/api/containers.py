from fastapi import APIRouter
from collector.scheduler import docker_client, get_last_metrics

containers_router = APIRouter()


@containers_router.get("/containers")
def list_containers():
    metrics = get_last_metrics()
    return {"containers": metrics.get("containers", [])}


@containers_router.get("/containers/{container_id}/logs")
async def get_logs(container_id: str, tail: int = 100):
    logs = await docker_client.get_logs(container_id, tail=tail)
    return {"logs": logs}
