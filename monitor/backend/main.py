import os
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from limiter import limiter
from api.auth import auth_router, verify_token_header
from api.metrics import metrics_router
from api.containers import containers_router
from api.health import router as health_router
from api.alerts import router as alerts_router
from api.config import router as config_router
from api.notifications import router as notifications_router
from api.whatsapp import router as whatsapp_router
from api.access_logs import router as access_logs_router
from api.fail2ban import router as fail2ban_router
from api.projects import router as projects_router
from api.traefik import router as traefik_router
from ws.stream import ws_router
from models.database import init_db

app = FastAPI(title="VPS Monitor", docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("PUBLIC_URL", "http://localhost")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_protected = {"dependencies": [Depends(verify_token_header)]}

app.include_router(health_router)
app.include_router(auth_router, prefix="/api")
app.include_router(metrics_router, prefix="/api", **_protected)
app.include_router(containers_router, prefix="/api", **_protected)
app.include_router(projects_router, prefix="/api", **_protected)
app.include_router(alerts_router)
app.include_router(config_router)
app.include_router(notifications_router)
app.include_router(whatsapp_router)
app.include_router(access_logs_router)
app.include_router(fail2ban_router)
app.include_router(traefik_router)
app.include_router(ws_router)


@app.on_event("startup")
async def startup():
    init_db()
    from collector.scheduler import start_scheduler
    start_scheduler()
