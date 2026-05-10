"""
agents/team.py

PayrollAnalysisTeam — the single public entry point for the entire pipeline.

TEAM MODE: Coordinate
─────────────────────
Justification (for README):
  The pipeline cannot be purely sequential because the path after
  AnomalyDetectorAgent depends on the data it produces:

    • zero anomalies  → CategorizationAgent is skipped entirely
    • anomalies found → CategorizationAgent is called for LLM enrichment

  A fixed sequential pipeline would call CategorizationAgent even when
  there is nothing to categorize, wasting LLM tokens and adding latency.
  Coordinate mode gives the Orchestrator the authority to make this
  routing decision based on live data — which is exactly what it does.

TEAM MEMBERS AND ROLES:
  ┌─────────────────────────┬──────────────────────────────────────────┐
  │ Agent                   │ Role                                     │
  ├─────────────────────────┼──────────────────────────────────────────┤
  │ DataFetchAgent          │ ERPNext data layer — no LLM              │
  │ ComparisonAgent         │ Month-over-month diff — no LLM           │
  │ AnomalyDetectorAgent    │ Rule-based flagging — no LLM             │
  │ CategorizationAgent     │ LLM text generation (Groq)               │
  │ ReportBuilderAgent      │ Assembly + LLM summary (Groq)            │
  └─────────────────────────┴──────────────────────────────────────────┘

WORKFLOW WITH CONDITIONAL BRANCH:

  User prompt
      │
      ▼
  PayrollAnalysisTeam.run(prompt)
      │
      ▼
  PayrollOrchestrator
      │
      ├─► DataFetchAgent         (parallel ERPNext API calls)
      │       │
      ├─► ComparisonAgent        (pure Python diff)
      │       │
      ├─► AnomalyDetectorAgent   (rule-based flagging)
      │       │
      │   ┌───┴──────────────────────────┐
      │   │     CONDITIONAL BRANCH       │
      │   │  flagged == 0 → BRANCH A     │
      │   │  flagged  > 0 → BRANCH B     │
      │   └───┬──────────────────────────┘
      │       │
      │   BRANCH A (clean_path):
      │       └─► ReportBuilderAgent  (no LLM categorization)
      │
      │   BRANCH B (anomaly_path):
      │       ├─► CategorizationAgent  (Groq: explanation + action)
      │       └─► ReportBuilderAgent   (Groq: executive summary)
      │
      ▼
  PipelineResult  →  (preview, report)
      │
      ▼
  HITL pause  →  session stored  →  awaiting_confirmation
      │
  User confirms / cancels
      │
      ▼
  Final AnomalyReport returned

PUBLIC API:
  team = PayrollAnalysisTeam()
  result = await team.run(prompt)
  # result.preview  → show to user for HITL
  # result.report   → store in session, return after confirmation
"""

import logging

from agents.orchestrator import PayrollOrchestrator, PipelineResult

logger = logging.getLogger(__name__)


class PayrollAnalysisTeam:
    """
    Top-level coordinator for the payroll anomaly detection system.

    This class owns:
      - The team mode decision (coordinate)
      - The single public interface the route handler calls
      - High-level logging of team execution

    The actual pipeline logic lives in PayrollOrchestrator.
    Keeping them separate means the team layer can be swapped
    (e.g. replaced with a native Agno Team) without touching pipeline logic.

    Call:
        team = PayrollAnalysisTeam()
        result = await team.run("Review this month's payroll...")
    """

    MODE = "coordinate"

    MEMBERS = [
        "DataFetchAgent",
        "ComparisonAgent",
        "AnomalyDetectorAgent",
        "CategorizationAgent",   # conditionally invoked
        "ReportBuilderAgent",
    ]

    def __init__(self):
        self._orchestrator = PayrollOrchestrator()
        logger.info(
            "[Team] PayrollAnalysisTeam initialised | mode=%s | members=%s",
            self.MODE,
            ", ".join(self.MEMBERS),
        )

    async def run(self, prompt: str) -> PipelineResult:
        """
        Accept a natural language payroll review request and run the
        full multi-agent pipeline.

        Args:
            prompt: User's request, e.g.
                    "Review this month's payroll and flag anything unusual."

        Returns:
            PipelineResult containing:
              .preview  → HITLPreview for the confirmation screen
              .report   → Full AnomalyReport to return after HITL confirm
              .branch_taken → "clean_path" or "anomaly_path"

        Raises:
            ERPNextError  if ERPNext API calls fail
            Exception     for any unexpected pipeline error
        """
        logger.info("[Team] run() | prompt=%r", prompt[:80])

        result = await self._orchestrator.run(prompt)

        logger.info(
            "[Team] run() complete | branch=%s | anomalies=%d | review_needed=%d",
            result.branch_taken,
            result.report.total_anomalies,
            sum(1 for a in result.report.anomalies if a.requires_manual_review),
        )

        return result


# ---------------------------------------------------------------------------
# Module-level singleton — import this in routes/agent.py
# ---------------------------------------------------------------------------
# Usage:
#   from agents.team import team
#   result = await team.run(prompt)
# ---------------------------------------------------------------------------
team = PayrollAnalysisTeam()