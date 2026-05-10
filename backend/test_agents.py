"""
test_agents.py

Phase 4 test — run from the backend/ directory.
Tests the first three agents in isolation:
  DataFetchAgent → ComparisonAgent → AnomalyDetectorAgent

Prints all comparison records and highlights flagged anomalies.

Run with:
    cd backend
    python test_agents.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from agents.anomaly import AnomalyDetectorAgent
from agents.comparison import ComparisonAgent
from agents.data_fetch import DataFetchAgent
from config import settings


def separator(title: str, width: int = 70):
    print(f"\n{'='*width}")
    print(f"  {title}")
    print(f"{'='*width}")


def fmt(val, prefix="", suffix="") -> str:
    if val is None:
        return "         N/A"
    return f"{prefix}{val:>10,.2f}{suffix}"


def pct_str(val) -> str:
    if val is None:
        return "        N/A"
    sign = "+" if val >= 0 else ""
    flag = " ⚠️ " if abs(val) > settings.anomaly_threshold_pct else "    "
    return f"{flag}{sign}{val:.1f}%"


async def main():
    print("\n🔍 PAYROLL ANOMALY DETECTOR — Phase 4: Agent Pipeline Test")
    print(f"   Anomaly threshold: ±{settings.anomaly_threshold_pct}%\n")

    # -----------------------------------------------------------------------
    # STEP 1: DataFetchAgent
    # -----------------------------------------------------------------------
    separator("STEP 1: DataFetchAgent — Fetching payroll data from ERPNext")
    print("  Fetching salary slips and employee records... (parallel API calls)\n")

    fetch_agent = DataFetchAgent()
    data = await fetch_agent.run()

    print(f"  Period (current)   : {data.period_current}")
    print(f"  Period (previous)  : {data.period_previous}")
    print(f"  Current slips      : {len(data.current_summaries)}")
    print(f"  Previous slips     : {len(data.previous_summaries)}")
    print(f"  Current details    : {len(data.current_details)} (with components)")
    print(f"  Previous details   : {len(data.previous_details)} (with components)")
    print(f"  Active employees   : {len(data.employees)}")
    print(f"  Employee records   : {len(data.employee_details)}")
    print("\n  ✅ DataFetchAgent complete")

    # -----------------------------------------------------------------------
    # STEP 2: ComparisonAgent
    # -----------------------------------------------------------------------
    separator("STEP 2: ComparisonAgent — Building month-over-month diff")

    comparison_agent = ComparisonAgent()
    records = comparison_agent.run(data)

    print(f"\n  {len(records)} employee(s) compared\n")
    print(
        f"  {'Employee':<28} "
        f"{'Prev Net':>12} "
        f"{'Curr Net':>12} "
        f"{'Δ Net':>12} "
        f"{'Δ %':>10} "
        f"{'Missing Deductions'}"
    )
    print(f"  {'-'*28} {'-'*12} {'-'*12} {'-'*12} {'-'*10} {'-'*25}")

    for rec in records:
        missing = ", ".join(rec.missing_deduction_components) or "—"
        print(
            f"  {rec.employee_name:<28} "
            f"{fmt(rec.prev_net):>12} "
            f"{fmt(rec.curr_net):>12} "
            f"{fmt(rec.net_delta):>12} "
            f"{pct_str(rec.net_delta_pct):>10} "
            f"{missing}"
        )

    print("\n  ✅ ComparisonAgent complete")

    # -----------------------------------------------------------------------
    # STEP 3: AnomalyDetectorAgent
    # -----------------------------------------------------------------------
    separator("STEP 3: AnomalyDetectorAgent — Applying anomaly rules")

    anomaly_agent = AnomalyDetectorAgent()
    flagged = anomaly_agent.run(records)

    stats = anomaly_agent.summary_stats(flagged)
    print(f"\n  Flagged: {len(flagged)} / {len(records)} employee(s)\n")

    if stats:
        print("  Breakdown by reason:")
        for reason, count in stats.items():
            print(f"    {reason:<30} {count} employee(s)")

    if not flagged:
        print("\n  ✅ No anomalies detected — payroll looks clean")
    else:
        print(f"\n  {'#':<3} {'Employee':<28} {'Prev Net':>12} {'Curr Net':>12} {'Δ %':>10}  {'Reasons'}")
        print(f"  {'-'*3} {'-'*28} {'-'*12} {'-'*12} {'-'*10}  {'-'*40}")
        for i, fr in enumerate(flagged, 1):
            c = fr.comparison
            reasons_str = " | ".join(fr.anomaly_reasons)
            print(
                f"  {i:<3} {c.employee_name:<28} "
                f"{fmt(c.prev_net):>12} "
                f"{fmt(c.curr_net):>12} "
                f"{pct_str(c.net_delta_pct):>10}  "
                f"{reasons_str}"
            )

        print("\n  Detailed anomaly cards:")
        for fr in flagged:
            c = fr.comparison
            print(f"\n  ┌─ {c.employee_name} ({c.employee_id})")
            print(f"  │  Previous net pay : {c.prev_net}")
            print(f"  │  Current net pay  : {c.curr_net}")
            print(f"  │  Change           : {c.net_delta_pct}%")
            print(f"  │  Prev gross       : {c.prev_gross}")
            print(f"  │  Curr gross       : {c.curr_gross}")
            print(f"  │  Prev deductions  : {c.prev_deductions}")
            print(f"  │  Curr deductions  : {c.curr_deductions}")
            if c.missing_deduction_components:
                print(f"  │  Missing comps    : {', '.join(c.missing_deduction_components)}")
            print(f"  └─ Reasons          : {' | '.join(fr.anomaly_reasons)}")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    separator("PHASE 4 COMPLETE")
    print(f"  DataFetchAgent    ✅  ({len(data.current_summaries)} current + {len(data.previous_summaries)} previous slips)")
    print(f"  ComparisonAgent   ✅  ({len(records)} comparison records)")
    print(f"  AnomalyDetector   ✅  ({len(flagged)} anomalies flagged)")
    print()
    print("  Next: Phase 5 — CategorizationAgent (LLM) + ReportBuilderAgent")
    print()


if __name__ == "__main__":
    asyncio.run(main())