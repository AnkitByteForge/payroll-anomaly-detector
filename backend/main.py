"""
main.py

FastAPI application entry point.

Responsibilities:
  - Create the FastAPI app with metadata
  - Configure structured logging
  - Register all routers under /api/v1/
  - Add CORS middleware (permissive for development, tighten for production)
  - Lifespan context: log startup/shutdown cleanly

Run with:
    uvicorn main:app --reload --port 8000
"""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from routes.agent import router as agent_router
from routes.health import router as health_router


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

def configure_logging():
    """Set up structured logging to stdout."""
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO if settings.app_env == "production" else logging.DEBUG,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Quiet down noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


configure_logging()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — startup and shutdown hooks
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once on startup (before any request) and once on shutdown.
    Good place to initialize connection pools, load models, etc.
    """
    logger.info(
        "🚀 Starting %s v%s [%s]",
        app.title,
        settings.app_version,
        settings.app_env,
    )
    logger.info("ERPNext URL   : %s", settings.erpnext_base_url)
    logger.info("Groq Model    : %s", settings.groq_model)
    logger.info("Anomaly Thresh: %.1f%%", settings.anomaly_threshold_pct)

    yield  # Server is running — handle requests

    logger.info("🛑 Shutting down %s", app.title)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Payroll Anomaly Detector",
    description=(
        "AI-powered multi-agent system that compares month-over-month payroll data "
        "from ERPNext, flags anomalies, categorizes them using an LLM, and presents "
        "a structured report with human-in-the-loop confirmation before finalizing."
    ),
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # Tighten to your frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

API_PREFIX = "/api/v1"

app.include_router(health_router, prefix=API_PREFIX)
app.include_router(agent_router, prefix=API_PREFIX)


# ---------------------------------------------------------------------------
# Root redirect (convenience)
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "Payroll Anomaly Detector",
        "version": settings.app_version,
        "docs": "/docs",
        "health": f"{API_PREFIX}/health",
    }