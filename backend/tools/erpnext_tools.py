"""
tools/erpnext_tools.py

All ERPNext data-fetching functions live here.
Every function is:
  - async (compatible with FastAPI + httpx)
  - typed (returns structured dicts or Pydantic models)
  - single-responsibility (does exactly one thing)

These functions are called by DataFetchAgent.
In Phase 6, they will be wrapped with @tool for Agno.

Date logic:
  - "current month" = the calendar month of today's date
  - "previous month" = the calendar month immediately before current
  - Both Draft (docstatus=0) and Submitted (docstatus=1) slips are included
"""

import calendar
import logging
from datetime import date, timedelta

from services.erpnext_client import erpnext, ERPNextError
from schemas.models import (
    EmployeeDetail,
    EmployeeSummary,
    SalaryComponent,
    SalarySlipDetail,
    SalarySlipSummary,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date helpers — private to this module
# ---------------------------------------------------------------------------


def _current_month_range() -> tuple[str, str]:
    """Returns (first_day, last_day) of the current calendar month as ISO strings."""
    today = date.today()
    first = today.replace(day=1)
    last_day_num = calendar.monthrange(today.year, today.month)[1]
    last = today.replace(day=last_day_num)
    return first.isoformat(), last.isoformat()


def _previous_month_range() -> tuple[str, str]:
    """Returns (first_day, last_day) of the previous calendar month as ISO strings."""
    today = date.today()
    first_this_month = today.replace(day=1)
    last_day_prev = first_this_month - timedelta(days=1)
    first_day_prev = last_day_prev.replace(day=1)
    return first_day_prev.isoformat(), last_day_prev.isoformat()


def _month_label(first: str) -> str:
    """Converts '2026-04-01' → '2026-04'."""
    return first[:7]


# ---------------------------------------------------------------------------
# Tool 1: Fetch current month salary slips
# ---------------------------------------------------------------------------


async def fetch_salary_slips_current_month() -> list[SalarySlipSummary]:
    """
    Fetch all salary slips (Draft + Submitted) for the current calendar month.

    ERPNext filter: start_date falls within this month's date range.
    Returns a list of SalarySlipSummary objects — lightweight, no component breakdown.

    Raises:
        ERPNextError: if the API call fails.
    """
    first, last = _current_month_range()
    logger.info("Fetching current month salary slips: %s → %s", first, last)

    raw = await erpnext.get_list(
        doctype="Salary Slip",
        filters=[
            ["start_date", ">=", first],
            ["start_date", "<=", last],
            ["docstatus", "in", [0, 1]],
        ],
        fields=[
            "name",
            "employee",
            "employee_name",
            "net_pay",
            "gross_pay",
            "total_deduction",
            "start_date",
            "end_date",
            "docstatus",
        ],
        limit=500,
    )

    slips = [SalarySlipSummary(**row) for row in raw]
    logger.info("Current month: found %d salary slip(s)", len(slips))
    return slips


# ---------------------------------------------------------------------------
# Tool 2: Fetch previous month salary slips
# ---------------------------------------------------------------------------


async def fetch_salary_slips_previous_month() -> list[SalarySlipSummary]:
    """
    Fetch all salary slips (Draft + Submitted) for the previous calendar month.

    Same structure as fetch_salary_slips_current_month() — different date range.

    Raises:
        ERPNextError: if the API call fails.
    """
    first, last = _previous_month_range()
    logger.info("Fetching previous month salary slips: %s → %s", first, last)

    raw = await erpnext.get_list(
        doctype="Salary Slip",
        filters=[
            ["start_date", ">=", first],
            ["start_date", "<=", last],
            ["docstatus", "in", [0, 1]],
        ],
        fields=[
            "name",
            "employee",
            "employee_name",
            "net_pay",
            "gross_pay",
            "total_deduction",
            "start_date",
            "end_date",
            "docstatus",
        ],
        limit=500,
    )

    slips = [SalarySlipSummary(**row) for row in raw]
    logger.info("Previous month: found %d salary slip(s)", len(slips))
    return slips


# ---------------------------------------------------------------------------
# Tool 3: Get all active employees
# ---------------------------------------------------------------------------


async def get_all_active_employees() -> list[EmployeeSummary]:
    """
    Fetch all Active employees from ERPNext.

    Used by DataFetchAgent to:
      - Cross-reference which employees have no slip this month (missing_slip)
      - Provide employee roster count for the report

    Raises:
        ERPNextError: if the API call fails.
    """
    logger.info("Fetching all active employees")

    raw = await erpnext.get_list(
        doctype="Employee",
        filters=[["status", "=", "Active"]],
        fields=["name", "employee_name", "department", "status"],
        limit=500,
    )

    employees = [EmployeeSummary(**row) for row in raw]
    logger.info("Found %d active employee(s)", len(employees))
    return employees


# ---------------------------------------------------------------------------
# Tool 4: Get single employee details
# ---------------------------------------------------------------------------


async def get_employee_details(employee_id: str) -> EmployeeDetail:
    """
    Fetch full details for a single employee by their ERPNext ID.

    Used by CategorizationAgent to enrich anomaly context:
      - date_of_joining → determines if employee is new
      - designation / department → context for salary revision judgement

    Args:
        employee_id: ERPNext Employee document name, e.g. "EMP-0001"

    Returns:
        EmployeeDetail with all available fields.

    Raises:
        ERPNextError: if the employee does not exist or the API call fails.
    """
    logger.info("Fetching employee details for: %s", employee_id)

    raw = await erpnext.get_document("Employee", employee_id)

    # Map to EmployeeDetail — ignore extra fields ERPNext might return
    return EmployeeDetail(
        name=raw.get("name", employee_id),
        employee_name=raw.get("employee_name", ""),
        department=raw.get("department"),
        date_of_joining=raw.get("date_of_joining"),
        status=raw.get("status"),
        salary_mode=raw.get("salary_mode"),
        designation=raw.get("designation"),
        company=raw.get("company"),
    )


# ---------------------------------------------------------------------------
# Tool 5: Get full salary slip with component breakdown
# ---------------------------------------------------------------------------


async def fetch_salary_slip_details(slip_name: str) -> SalarySlipDetail:
    """
    Fetch the complete salary slip document including all earning and
    deduction components.

    Used by DataFetchAgent to enable component-level deduction comparison.
    For example: if "Professional Tax" was deducted last month but not this
    month, it appears in missing_deduction_components on the ComparisonRecord.

    Args:
        slip_name: ERPNext Salary Slip document name, e.g. "SAL-SLIP-00001"

    Returns:
        SalarySlipDetail with earnings[] and deductions[] component lists.

    Raises:
        ERPNextError: if the slip does not exist or the API call fails.
    """
    logger.info("Fetching salary slip details for: %s", slip_name)

    raw = await erpnext.get_document("Salary Slip", slip_name)

    # Parse earnings and deductions sub-tables
    earnings = [
        SalaryComponent(
            salary_component=row.get("salary_component", ""),
            amount=float(row.get("amount", 0)),
            component_type="earning",
        )
        for row in raw.get("earnings", [])
    ]

    deductions = [
        SalaryComponent(
            salary_component=row.get("salary_component", ""),
            amount=float(row.get("amount", 0)),
            component_type="deduction",
        )
        for row in raw.get("deductions", [])
    ]

    return SalarySlipDetail(
        name=raw.get("name", slip_name),
        employee=raw.get("employee", ""),
        employee_name=raw.get("employee_name", ""),
        net_pay=float(raw.get("net_pay", 0)),
        gross_pay=float(raw.get("gross_pay", 0)),
        total_deduction=float(raw.get("total_deduction", 0)),
        start_date=raw.get("start_date"),
        end_date=raw.get("end_date"),
        docstatus=int(raw.get("docstatus", 0)),
        earnings=earnings,
        deductions=deductions,
    )


# ---------------------------------------------------------------------------
# Convenience: fetch both months in one call
# ---------------------------------------------------------------------------


async def fetch_both_months() -> dict:
    """
    Fetch current and previous month slips together.
    Returns a dict with keys: current_slips, previous_slips, period_current, period_previous.

    Used by DataFetchAgent to reduce the number of sequential awaits needed.
    """
    curr_first, _ = _current_month_range()
    prev_first, _ = _previous_month_range()

    current_slips = await fetch_salary_slips_current_month()
    previous_slips = await fetch_salary_slips_previous_month()

    return {
        "current_slips": current_slips,
        "previous_slips": previous_slips,
        "period_current": _month_label(curr_first),
        "period_previous": _month_label(prev_first),
    }