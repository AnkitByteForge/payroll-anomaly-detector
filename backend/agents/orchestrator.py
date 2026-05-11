"""
agents/orchestrator.py

PayrollOrchestrator — the execution brain of the team.

Responsibilities:
  - Instantiate all five specialist agents
  - Execute them in the correct order
  - Own the CONDITIONAL BRANCH (the key architectural decision)
  - Return a typed PipelineResult to the team layer

What it does NOT do:
  - No HTTP handling
  - No session management
  - No direct ERPNext or LLM calls
  - No knowledge of HITL mechanics

The conditional branch is the architectural justification for using a
coordinate-style team rather than a fixed sequential pipeline:

  BRANCH A (clean path):  flagged == 0
      AnomalyDetectorAgent → skip CategorizationAgent → ReportBuilderAgent
      Effect: zero LLM calls wasted, instant clean report

  BRANCH B (anomaly path): flagged > 0
      AnomalyDetectorAgent → CategorizationAgent → ReportBuilderAgent
      Effect: each flagged record enriched with LLM explanation + action
"""

import logging
from dataclasses import dataclass

from agents.anomaly import AnomalyDetectorAgent
from agents.categorization import CategorizationAgent
from agents.comparison import ComparisonAgent
from agents.data_fetch import DataFetchAgent
from agents.report_builder import ReportBuilderAgent
from schemas.models import AnomalyReport, HITLPreview

logger = logging.getLogger(__name__)


class NoPayrollData(Exception):
    def __init__(self, period_current: str, period_previous: str):
        self.period_current = period_current
        self.period_previous = period_previous
        super().__init__(
            f"No payroll data for {period_previous} vs {period_current}"
        )


@dataclass
class PipelineResult:
    """
    Typed output from a single orchestrator run.
    Passed back to PayrollAnalysisTeam, then to the route handler.
    """
    preview: HITLPreview       # shown to user on HITL confirmation screen
    report: AnomalyReport      # held in session, returned after user confirms
    employees_evaluated: int
    employees_in_current_payroll: int
    total_flagged: int
    branch_taken: str          # "clean_path" or "anomaly_path"


class PayrollOrchestrator:
    """
    Coordinates the multi-agent payroll anomaly detection pipeline.

    Agent execution order:
        DataFetchAgent
            ↓
        ComparisonAgent
            ↓
        AnomalyDetectorAgent
            ↓
        ┌──────────────────────────────────────────────────┐
        │             CONDITIONAL BRANCH                   │
        │  flagged == 0  →  skip CategorizationAgent       │  BRANCH A
        │  flagged  > 0  →  CategorizationAgent            │  BRANCH B
        └──────────────────────────────────────────────────┘
            ↓
        ReportBuilderAgent
            ↓
        Returns PipelineResult (caller handles HITL)

    Call:
        orchestrator = PayrollOrchestrator()
        result = await orchestrator.run(prompt)
    """

    def __init__(self):
        self.data_fetch_agent      = DataFetchAgent()
        self.comparison_agent      = ComparisonAgent()
        self.anomaly_agent         = AnomalyDetectorAgent()
        self.categorization_agent  = CategorizationAgent()
        self.report_builder_agent  = ReportBuilderAgent()

    async def run(self, prompt: str) -> PipelineResult:
        """
        Execute the full pipeline end-to-end.

        Args:
            prompt: The user's original natural language request.
                    Used for logging/audit trail — not parsed by this layer.

        Returns:
            PipelineResult with the HITL preview, full report, and metadata.

        Raises:
            ERPNextError: if any ERPNext API call fails
            Exception:    for unexpected failures in any agent stage
        """
        logger.info(
            "[Orchestrator] ═══ Pipeline start ═══ prompt=%r", prompt[:80]
        )

        # ── Stage 1: DATA FETCH ──────────────────────────────────────────
        logger.info("[Orchestrator] Stage 1 → DataFetchAgent")
        data = await self.data_fetch_agent.run()
        logger.info(
            "[Orchestrator] Stage 1 done | current=%d previous=%d employees=%d",
            len(data.current_summaries),
            len(data.previous_summaries),
            len(data.employee_details),
        )

        if not data.current_summaries and not data.previous_summaries:
            logger.warning(
                "[Orchestrator] No payroll data available for periods %s vs %s",
                data.period_previous,
                data.period_current,
            )
            raise NoPayrollData(data.period_current, data.period_previous)

        # ── Stage 2: COMPARISON ──────────────────────────────────────────
        logger.info("[Orchestrator] Stage 2 → ComparisonAgent")
        records = self.comparison_agent.run(data)
        logger.info(
            "[Orchestrator] Stage 2 done | comparison_records=%d", len(records)
        )

        # ── Stage 3: ANOMALY DETECTION ───────────────────────────────────
        logger.info("[Orchestrator] Stage 3 → AnomalyDetectorAgent")
        flagged = self.anomaly_agent.run(records)
        logger.info(
            "[Orchestrator] Stage 3 done | flagged=%d / %d",
            len(flagged), len(records),
        )

        # ── Stage 4: CONDITIONAL BRANCH ──────────────────────────────────
        if not flagged:
            # ─ BRANCH A: Clean payroll ────────────────────────────────
            # No anomalies → skip CategorizationAgent entirely.
            # This avoids unnecessary LLM calls and produces an instant
            # clean-bill-of-health report.
            branch = "clean_path"
            logger.info(
                "[Orchestrator] Stage 4 → BRANCH A (clean_path) | "
                "CategorizationAgent skipped"
            )
            anomalies = []

        else:
            # ─ BRANCH B: Anomalies present ───────────────────────────
            # Route to CategorizationAgent for LLM enrichment.
            branch = "anomaly_path"
            logger.info(
                "[Orchestrator] Stage 4 → BRANCH B (anomaly_path) | "
                "routing %d record(s) to CategorizationAgent",
                len(flagged),
            )
            anomalies = await self.categorization_agent.run(
                flagged, data.employee_details
            )
            logger.info(
                "[Orchestrator] Stage 4 done | categorized=%d", len(anomalies)
            )

        # ── Stage 5: REPORT BUILDER ──────────────────────────────────────
        logger.info("[Orchestrator] Stage 5 → ReportBuilderAgent")
        preview, report = await self.report_builder_agent.run(
            anomalies=anomalies,
            employees_evaluated=len(records),
            employees_in_current_payroll=len(data.current_summaries),
            period_current=data.period_current,
            period_previous=data.period_previous,
        )
        logger.info(
            "[Orchestrator] Stage 5 done | anomalies_in_report=%d",
            report.total_anomalies,
        )

        logger.info(
            "[Orchestrator] ═══ Pipeline complete ═══ branch=%s anomalies=%d",
            branch, len(anomalies),
        )

        return PipelineResult(
            preview=preview,
            report=report,
            employees_evaluated=len(records),
            employees_in_current_payroll=len(data.current_summaries),
            total_flagged=len(flagged),
            branch_taken=branch,
        )