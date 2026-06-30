from fastapi import APIRouter

metrics_router = APIRouter()


@metrics_router.get("/metrics/current")
def current():
    return {}
