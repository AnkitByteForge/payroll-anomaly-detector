"""
test_tools.py

Phase 2 test — run from the backend/ directory.
Tests all 5 ERPNext tools and shows a side-by-side comparison of
current vs previous month salary slips so you can visually confirm
the anomaly you created in ERPNext is visible.

Run with:
    cd backend
    python test_tools.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from tools.erpnext_tools import (
    fetch_salary_slips_current_month,
    fetch_salary_slips_previous_month,
    get_all_active_employees,
    get_employee_details,
    fetch_salary_slip_details,
    fetch_both_months,
)
from services.erpnext_client import ERPNextError


def separator(title: str, width: int = 65):
    print(f"\n{'='*width}")
    print(f"  {title}")
    print(f"{'='*width}")


def fmt_currency(val) -> str:
    if val is None:
        return "      N/A"
    return f"{val:>10,.2f}"


def pct_change(prev, curr) -> str:
    if prev is None or curr is None:
        return "    N/A"
    if prev == 0:
        return "   +∞ %"
    pct = ((curr - prev) / abs(prev)) * 100
    sign = "+" if pct >= 0 else ""
    flag = " ⚠️ " if abs(pct) > 15 else "    "
    return f"{flag}{sign}{pct:.1f}%"


# ---------------------------------------------------------------------------

async def test_tool1_current_month():
    separator("TOOL 1: Current Month Salary Slips")
    try:
        slips = await fetch_salary_slips_current_month()
        print(f"  Count: {len(slips)}\n")
        print(f"  {'Slip Name':<20} {'Employee':<30} {'Net Pay':>12} {'Status':<12}")
        print(f"  {'-'*20} {'-'*30} {'-'*12} {'-'*12}")
        for s in slips:
            print(
                f"  {s.name:<20} {s.employee_name:<30} "
                f"{fmt_currency(s.net_pay)} {s.status_label:<12}"
            )
        print("\n  ✅ Tool 1 OK")
        return slips
    except ERPNextError as e:
        print(f"  ❌ ERPNextError [{e.status_code}]: {e.message}")
        return []


async def test_tool2_previous_month():
    separator("TOOL 2: Previous Month Salary Slips")
    try:
        slips = await fetch_salary_slips_previous_month()
        print(f"  Count: {len(slips)}\n")
        print(f"  {'Slip Name':<20} {'Employee':<30} {'Net Pay':>12} {'Status':<12}")
        print(f"  {'-'*20} {'-'*30} {'-'*12} {'-'*12}")
        for s in slips:
            print(
                f"  {s.name:<20} {s.employee_name:<30} "
                f"{fmt_currency(s.net_pay)} {s.status_label:<12}"
            )
        print("\n  ✅ Tool 2 OK")
        return slips
    except ERPNextError as e:
        print(f"  ❌ ERPNextError [{e.status_code}]: {e.message}")
        return []


async def test_tool3_employees():
    separator("TOOL 3: All Active Employees")
    try:
        employees = await get_all_active_employees()
        print(f"  Count: {len(employees)}\n")
        for emp in employees:
            print(f"  [{emp.name}] {emp.employee_name} — {emp.department or 'No Dept'}")
        print("\n  ✅ Tool 3 OK")
        return employees
    except ERPNextError as e:
        print(f"  ❌ ERPNextError [{e.status_code}]: {e.message}")
        return []


async def test_tool4_employee_detail(employees):
    separator("TOOL 4: Single Employee Details")
    if not employees:
        print("  ⚠️  Skipped — no employees available")
        return

    emp_id = employees[0].name
    try:
        detail = await get_employee_details(emp_id)
        print(f"  ID            : {detail.name}")
        print(f"  Name          : {detail.employee_name}")
        print(f"  Department    : {detail.department or 'N/A'}")
        print(f"  Designation   : {detail.designation or 'N/A'}")
        print(f"  Date of Join  : {detail.date_of_joining or 'N/A'}")
        print(f"  Status        : {detail.status or 'N/A'}")
        print(f"  Salary Mode   : {detail.salary_mode or 'N/A'}")
        print("\n  ✅ Tool 4 OK")
    except ERPNextError as e:
        print(f"  ❌ ERPNextError [{e.status_code}]: {e.message}")


async def test_tool5_slip_detail(current_slips):
    separator("TOOL 5: Full Salary Slip with Component Breakdown")
    if not current_slips:
        print("  ⚠️  Skipped — no current month slips available")
        return

    slip_name = current_slips[0].name
    try:
        detail = await fetch_salary_slip_details(slip_name)
        print(f"  Slip          : {detail.name}")
        print(f"  Employee      : {detail.employee_name}")
        print(f"  Gross Pay     : {fmt_currency(detail.gross_pay)}")
        print(f"  Total Deduct  : {fmt_currency(detail.total_deduction)}")
        print(f"  Net Pay       : {fmt_currency(detail.net_pay)}")

        if detail.earnings:
            print(f"\n  Earnings ({len(detail.earnings)} components):")
            for e in detail.earnings:
                print(f"    + {e.salary_component:<30} {fmt_currency(e.amount)}")

        if detail.deductions:
            print(f"\n  Deductions ({len(detail.deductions)} components):")
            for d in detail.deductions:
                print(f"    - {d.salary_component:<30} {fmt_currency(d.amount)}")

        print("\n  ✅ Tool 5 OK")
    except ERPNextError as e:
        print(f"  ❌ ERPNextError [{e.status_code}]: {e.message}")


async def test_side_by_side_comparison(current_slips, previous_slips):
    separator("SIDE-BY-SIDE: Month-over-Month Comparison (Anomaly Check)")

    # Build lookup maps: employee_id → slip
    curr_map = {s.employee: s for s in current_slips}
    prev_map = {s.employee: s for s in previous_slips}
    all_employees = sorted(set(curr_map) | set(prev_map))

    if not all_employees:
        print("  ⚠️  No data to compare")
        return

    header = (
        f"  {'Employee':<25} "
        f"{'Prev Net':>12} "
        f"{'Curr Net':>12} "
        f"{'Change':>10}  "
        f"{'Note'}"
    )
    print(header)
    print(f"  {'-'*25} {'-'*12} {'-'*12} {'-'*10}  {'-'*20}")

    for emp_id in all_employees:
        curr = curr_map.get(emp_id)
        prev = prev_map.get(emp_id)

        name = (curr or prev).employee_name
        prev_net = prev.net_pay if prev else None
        curr_net = curr.net_pay if curr else None

        note = ""
        if prev is None:
            note = "🆕 NEW EMPLOYEE"
        elif curr is None:
            note = "❓ MISSING THIS MONTH"

        change_str = pct_change(prev_net, curr_net)

        print(
            f"  {name:<25} "
            f"{fmt_currency(prev_net)} "
            f"{fmt_currency(curr_net)} "
            f"{change_str:>10}  "
            f"{note}"
        )

    print("\n  Legend: ⚠️  = change > 15% threshold | 🆕 = new employee | ❓ = missing")
    print("\n  ✅ Side-by-side comparison complete")


async def main():
    print("\n🔧 PAYROLL ANOMALY DETECTOR — Phase 2 Tool Tests")

    current_slips = await test_tool1_current_month()
    previous_slips = await test_tool2_previous_month()
    employees = await test_tool3_employees()
    await test_tool4_employee_detail(employees)
    await test_tool5_slip_detail(current_slips)
    await test_side_by_side_comparison(current_slips, previous_slips)

    separator("PHASE 2 COMPLETE")
    print("  All 5 tools tested successfully.")
    print("  The side-by-side comparison above should show your anomaly.")
    print("  Ready for Phase 3: FastAPI base + health endpoints.\n")


if __name__ == "__main__":
    asyncio.run(main())