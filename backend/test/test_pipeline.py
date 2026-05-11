"""
test_pipeline.py

Phase 5 test — full pipeline through all 5 agents.
Updated to pass employees_evaluated + employees_in_current_payroll
and display the new severity / requires_manual_review fields.

Run with:
    cd backend
    python test_pipeline.py
"""

import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)

from agents.anomaly import AnomalyDetectorAgent
from agents.categorization import CategorizationAgent
from agents.comparison import ComparisonAgent
from agents.data_fetch import DataFetchAgent
from agents.report_builder import ReportBuilderAgent
from config import settings


def sep(title: str, w: int = 70):
    print(f"\n{'='*w}\n  {title}\n{'='*w}")


def fmt(v) -> str:
    return f"{v:>12,.2f}" if v is not None else "         N/A"


def pct(v) -> str:
    if v is None:
        return "N/A"
    sign = "+" if v >= 0 else ""
    flag = "⚠️ " if abs(v) > settings.anomaly_threshold_pct else "   "
    return f"{flag}{sign}{v:.1f}%"


SEVERITY_ICON = {
    "critical": "🔴",
    "high":     "🟠",
    "medium":   "🟡",
    "low":      "🟢",
}


async def main():
    print(f"\n🚀 PAYROLL ANOMALY DETECTOR — Full Pipeline Test")
    print(f"   Threshold : ±{settings.anomaly_threshold_pct}%  |  LLM: {settings.groq_model}\n")

    # Stage 1
    sep("STAGE 1 — DataFetchAgent")
    data = await DataFetchAgent().run()
    print(f"  ✅  current={len(data.current_summaries)} slips | "
          f"previous={len(data.previous_summaries)} slips | "
          f"employees={len(data.employee_details)}")
    print(f"      Period: {data.period_previous} → {data.period_current}")

    # Stage 2
    sep("STAGE 2 — ComparisonAgent")
    records = ComparisonAgent().run(data)
    print(f"  ✅  {len(records)} comparison record(s) built")

    # Stage 3
    sep("STAGE 3 — AnomalyDetectorAgent")
    flagged = AnomalyDetectorAgent().run(records)
    stats = AnomalyDetectorAgent().summary_stats(flagged)
    print(f"  ✅  {len(flagged)} / {len(records)} flagged")
    for reason, count in stats.items():
        print(f"      {reason:<28}: {count}")

    # Stage 4
    sep("STAGE 4 — CategorizationAgent  (deterministic rules + Groq text)")
    print("  Classifying and generating explanations...\n")
    anomalies = await CategorizationAgent().run(flagged, data.employee_details)

    for a in anomalies:
        icon = SEVERITY_ICON.get(a.severity.value, "⚪")
        review = "⬛ MANUAL REVIEW" if a.requires_manual_review else "✅ no review"
        print(f"  {icon} [{a.anomaly_category.value.upper():<20}] {a.employee_name}")
        print(f"     Severity       : {a.severity.value.upper()}  |  {review}")
        print(f"     Net pay change : {fmt(a.prev_net_pay)} → {fmt(a.curr_net_pay)}  ({pct(a.pct_change)})")
        print(f"     Explanation    : {a.llm_explanation}")
        print(f"     Action         : {a.suggested_action}")
        if a.missing_deduction_components:
            print(f"     Missing dedcts : {', '.join(a.missing_deduction_components)}")
        print()

    # Stage 5
    sep("STAGE 5 — ReportBuilderAgent")
    preview, report = await ReportBuilderAgent().run(
        anomalies=anomalies,
        employees_evaluated=len(records),                    # union of both months
        employees_in_current_payroll=len(data.current_summaries),
        period_current=data.period_current,
        period_previous=data.period_previous,
    )
    print(f"  ✅  Report assembled")

    # HITL Preview
    sep("HITL PREVIEW  (confirmation screen)")
    print(f"\n  {preview.confirmation_prompt}\n")
    print(f"  Employees in current payroll : {preview.employees_in_current_payroll}")
    print(f"  Total anomalies              : {preview.total_anomalies}")
    needs_review = sum(1 for a in anomalies if a.requires_manual_review)
    print(f"  Requiring manual review      : {needs_review}")
    print(f"\n  Breakdown by category:")
    for field, count in preview.breakdown.model_dump().items():
        if count > 0:
            print(f"    {field:<25}: {count}")

    if preview.top_3_anomalies:
        print(f"\n  Top {len(preview.top_3_anomalies)} by severity + magnitude:")
        for i, t in enumerate(preview.top_3_anomalies, 1):
            icon = SEVERITY_ICON.get(t.severity.value, "⚪")
            print(
                f"    {i}. {icon} {t.employee_name:<25} "
                f"{fmt(t.prev_net_pay)} → {fmt(t.curr_net_pay)}"
                f"  ({pct(t.pct_change)})  [{t.category.value}]"
            )

    # Executive Summary
    sep("EXECUTIVE SUMMARY")
    print(f"\n  {report.summary}\n")

    # Metrics check
    sep("REPORT METRICS")
    print(f"  employees_evaluated         : {report.employees_evaluated}")
    print(f"  employees_in_current_payroll: {report.employees_in_current_payroll}")
    print(f"  total_anomalies             : {report.total_anomalies}")
    print(f"  threshold_pct               : {report.threshold_pct}%")
    print(f"  period                      : {report.period_previous} → {report.period_current}")

    # Full JSON
    sep("FULL REPORT JSON")
    print(json.dumps(report.model_dump(mode="json"), indent=2, default=str))

    sep("PIPELINE COMPLETE ✅")
    print("  All 5 agents ran successfully.")
    print("  Deterministic categorization: category/severity/review set by rules.")
    print("  LLM used only for: explanation text, suggested actions, executive summary.")
    print()


if __name__ == "__main__":
    asyncio.run(main())