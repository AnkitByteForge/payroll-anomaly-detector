"""
agents/report_builder.py

ReportBuilderAgent — assembles the final structured report.

Changes from previous version:
  - run() now accepts employees_evaluated + employees_in_current_payroll
    instead of the single total_employees param
  - TopAnomaly now includes severity field
  - HITLPreview uses employees_in_current_payroll (renamed field)
  - AnomalyReport uses the two renamed metric fields
  - Sort key updated to handle severity as tiebreaker

Everything else (LLM summary, top-3 logic, breakdown, agents_involved) unchanged.
"""

import logging
from datetime import datetime

from config import settings
from schemas.models import (
    AnomalyBreakdown,
    AnomalyCategory,
    AnomalyRecord,
    AnomalyReport,
    AnomalySeverity,
    HITLPreview,
    TopAnomaly,
)
from services.llm_client import llm

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM = (
    "You are a concise payroll audit assistant. "
    "Write a 2-3 sentence executive summary for an HR manager. "
    "Be direct, factual, and highlight the most important findings. "
    "Include evaluated employees, current payroll employees, affected employees, "
    "category breakdown, manual review count, highest severity issue, and "
    "operational payroll impact."
)

# Severity sort weight — higher = more urgent = appears first
_SEVERITY_WEIGHT = {
    AnomalySeverity.critical: 4,
    AnomalySeverity.high: 3,
    AnomalySeverity.medium: 2,
    AnomalySeverity.low: 1,
}


class ReportBuilderAgent:

    async def run(
        self,
        anomalies: list[AnomalyRecord],
        employees_evaluated: int,
        employees_in_current_payroll: int,
        period_current: str,
        period_previous: str,
    ) -> tuple[HITLPreview, AnomalyReport]:
        """
        Build both the HITL preview and the full finalized report.

        Args:
            anomalies:                   Categorized records from CategorizationAgent.
            employees_evaluated:         All employees appearing in either month (union).
            employees_in_current_payroll: Employees with a slip this month only.
            period_current:              e.g. "2026-05"
            period_previous:             e.g. "2026-04"
        """
        logger.info(
            "[ReportBuilderAgent] Building report: %d anomalies | "
            "evaluated=%d | current_payroll=%d",
            len(anomalies),
            employees_evaluated,
            employees_in_current_payroll,
        )

        # Sort by severity first, then by absolute % change
        sorted_anomalies = sorted(
            anomalies,
            key=lambda a: (
                _SEVERITY_WEIGHT.get(a.severity, 0),
                abs(a.pct_change) if a.pct_change is not None else 0,
            ),
            reverse=True,
        )

        breakdown = self._build_breakdown(sorted_anomalies)
        top_3 = self._build_top_3(sorted_anomalies)

        n = len(sorted_anomalies)
        needs_review = sum(1 for a in sorted_anomalies if a.requires_manual_review)

        if n == 0:
            confirmation_prompt = (
                "No anomalies detected in this month's payroll. "
                "Confirm to finalise the clean audit report?"
            )
        else:
            confirmation_prompt = (
                f"I found {n} anomaly{'s' if n != 1 else ''} in this month's payroll "
                f"({needs_review} requiring manual review). "
                f"Confirm and finalise the report for HR review?"
            )

        preview = HITLPreview(
            employees_in_current_payroll=employees_in_current_payroll,
            total_anomalies=n,
            breakdown=breakdown,
            top_3_anomalies=top_3,
            confirmation_prompt=confirmation_prompt,
        )

        summary = await self._generate_summary(
            sorted_anomalies,
            employees_evaluated,
            employees_in_current_payroll,
            period_current,
            period_previous,
        )

        report = AnomalyReport(
            generated_at=datetime.utcnow(),
            period_current=period_current,
            period_previous=period_previous,
            employees_evaluated=employees_evaluated,
            employees_in_current_payroll=employees_in_current_payroll,
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

    def _build_breakdown(self, anomalies: list[AnomalyRecord]) -> AnomalyBreakdown:
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
        return [
            TopAnomaly(
                employee_name=a.employee_name,
                prev_net_pay=a.prev_net_pay,
                curr_net_pay=a.curr_net_pay,
                pct_change=a.pct_change,
                category=a.anomaly_category,
                severity=a.severity,
            )
            for a in sorted_anomalies[:3]
        ]

    async def _generate_summary(
        self,
        anomalies: list[AnomalyRecord],
        employees_evaluated: int,
        employees_in_current_payroll: int,
        period_current: str,
        period_previous: str,
    ) -> str:
        needs_review = sum(1 for a in anomalies if a.requires_manual_review)

        if not anomalies:
            return (
                f"Payroll audit for {period_current} is complete. "
                f"All {employees_in_current_payroll} employee(s) in the current payroll "
                f"passed the anomaly check with no issues detected."
            )

        category_counts: dict[str, int] = {}
        for a in anomalies:
            cat = a.anomaly_category.value
            category_counts[cat] = category_counts.get(cat, 0) + 1

        breakdown_str = ", ".join(
            f"{count} {cat.replace('_', ' ')}"
            for cat, count in category_counts.items()
        )
        affected_employees = len({a.employee_id for a in anomalies})
        top = anomalies[0]
        top_str = (
            f"{top.employee_name} ({top.anomaly_category.value}, "
            f"{top.severity.value} severity"
            + (f", {top.pct_change:+.1f}% net pay change" if top.pct_change else "")
            + ")"
        )
        impact_str = (
            f"{len(anomalies)} of {employees_in_current_payroll} employee(s) "
            f"in the current payroll"
        )

        prompt = (
            f"Payroll audit for {period_current} (vs {period_previous}):\n"
            f"  - Employees evaluated    : {employees_evaluated}\n"
            f"  - In current payroll     : {employees_in_current_payroll}\n"
            f"  - Affected employees     : {affected_employees}\n"
            f"  - Anomalies detected     : {len(anomalies)} ({breakdown_str})\n"
            f"  - Requiring manual review: {needs_review}\n"
            f"  - Most urgent case       : {top_str}\n\n"
            f"Operational payroll impact: {impact_str}.\n\n"
            f"Write a 2-3 sentence executive summary for the HR manager."
        )

        try:
            summary = await llm.ask(prompt, system_prompt=_SUMMARY_SYSTEM, max_tokens=200)
            logger.info("[ReportBuilderAgent] LLM summary generated")
            return summary
        except Exception as e:
            logger.warning(
                "[ReportBuilderAgent] LLM summary failed: %s — using fallback", e
            )
            return (
                f"Payroll audit for {period_current} identified {len(anomalies)} anomaly(s) "
                f"across {affected_employees} affected employee(s) out of "
                f"{employees_evaluated} evaluated: {breakdown_str}. "
                f"{needs_review} record(s) require manual review before payroll is finalised, "
                f"impacting {impact_str}. "
                f"Highest severity case: {top_str}."
            )