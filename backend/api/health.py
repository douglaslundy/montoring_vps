import time
from fastapi import APIRouter

router = APIRouter()
_started_at = time.time()

@router.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _started_at, 1),
        "version": "1.0.0",
    }
