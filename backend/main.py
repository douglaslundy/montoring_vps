import os
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from limiter import limiter
from api.auth import auth_router, verify_token_header
from api.metrics import metrics_router
from api.containers import containers_router
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

app.include_router(auth_router, prefix="/api")
app.include_router(metrics_router, prefix="/api", **_protected)
app.include_router(containers_router, prefix="/api", **_protected)
app.include_router(ws_router)


@app.on_event("startup")
async def startup():
    init_db()
    from collector.scheduler import start_scheduler
    start_scheduler()


@app.get("/api/health")
async def health():
    return {"status": "ok"}
