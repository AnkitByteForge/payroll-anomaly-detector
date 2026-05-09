"""
routes/agent.py

Endpoints:
  POST /api/v1/run     — accept prompt, run agent team, return result or HITL pause
  POST /api/v1/confirm — resume a paused HITL session with user's decision

Phase 3: Both endpoints return placeholder responses so the server starts cleanly.
Phase 7: Full HITL + agent pipeline wired in here.
"""

import logging

from fastapi import APIRouter, HTTPException

from schemas.models import (
    ConfirmRequest,
    ConfirmResponse,
    RunRequest,
    RunResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent"])


@router.post("/run", response_model=RunResponse)
async def run_agent(request: RunRequest) -> RunResponse:
    """
    Accept a natural language prompt and run the payroll anomaly detection pipeline.

    Phase 3 stub: returns a 501 with a clear message.
    Phase 7: orchestrates the full agent team and HITL flow.
    """
    # TODO (Phase 7): replace with full pipeline call
    raise HTTPException(
        status_code=501,
        detail="Agent pipeline not yet implemented. Complete through Phase 7.",
    )


@router.post("/confirm", response_model=ConfirmResponse)
async def confirm_action(request: ConfirmRequest) -> ConfirmResponse:
    """
    Resume a paused HITL session.

    Phase 3 stub: returns a 501 with a clear message.
    Phase 7: looks up session, confirms/cancels, returns final report.
    """
    # TODO (Phase 7): replace with session lookup + resume logic
    raise HTTPException(
        status_code=501,
        detail="Confirm endpoint not yet implemented. Complete through Phase 7.",
    )