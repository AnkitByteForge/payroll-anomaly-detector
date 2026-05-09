"""
routes/health.py

Endpoints:
  GET /api/v1/health   — liveness check (service name, version, status)
  GET /api/v1/history  — list all past HITL sessions with summary info
"""

import logging

from fastapi import APIRouter

from config import settings
from schemas.models import HealthResponse, SessionSummary
from session_store import store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health & History"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """
    Liveness check.
    Returns service metadata — used by reviewers and monitoring tools
    to confirm the server is up and configured correctly.
    """
    return HealthResponse(
        service="Payroll Anomaly Detector",
        version=settings.app_version,
        status="ok",
        environment=settings.app_env,
    )


@router.get("/history", response_model=list[SessionSummary])
async def history() -> list[SessionSummary]:
    """
    Return a summary of all past HITL sessions, newest first.

    Each entry shows:
      - session_id
      - status (awaiting_confirmation / confirmed / cancelled)
      - the original prompt
      - when it was created and resolved
      - how many anomalies were found (if any)
    """
    sessions = await store.list_sessions()

    summaries = []
    for s in sessions:
        total_anomalies = None
        if s.pending_report is not None:
            total_anomalies = s.pending_report.total_anomalies

        summaries.append(
            SessionSummary(
                session_id=s.session_id,
                status=s.status,
                prompt=s.prompt,
                created_at=s.created_at,
                resolved_at=s.resolved_at,
                total_anomalies=total_anomalies,
            )
        )

    return summaries