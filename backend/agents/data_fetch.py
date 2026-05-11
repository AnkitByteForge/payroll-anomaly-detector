"""
agents/data_fetch.py

DataFetchAgent — the only agent that talks to ERPNext.

Responsibilities:
  - Fetch current and previous month salary slip summaries
  - Fetch full slip details (with component breakdown) for deduction analysis
  - Fetch all active employees + individual employee records for context
  - Return one clean, normalized FetchedPayrollData object

Design decisions:
  - asyncio.gather() is used for parallel API calls to minimize latency
  - Failed detail fetches are logged and skipped (non-fatal) so one bad slip
    doesn't abort the entire run
  - This agent returns raw data only — no business logic lives here
"""

import asyncio
import logging
from dataclasses import dataclass, field

from schemas.models import (
    EmployeeDetail,
    EmployeeSummary,
    SalarySlipDetail,
    SalarySlipSummary,
)
from services.erpnext_client import ERPNextError
from tools.erpnext_tools import (
    fetch_both_months,
    fetch_salary_slip_details,
    get_all_active_employees,
    get_employee_details,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal data container — produced by this agent, consumed by ComparisonAgent
# ---------------------------------------------------------------------------


@dataclass
class FetchedPayrollData:
    """
    All raw payroll data needed for the comparison pipeline.
    Passed from DataFetchAgent → ComparisonAgent.
    """

    # Lightweight slip summaries (from list endpoint)
    current_summaries: list[SalarySlipSummary] = field(default_factory=list)
    previous_summaries: list[SalarySlipSummary] = field(default_factory=list)

    # Full slip details keyed by slip name (from document endpoint)
    # Used for component-level deduction comparison
    current_details: dict[str, SalarySlipDetail] = field(default_factory=dict)
    previous_details: dict[str, SalarySlipDetail] = field(default_factory=dict)

    # All active employees (for "missing slip" detection)
    employees: list[EmployeeSummary] = field(default_factory=list)

    # Employee detail keyed by employee_id (for CategorizationAgent context)
    employee_details: dict[str, EmployeeDetail] = field(default_factory=dict)

    # Period labels e.g. "2026-05", "2026-04"
    period_current: str = ""
    period_previous: str = ""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class DataFetchAgent:
    """
    Fetches all data needed for payroll anomaly detection from ERPNext.

    Call:
        agent = DataFetchAgent()
        data = await agent.run()
    """

    async def run(self) -> FetchedPayrollData:
        """
        Execute the full data fetch pipeline.

        Step 1: Fetch salary slip summaries + active employees in parallel.
        Step 2: Fetch detailed slips in parallel (for deduction components).
        Step 3: Fetch individual employee records in parallel (for context).
        """
        logger.info("[DataFetchAgent] Starting data fetch")

        # -------------------------------------------------------------------
        # Step 1: Parallel fetch of summaries + employee roster
        # -------------------------------------------------------------------
        month_data, employees = await asyncio.gather(
            fetch_both_months(),
            get_all_active_employees(),
        )

        current_summaries: list[SalarySlipSummary] = month_data["current_slips"]
        previous_summaries: list[SalarySlipSummary] = month_data["previous_slips"]
        period_current: str = month_data["period_current"]
        period_previous: str = month_data["period_previous"]

        logger.info(
            "[DataFetchAgent] Summaries fetched — current: %d, previous: %d, employees: %d",
            len(current_summaries),
            len(previous_summaries),
            len(employees),
        )

        # -------------------------------------------------------------------
        # Step 2: Fetch full slip details in parallel (component breakdown)
        # -------------------------------------------------------------------
        current_details = await self._fetch_details_parallel(
            current_summaries, label="current"
        )
        previous_details = await self._fetch_details_parallel(
            previous_summaries, label="previous"
        )

        # -------------------------------------------------------------------
        # Step 3: Fetch employee records for all employees in either month
        # -------------------------------------------------------------------
        all_employee_ids = {s.employee for s in current_summaries} | {
            s.employee for s in previous_summaries
        }
        employee_details = await self._fetch_employee_details_parallel(
            list(all_employee_ids)
        )

        logger.info(
            "[DataFetchAgent] Done — %d current details, %d previous details, %d employee records",
            len(current_details),
            len(previous_details),
            len(employee_details),
        )

        return FetchedPayrollData(
            current_summaries=current_summaries,
            previous_summaries=previous_summaries,
            current_details=current_details,
            previous_details=previous_details,
            employees=employees,
            employee_details=employee_details,
            period_current=period_current,
            period_previous=period_previous,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fetch_details_parallel(
        self,
        summaries: list[SalarySlipSummary],
        label: str = "",
    ) -> dict[str, SalarySlipDetail]:
        """
        Fetch full details for a list of salary slips in parallel.

        Uses asyncio.gather with return_exceptions=True so a single
        failed fetch doesn't cancel the rest. Failed slips are logged
        and excluded from the result.

        Returns:
            Dict keyed by slip name → SalarySlipDetail
        """
        if not summaries:
            return {}

        tasks = [fetch_salary_slip_details(s.name) for s in summaries]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        details: dict[str, SalarySlipDetail] = {}
        for slip, result in zip(summaries, results):
            if isinstance(result, BaseException):
                logger.warning(
                    "[DataFetchAgent] Could not fetch detail for slip %s (%s): %s",
                    slip.name,
                    label,
                    result,
                )
            else:
                details[slip.name] = result

        return details

    async def _fetch_employee_details_parallel(
        self,
        employee_ids: list[str],
    ) -> dict[str, EmployeeDetail]:
        """
        Fetch full employee records in parallel.
        Returns dict keyed by employee_id → EmployeeDetail.
        """
        if not employee_ids:
            return {}

        tasks = [get_employee_details(emp_id) for emp_id in employee_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        details: dict[str, EmployeeDetail] = {}
        for emp_id, result in zip(employee_ids, results):
            if isinstance(result, BaseException):
                logger.warning(
                    "[DataFetchAgent] Could not fetch employee detail for %s: %s",
                    emp_id,
                    result,
                )
            else:
                details[emp_id] = result

        return details