"""
test_pipeline.py

Phase 5 test — run from the backend/ directory.
Runs the complete pre-HITL pipeline:
  DataFetchAgent → ComparisonAgent → AnomalyDetectorAgent
  → CategorizationAgent (LLM) → ReportBuilderAgent

Prints:
  - HITL preview (what the user would see before confirming)
  - Full anomaly report JSON
  - LLM executive summary

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

# Suppress debug logs for a clean test output
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


def separator(title: str, width: int = 70):
    print(f"\n{'='*width}")
    print(f"  {title}")
    print(f"{'='*width}")


def fmt(val, prefix="") -> str:
    if val is None:
        return "N/A"
    return f"{prefix}{val:>10,.2f}"


def pct(val) -> str:
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    flag = "⚠️ " if abs(val) > settings.anomaly_threshold_pct else "   "
    return f"{flag}{sign}{val:.1f}%"


async def main():
    print("\n🚀 PAYROLL ANOMALY DETECTOR — Phase 5: Full Pipeline Test")
    print(f"   Threshold: ±{settings.anomaly_threshold_pct}%  |  LLM: {settings.groq_model}\n")

    # -----------------------------------------------------------------------
    # Stage 1: DataFetchAgent
    # -----------------------------------------------------------------------
    separator("STAGE 1 — DataFetchAgent")
    fetch_agent = DataFetchAgent()
    data = await fetch_agent.run()
    print(
        f"  ✅ Fetched  current: {len(data.current_summaries)} slips  |  "
        f"previous: {len(data.previous_summaries)} slips  |  "
        f"employees: {len(data.employee_details)}"
    )
    print(f"     Period: {data.period_previous} → {data.period_current}")

    # -----------------------------------------------------------------------
    # Stage 2: ComparisonAgent
    # -----------------------------------------------------------------------
    separator("STAGE 2 — ComparisonAgent")
    comparison_agent = ComparisonAgent()
    records = comparison_agent.run(data)
    print(f"  ✅ Built {len(records)} comparison record(s)")

    # -----------------------------------------------------------------------
    # Stage 3: AnomalyDetectorAgent
    # -----------------------------------------------------------------------
    separator("STAGE 3 — AnomalyDetectorAgent")
    anomaly_agent = AnomalyDetectorAgent()
    flagged = anomaly_agent.run(records)
    stats = anomaly_agent.summary_stats(flagged)
    print(f"  ✅ Flagged {len(flagged)} / {len(records)} employee(s)")
    for reason, count in stats.items():
        print(f"     {reason:<28}: {count}")

    if not flagged:
        print("\n  ✅ No anomalies to process. Pipeline will produce a clean report.")

    # -----------------------------------------------------------------------
    # Stage 4: CategorizationAgent (LLM)
    # -----------------------------------------------------------------------
    separator("STAGE 4 — CategorizationAgent  (Groq LLM calls)")
    print("  Sending flagged records to Groq for classification...\n")

    cat_agent = CategorizationAgent()
    anomalies = await cat_agent.run(flagged, data.employee_details)

    for a in anomalies:
        pct_display = pct(a.pct_change)
        print(f"  [{a.anomaly_category.value.upper():<20}] {a.employee_name}")
        print(f"     Net pay change  : {fmt(a.prev_net_pay)} → {fmt(a.curr_net_pay)}  ({pct_display})")
        print(f"     Suggested action: {a.suggested_action}")
        print(f"     LLM explanation : {a.llm_explanation}")
        if a.missing_deduction_components:
            print(f"     Missing deducts : {', '.join(a.missing_deduction_components)}")
        print()

    print(f"  ✅ Categorized {len(anomalies)} anomaly record(s)")

    # -----------------------------------------------------------------------
    # Stage 5: ReportBuilderAgent
    # -----------------------------------------------------------------------
    separator("STAGE 5 — ReportBuilderAgent  (builds final report)")

    builder = ReportBuilderAgent()
    preview, report = await builder.run(
        anomalies=anomalies,
        total_employees=len(data.current_summaries),
        period_current=data.period_current,
        period_previous=data.period_previous,
    )
    print(f"  ✅ Report assembled")

    # -----------------------------------------------------------------------
    # HITL Preview (what the frontend will show)
    # -----------------------------------------------------------------------
    separator("HITL PREVIEW  (what the user sees before confirming)")
    print(f"\n  {preview.confirmation_prompt}\n")
    print(f"  Total employees  : {preview.total_employees}")
    print(f"  Total anomalies  : {preview.total_anomalies}")
    print(f"\n  Breakdown:")
    b = preview.breakdown
    for field, count in b.model_dump().items():
        if count > 0:
            print(f"    {field:<25}: {count}")

    if preview.top_3_anomalies:
        print(f"\n  Top {len(preview.top_3_anomalies)} anomalies:")
        for i, t in enumerate(preview.top_3_anomalies, 1):
            print(
                f"    {i}. {t.employee_name:<25}  "
                f"{fmt(t.prev_net_pay)} → {fmt(t.curr_net_pay)}  "
                f"({pct(t.pct_change)})  [{t.category.value}]"
            )

    # -----------------------------------------------------------------------
    # Executive Summary
    # -----------------------------------------------------------------------
    separator("EXECUTIVE SUMMARY  (LLM generated)")
    print(f"\n  {report.summary}\n")

    # -----------------------------------------------------------------------
    # Full Report JSON
    # -----------------------------------------------------------------------
    separator("FULL REPORT JSON  (stored in session, returned after HITL confirm)")
    report_dict = report.model_dump(mode="json")
    # Pretty-print but truncate long lists for readability
    print(json.dumps(report_dict, indent=2, default=str))

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    separator("PHASE 5 COMPLETE")
    print(f"  DataFetchAgent       ✅")
    print(f"  ComparisonAgent      ✅")
    print(f"  AnomalyDetectorAgent ✅")
    print(f"  CategorizationAgent  ✅  (Groq LLM)")
    print(f"  ReportBuilderAgent   ✅  (Groq LLM summary)")
    print()
    print("  Full pipeline running end-to-end.")
    print("  Next: Phase 6 — Orchestrator + Team + Workflow")
    print()


if __name__ == "__main__":
    asyncio.run(main())