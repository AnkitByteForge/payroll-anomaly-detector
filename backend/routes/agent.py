"""
routes/agent.py

POST /api/v1/run     — run the agent team, return HITL preview
POST /api/v1/confirm — resume a paused session (confirm or cancel)

HITL flow:
  1. Client sends POST /run with a prompt
  2. PayrollAnalysisTeam runs the full pipeline
  3. Result is stored in session_store keyed by session_id (UUID)
  4. /run returns status="awaiting_confirmation" + preview + session_id
  5. Client shows the preview and Confirm / Cancel buttons
  6. Client sends POST /confirm with {session_id, confirmed: true/false}
  7. If confirmed  → session resolved, full report returned
  8. If cancelled  → session resolved, discarded message returned

Error handling:
  ERPNextError  → 503 Service Unavailable (ERP is unreachable)
  ValueError    → 422 Unprocessable Entity (bad input)
  All others    → 500 Internal Server Error with detail message
"""

import logging

from fastapi import APIRouter, HTTPException

from agents.team import team
from schemas.models import (
    ConfirmRequest,
    ConfirmResponse,
    RunRequest,
    RunResponse,
    SessionStatus,
)
from services.erpnext_client import ERPNextError
from session_store import store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent"])


# ---------------------------------------------------------------------------
# POST /api/v1/run
# ---------------------------------------------------------------------------


@router.post("/run", response_model=RunResponse)
async def run_agent(request: RunRequest) -> RunResponse:
    """
    Accept a natural language payroll review prompt, run the multi-agent
    pipeline, and return an awaiting_confirmation response with the
    HITL preview.

    The full AnomalyReport is held in session_store until the user
    confirms or cancels via POST /confirm.

    Request body:
        { "prompt": "Review this month's payroll..." }

    Response (success):
        {
          "status": "awaiting_confirmation",
          "session_id": "<uuid>",
          "preview": { ...HITLPreview... }
        }

    Response (error):
        { "detail": "<error message>" }
    """
    logger.info("[/run] Received prompt: %r", request.prompt[:80])

    try:
        # Run the full multi-agent pipeline via the team
        result = await team.run(request.prompt)

    except ERPNextError as e:
        # ERPNext unreachable or returned an error — surface as 503
        logger.error("[/run] ERPNext error [%d]: %s", e.status_code, e.message)
        raise HTTPException(
            status_code=503,
            detail=f"ERPNext API error: {e.message}",
        )

    except Exception as e:
        logger.exception("[/run] Unexpected pipeline error: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline error: {str(e)}",
        )

    # Store the full report in session — hold it until user confirms
    session_id = await store.create_session(
        prompt=request.prompt,
        preview=result.preview,
        pending_report=result.report,
    )

    logger.info(
        "[/run] Session created: %s | anomalies=%d | branch=%s",
        session_id,
        result.report.total_anomalies,
        result.branch_taken,
    )

    return RunResponse(
        status="awaiting_confirmation",
        session_id=session_id,
        preview=result.preview,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/confirm
# ---------------------------------------------------------------------------


@router.post("/confirm", response_model=ConfirmResponse)
async def confirm_action(request: ConfirmRequest) -> ConfirmResponse:
    """
    Resume a paused HITL session.

    If confirmed=true:
        Marks the session as confirmed.
        Returns the full AnomalyReport that was held in session.

    If confirmed=false:
        Marks the session as cancelled.
        Returns a discard message. No data is written to ERPNext.

    Request body:
        { "session_id": "<uuid>", "confirmed": true }

    Response (confirmed):
        {
          "status": "completed",
          "session_id": "<uuid>",
          "report": { ...AnomalyReport... }
        }

    Response (cancelled):
        {
          "status": "cancelled",
          "session_id": "<uuid>",
          "message": "Anomaly report discarded. No data was saved or flagged in ERPNext."
        }

    Error cases:
        404  session_id not found
        400  session already resolved (confirmed or cancelled)
    """
    logger.info(
        "[/confirm] session_id=%s confirmed=%s",
        request.session_id,
        request.confirmed,
    )

    # Retrieve session
    session = await store.get_session(request.session_id)

    if session is None:
        logger.warning("[/confirm] Session not found: %s", request.session_id)
        raise HTTPException(
            status_code=404,
            detail=f"Session '{request.session_id}' not found. "
                   f"It may have expired or the session_id is incorrect.",
        )

    # Guard against double-confirmation
    if session.status != SessionStatus.awaiting_confirmation:
        logger.warning(
            "[/confirm] Session already resolved: %s | status=%s",
            request.session_id,
            session.status.value,
        )
        raise HTTPException(
            status_code=400,
            detail=f"Session is already '{session.status.value}'. "
                   f"Each session can only be confirmed or cancelled once.",
        )

    # Resolve the session
    await store.resolve_session(request.session_id, request.confirmed)

    if not request.confirmed:
        logger.info("[/confirm] Session cancelled: %s", request.session_id)
        return ConfirmResponse(
            status="cancelled",
            session_id=request.session_id,
            message=(
                "Anomaly report discarded. "
                "No data was saved or flagged in ERPNext."
            ),
        )

    # Confirmed — return the full report
    logger.info(
        "[/confirm] Session confirmed: %s | anomalies=%d",
        request.session_id,
        session.pending_report.total_anomalies if session.pending_report else 0,
    )

    return ConfirmResponse(
        status="completed",
        session_id=request.session_id,
        report=session.pending_report,
    )