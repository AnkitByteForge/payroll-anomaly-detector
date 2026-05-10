"""
agents/report_builder.py

ReportBuilderAgent — assembles the final structured report.

Responsibilities:
  - Sort anomalies by severity (highest absolute % change first)
  - Build the AnomalyBreakdown (counts per category)
  - Build the HITLPreview (top 3 + counts + confirmation prompt)
  - Generate an executive summary via LLM (optional — graceful fallback)
  - Assemble the final AnomalyReport

Input:  list[AnomalyRecord] + metadata
Output: (HITLPreview, AnomalyReport)

The (HITLPreview, AnomalyReport) tuple is what the Orchestrator holds in session:
  - HITLPreview  → sent to frontend for the confirmation screen
  - AnomalyReport → held in session store, returned after user confirms
"""

import logging
from datetime import datetime

from config import settings
from schemas.models import (
    AnomalyBreakdown,
    AnomalyCategory,
    AnomalyRecord,
    AnomalyReport,
    HITLPreview,
    TopAnomaly,
)
from services.llm_client import llm

logger = logging.getLogger(__name__)

# LLM prompt for executive summary
_SUMMARY_SYSTEM = (
    "You are a concise payroll audit assistant. "
    "Write a 2-3 sentence executive summary for an HR manager. "
    "Be direct, factual, and highlight the most important findings."
)


class ReportBuilderAgent:
    """
    Assembles the HITLPreview and full AnomalyReport from categorized anomalies.

    Call:
        agent = ReportBuilderAgent()
        preview, report = await agent.run(
            anomalies=anomaly_records,
            total_employees=42,
            period_current="2026-05",
            period_previous="2026-04",
        )
    """

    async def run(
        self,
        anomalies: list[AnomalyRecord],
        total_employees: int,
        period_current: str,
        period_previous: str,
    ) -> tuple[HITLPreview, AnomalyReport]:
        """
        Build both the HITL preview and the full finalized report.

        Args:
            anomalies:         Categorized anomaly records from CategorizationAgent.
            total_employees:   Total employees in the current month's payroll.
            period_current:    e.g. "2026-05"
            period_previous:   e.g. "2026-04"

        Returns:
            (HITLPreview, AnomalyReport) tuple.
        """
        logger.info(
            "[ReportBuilderAgent] Building report: %d anomalies, %d total employees",
            len(anomalies),
            total_employees,
        )

        # Sort by severity: highest absolute % change first
        # Employees with None pct_change (new/missing) are sorted to the end
        sorted_anomalies = sorted(
            anomalies,
            key=lambda a: abs(a.pct_change) if a.pct_change is not None else -1,
            reverse=True,
        )

        # Build breakdown counts
        breakdown = self._build_breakdown(sorted_anomalies)

        # Build top-3 preview (for HITL confirmation screen)
        top_3 = self._build_top_3(sorted_anomalies)

        # Build HITL confirmation prompt
        n = len(sorted_anomalies)
        if n == 0:
            confirmation_prompt = (
                "No anomalies detected in this month's payroll. "
                "Confirm to finalize the clean audit report?"
            )
        else:
            confirmation_prompt = (
                f"I found {n} anomaly{'s' if n != 1 else ''} in this month's payroll. "
                f"Confirm and finalize the report for HR review?"
            )

        preview = HITLPreview(
            total_employees=total_employees,
            total_anomalies=n,
            breakdown=breakdown,
            top_3_anomalies=top_3,
            confirmation_prompt=confirmation_prompt,
        )

        # Generate LLM executive summary
        summary = await self._generate_summary(
            sorted_anomalies, total_employees, period_current, period_previous
        )

        report = AnomalyReport(
            generated_at=datetime.utcnow(),
            period_current=period_current,
            period_previous=period_previous,
            total_employees_current=total_employees,
            total_anomalies=n,
            threshold_pct=settings.anomaly_threshold_pct,
            anomalies=sorted_anomalies,
            agents_involved=[
                "DataFetchAgent",
                "ComparisonAgent",
                "AnomalyDetectorAgent",
                "CategorizationAgent",
                "ReportBuilderAgent",
            ],
            summary=summary,
        )

        logger.info("[ReportBuilderAgent] Report assembled successfully")
        return preview, report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_breakdown(self, anomalies: list[AnomalyRecord]) -> AnomalyBreakdown:
        """Count anomalies per category."""
        counts: dict[str, int] = {
            "new_employee": 0,
            "salary_revision": 0,
            "data_error": 0,
            "missing_deduction": 0,
            "missing_slip": 0,
        }
        for a in anomalies:
            key = a.anomaly_category.value
            if key in counts:
                counts[key] += 1

        return AnomalyBreakdown(**counts)

    def _build_top_3(self, sorted_anomalies: list[AnomalyRecord]) -> list[TopAnomaly]:
        """Build the top-3 anomaly entries for the HITL preview."""
        top_3 = []
        for a in sorted_anomalies[:3]:
            top_3.append(
                TopAnomaly(
                    employee_name=a.employee_name,
                    prev_net_pay=a.prev_net_pay,
                    curr_net_pay=a.curr_net_pay,
                    pct_change=a.pct_change,
                    category=a.anomaly_category,
                )
            )
        return top_3

    async def _generate_summary(
        self,
        anomalies: list[AnomalyRecord],
        total_employees: int,
        period_current: str,
        period_previous: str,
    ) -> str:
        """
        Generate a 2-3 sentence executive summary using the LLM.
        Falls back to a rule-based summary if the LLM is unavailable.
        """
        if not anomalies:
            return (
                f"Payroll audit for {period_current} is complete. "
                f"All {total_employees} employee(s) passed the anomaly check with no issues detected. "
                f"No action is required."
            )

        # Build a compact summary string for the LLM
        category_counts = {}
        for a in anomalies:
            cat = a.anomaly_category.value
            category_counts[cat] = category_counts.get(cat, 0) + 1

        breakdown_str = ", ".join(
            f"{count} {cat.replace('_', ' ')}" for cat, count in category_counts.items()
        )

        top_anomaly = anomalies[0]
        top_str = (
            f"{top_anomaly.employee_name}: "
            f"{top_anomaly.pct_change:+.1f}% net pay change "
            f"({top_anomaly.anomaly_category.value})"
            if top_anomaly.pct_change is not None
            else f"{top_anomaly.employee_name}: {top_anomaly.anomaly_category.value}"
        )

        prompt = (
            f"Payroll audit for period {period_current} (vs {period_previous}):\n"
            f"  - Total employees in payroll: {total_employees}\n"
            f"  - Anomalies detected: {len(anomalies)} ({breakdown_str})\n"
            f"  - Most severe anomaly: {top_str}\n\n"
            f"Write a 2-3 sentence executive summary for the HR manager."
        )

        try:
            summary = await llm.ask(prompt, system_prompt=_SUMMARY_SYSTEM, max_tokens=200)
            logger.info("[ReportBuilderAgent] LLM summary generated")
            return summary
        except Exception as e:
            logger.warning("[ReportBuilderAgent] LLM summary failed: %s — using fallback", e)
            return (
                f"Payroll audit for {period_current} identified {len(anomalies)} anomalie(s) "
                f"out of {total_employees} employee(s): {breakdown_str}. "
                f"The most significant case is {top_anomaly.employee_name} "
                f"({top_anomaly.anomaly_category.value.replace('_', ' ')}). "
                f"Please review the flagged records below."
            )