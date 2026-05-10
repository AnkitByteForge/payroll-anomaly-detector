"""
agents/comparison.py

ComparisonAgent — pure computation, no I/O, no LLM.

Responsibilities:
  - Build a ComparisonRecord per employee using current and previous month data
  - Handle all edge cases: new employee, missing slip, multiple slips, zero pay
  - Detect missing deduction components (component in prev month, absent this month)
  - Round all percentage values to 2 decimal places

Input:  FetchedPayrollData (from DataFetchAgent)
Output: list[ComparisonRecord]
"""

import logging

from agents.data_fetch import FetchedPayrollData
from schemas.models import ComparisonRecord, SalarySlipSummary

logger = logging.getLogger(__name__)


class ComparisonAgent:
    """
    Compares current vs previous month salary data per employee.

    Call:
        agent = ComparisonAgent()
        records = agent.run(fetched_data)
    """

    def run(self, data: FetchedPayrollData) -> list[ComparisonRecord]:
        """
        Build comparison records for all employees appearing in either month.

        Returns:
            List of ComparisonRecord, one per unique employee_id.
        """
        logger.info("[ComparisonAgent] Building comparison records")

        # Deduplicate: if an employee has >1 slip in a month, pick the best one
        # Priority: Submitted (docstatus=1) > Draft (docstatus=0)
        curr_map = self._best_slip_per_employee(data.current_summaries)
        prev_map = self._best_slip_per_employee(data.previous_summaries)

        # Build a lookup: slip_name → detail (for component extraction)
        curr_detail_map = data.current_details   # slip_name → SalarySlipDetail
        prev_detail_map = data.previous_details  # slip_name → SalarySlipDetail

        # Union of all employee IDs across both months
        all_employee_ids = sorted(set(curr_map) | set(prev_map))

        records: list[ComparisonRecord] = []

        for emp_id in all_employee_ids:
            curr_slip = curr_map.get(emp_id)
            prev_slip = prev_map.get(emp_id)

            # Look up detailed slips for component analysis
            curr_detail = (
                curr_detail_map.get(curr_slip.name) if curr_slip else None
            )
            prev_detail = (
                prev_detail_map.get(prev_slip.name) if prev_slip else None
            )

            # Extract deduction component names from detailed slips
            curr_deduction_components = (
                [d.salary_component for d in curr_detail.deductions]
                if curr_detail
                else []
            )
            prev_deduction_components = (
                [d.salary_component for d in prev_detail.deductions]
                if prev_detail
                else []
            )

            # Components present last month but absent this month
            missing_deduction_components = [
                c
                for c in prev_deduction_components
                if c not in curr_deduction_components
            ]

            # Net pay values
            prev_net = float(prev_slip.net_pay) if prev_slip else None
            curr_net = float(curr_slip.net_pay) if curr_slip else None

            # Delta calculations
            net_delta, net_delta_pct = self._calculate_delta(prev_net, curr_net)

            # Deduction delta
            prev_ded = float(prev_slip.total_deduction) if prev_slip else None
            curr_ded = float(curr_slip.total_deduction) if curr_slip else None
            deduction_delta = (
                round(curr_ded - prev_ded, 2)
                if curr_ded is not None and prev_ded is not None
                else None
            )

            # Gross pay
            prev_gross = float(prev_slip.gross_pay) if prev_slip else None
            curr_gross = float(curr_slip.gross_pay) if curr_slip else None

            # Employee name — prefer current slip, fall back to previous
            employee_name = (curr_slip or prev_slip).employee_name  # type: ignore

            record = ComparisonRecord(
                employee_id=emp_id,
                employee_name=employee_name,
                prev_net=prev_net,
                curr_net=curr_net,
                net_delta=net_delta,
                net_delta_pct=net_delta_pct,
                prev_gross=prev_gross,
                curr_gross=curr_gross,
                prev_deductions=prev_ded,
                curr_deductions=curr_ded,
                deduction_delta=deduction_delta,
                prev_slip_name=prev_slip.name if prev_slip else None,
                curr_slip_name=curr_slip.name if curr_slip else None,
                prev_deduction_components=prev_deduction_components,
                curr_deduction_components=curr_deduction_components,
                missing_deduction_components=missing_deduction_components,
            )
            records.append(record)

        logger.info(
            "[ComparisonAgent] Built %d comparison record(s)", len(records)
        )
        return records

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _best_slip_per_employee(
        self,
        slips: list[SalarySlipSummary],
    ) -> dict[str, SalarySlipSummary]:
        """
        For each employee, select the single best slip from a list.

        Priority order (highest wins):
          1. Submitted (docstatus=1)
          2. Draft (docstatus=0)
          3. If same status, keep the one with the higher net_pay
             (handles arrear corrections / amended slips)
        """
        emp_map: dict[str, SalarySlipSummary] = {}

        for slip in slips:
            emp_id = slip.employee
            if emp_id not in emp_map:
                emp_map[emp_id] = slip
            else:
                existing = emp_map[emp_id]
                # Prefer higher docstatus (Submitted > Draft)
                if slip.docstatus > existing.docstatus:
                    emp_map[emp_id] = slip
                # Same docstatus — prefer higher net_pay
                elif (
                    slip.docstatus == existing.docstatus
                    and slip.net_pay > existing.net_pay
                ):
                    emp_map[emp_id] = slip

        return emp_map

    def _calculate_delta(
        self,
        prev: float | None,
        curr: float | None,
    ) -> tuple[float | None, float | None]:
        """
        Calculate absolute and percentage change between two values.

        Edge cases handled:
          - Either value is None → both deltas are None
          - prev is 0 → percentage is +100% if curr > 0, else 0%
          - Result rounded to 2 decimal places

        Returns:
            (net_delta, net_delta_pct) — both may be None
        """
        if prev is None or curr is None:
            return None, None

        delta = round(curr - prev, 2)

        if prev == 0:
            pct = 100.0 if curr > 0 else 0.0
        else:
            pct = round((delta / abs(prev)) * 100, 2)

        return delta, pct