"""
session_store/store.py

In-memory session store for HITL pause/resume flow.

Design:
  - sessions are stored in a module-level dict keyed by session_id (UUID string)
  - asyncio.Lock protects all mutations (FastAPI runs in an async event loop)
  - PendingSession model (from schemas) is the only value type stored
  - Swap the dict for Redis in production with no changes to callers

Public API:
  create_session(prompt, preview, pending_report) → session_id
  get_session(session_id) → PendingSession | None
  resolve_session(session_id, confirmed) → PendingSession | None
  list_sessions() → list[PendingSession]
"""

import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from schemas.models import (
    AnomalyReport,
    HITLPreview,
    PendingSession,
    SessionStatus,
)

logger = logging.getLogger(__name__)

# Module-level storage — lives for the lifetime of the process
_sessions: dict[str, PendingSession] = {}
_lock = asyncio.Lock()


async def create_session(
    prompt: str,
    preview: HITLPreview,
    pending_report: AnomalyReport,
) -> str:
    """
    Create a new HITL session and store it.

    Args:
        prompt:         The original user prompt (for history display)
        preview:        The HITL summary shown to the user before confirmation
        pending_report: The full report, held until the user confirms

    Returns:
        The new session_id (UUID string).
    """
    session_id = str(uuid4())
    session = PendingSession(
        session_id=session_id,
        status=SessionStatus.awaiting_confirmation,
        prompt=prompt,
        preview=preview,
        pending_report=pending_report,
        created_at=datetime.now(timezone.utc),
    )

    async with _lock:
        _sessions[session_id] = session

    logger.info("Session created: %s", session_id)
    return session_id


async def get_session(session_id: str) -> PendingSession | None:
    """
    Retrieve a session by ID.

    Returns None if the session_id is not found (never raises).
    """
    async with _lock:
        return _sessions.get(session_id)


async def resolve_session(session_id: str, confirmed: bool) -> PendingSession | None:
    """
    Mark a session as confirmed or cancelled.

    Args:
        session_id: The session to resolve.
        confirmed:  True → SessionStatus.confirmed | False → SessionStatus.cancelled

    Returns:
        The updated PendingSession, or None if not found.
    """
    async with _lock:
        session = _sessions.get(session_id)
        if session is None:
            logger.warning("resolve_session: session not found: %s", session_id)
            return None

        session.status = (
            SessionStatus.confirmed if confirmed else SessionStatus.cancelled
        )
        session.resolved_at = datetime.now(timezone.utc)
        logger.info(
            "Session %s resolved as: %s", session_id, session.status.value
        )
        return session


async def list_sessions() -> list[PendingSession]:
    """
    Return all sessions (any status), newest first.
    Used by GET /api/v1/history.
    """
    async with _lock:
        all_sessions = list(_sessions.values())

    # Sort by created_at descending (most recent first)
    return sorted(all_sessions, key=lambda s: s.created_at, reverse=True)