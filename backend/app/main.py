from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api.routes.health import router as health_router
from app.api.routes.investigation import router as investigation_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="AI Kubernetes Troubleshooting Agent",
    description="On-demand Kubernetes troubleshooting API.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(investigation_router)

logger.info("AI Kubernetes Troubleshooting Agent API started")

