"""
agents/comparison.py

ComparisonAgent — pure computation, no I/O, no LLM.

Changes from previous version:
  - BUG FIX: missing_deduction_components is now always [] when curr_slip
    is None. Previously the diff ran against an empty list and falsely
    flagged every previous deduction component as "missing".

Everything else is identical to the previous version.

Agno compliance:
  - AgnoComparisonAgent wraps ComparisonAgent as an agno.agent.Agent.
  - The wrapper delegates ALL logic to ComparisonAgent.run().
  - Deterministic behaviour is completely preserved.
"""

import logging

from agno.agent.agent import Agent
from agents.data_fetch import FetchedPayrollData
from schemas.models import ComparisonRecord, SalarySlipSummary

logger = logging.getLogger(__name__)


class ComparisonAgent:

    def run(self, data: FetchedPayrollData) -> list[ComparisonRecord]:
        logger.info("[ComparisonAgent] Building comparison records")

        curr_map = self._best_slip_per_employee(data.current_summaries)
        prev_map = self._best_slip_per_employee(data.previous_summaries)

        curr_detail_map = data.current_details
        prev_detail_map = data.previous_details

        all_employee_ids = sorted(set(curr_map) | set(prev_map))
        records: list[ComparisonRecord] = []

        for emp_id in all_employee_ids:
            curr_slip = curr_map.get(emp_id)
            prev_slip = prev_map.get(emp_id)

            curr_detail = (
                curr_detail_map.get(curr_slip.name) if curr_slip else None
            )
            prev_detail = (
                prev_detail_map.get(prev_slip.name) if prev_slip else None
            )

            curr_deduction_components = (
                [d.salary_component for d in curr_detail.deductions]
                if curr_detail else []
            )
            prev_deduction_components = (
                [d.salary_component for d in prev_detail.deductions]
                if prev_detail else []
            )

            # ---------------------------------------------------------------
            # BUG FIX: only diff deduction components when BOTH slips exist.
            #
            # When curr_slip is None the employee has a missing_slip, not a
            # missing_deduction. Running the diff against an empty list would
            # falsely report every previous deduction as "missing".
            # ---------------------------------------------------------------
            if curr_slip is None:
                missing_deduction_components = []
            else:
                missing_deduction_components = [
                    c for c in prev_deduction_components
                    if c not in curr_deduction_components
                ]

            prev_net = prev_slip.net_pay if prev_slip else None
            curr_net = curr_slip.net_pay if curr_slip else None
            net_delta, net_delta_pct = self._calculate_delta(prev_net, curr_net)

            prev_ded = prev_slip.total_deduction if prev_slip else None
            curr_ded = curr_slip.total_deduction if curr_slip else None
            deduction_delta = (
                round(curr_ded - prev_ded, 2)
                if curr_ded is not None and prev_ded is not None else None
            )

            prev_gross = prev_slip.gross_pay if prev_slip else None
            curr_gross = curr_slip.gross_pay if curr_slip else None

            employee_name = (curr_slip or prev_slip).employee_name  # type: ignore

            records.append(ComparisonRecord(
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
            ))

        logger.info("[ComparisonAgent] Built %d comparison record(s)", len(records))
        return records

    def _best_slip_per_employee(
        self, slips: list[SalarySlipSummary]
    ) -> dict[str, SalarySlipSummary]:
        emp_map: dict[str, SalarySlipSummary] = {}
        for slip in slips:
            emp_id = slip.employee
            if emp_id not in emp_map:
                emp_map[emp_id] = slip
            else:
                existing = emp_map[emp_id]
                if slip.docstatus > existing.docstatus:
                    emp_map[emp_id] = slip
                elif (
                    slip.docstatus == existing.docstatus
                    and slip.net_pay > existing.net_pay
                ):
                    emp_map[emp_id] = slip
        return emp_map

    def _calculate_delta(
        self, prev: float | None, curr: float | None
    ) -> tuple[float | None, float | None]:
        if prev is None or curr is None:
            return None, None
        delta = round(curr - prev, 2)
        if prev == 0:
            pct = 100.0 if curr > 0 else 0.0
        else:
            pct = round((delta / abs(prev)) * 100, 2)
        return delta, pct


# ---------------------------------------------------------------------------
# Agno compliance wrapper
# ---------------------------------------------------------------------------


class AgnoComparisonAgent(Agent):
    """
    Agno-compliant wrapper around ComparisonAgent.

    Exposes the existing deterministic comparison logic as an agno.agent.Agent
    so the PayrollAnalysisTeam (agno.team.Team) can list it as a named member.

    The wrapper does NOT move any business logic into the prompt — the
    ComparisonAgent.run() method is called directly and its output is
    returned unchanged.
    """

    def __init__(self):
        super().__init__(
            name="ComparisonAgent",
            description=(
                "Month-over-month payroll comparison agent. "
                "Computes net pay deltas, gross pay deltas, deduction deltas, "
                "and missing deduction components for every employee. "
                "Pure deterministic computation — no LLM."
            ),
        )
        self._impl = ComparisonAgent()
        logger.debug("[AgnoComparisonAgent] Initialised (wraps ComparisonAgent)")

    def run_comparison(self, data):
        """Delegate to the underlying ComparisonAgent."""
        return self._impl.run(data)