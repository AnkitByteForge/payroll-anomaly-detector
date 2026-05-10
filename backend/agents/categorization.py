"""
agents/categorization.py

CategorizationAgent — the ONLY agent that uses the LLM (mandatory per spec).

Responsibilities:
  - For each FlaggedRecord, call the LLM once with structured employee context
  - The LLM assigns: anomaly_category, suggested_action, explanation
  - Falls back to rule-based categorization if the LLM call fails
  - Returns list[AnomalyRecord] — fully enriched, ready for ReportBuilderAgent

LLM is used here because:
  - Categorization requires nuanced judgment (is this a planned revision or an error?)
  - Employee context (joining date, department) affects the decision
  - Natural language explanations are more useful to HR than codes

Input:  list[FlaggedRecord] + dict[employee_id, EmployeeDetail]
Output: list[AnomalyRecord]
"""

import asyncio
import logging

from agents.data_fetch import FetchedPayrollData
from schemas.models import (
    AnomalyCategory,
    AnomalyRecord,
    EmployeeDetail,
    FlaggedRecord,
)
from services.llm_client import llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a payroll audit assistant for an HR team.
You analyze salary data anomalies and classify them accurately.
You respond ONLY with a valid JSON object — no markdown, no explanation outside the JSON.

The JSON must have exactly these three keys:
  "category":         one of: new_employee | salary_revision | data_error | missing_deduction | missing_slip
  "suggested_action": a short, specific action for the HR team (1-2 sentences)
  "explanation":      a brief plain-English explanation of why this is flagged (1-2 sentences)

Category definitions:
  new_employee      — employee has no previous month slip (recently joined)
  salary_revision   — net pay changed significantly, likely due to a planned appraisal or promotion
  data_error        — large unexplained change with no obvious cause; needs manual investigation
  missing_deduction — one or more deduction components present last month are absent this month
  missing_slip      — employee was in last month's payroll but has no slip this month
"""


def _build_user_prompt(
    flagged: FlaggedRecord,
    employee: EmployeeDetail | None,
) -> str:
    """Construct the user-turn prompt for one flagged employee."""
    comp = flagged.comparison

    # Format currency safely
    def fmt(v) -> str:
        return f"{v:,.2f}" if v is not None else "N/A"

    def pct(v) -> str:
        if v is None:
            return "N/A"
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.1f}%"

    # Employee context block
    emp_context = ""
    if employee:
        emp_context = f"""
Employee Context:
  - Date of joining  : {employee.date_of_joining or 'Unknown'}
  - Department       : {employee.department or 'Unknown'}
  - Designation      : {employee.designation or 'Unknown'}
  - Status           : {employee.status or 'Unknown'}"""

    # Deduction components block
    deduction_info = ""
    if comp.missing_deduction_components:
        comps = ", ".join(comp.missing_deduction_components)
        deduction_info = f"\n  - Deduction components missing this month: {comps}"

    return f"""Analyze this payroll anomaly and classify it:

Employee: {comp.employee_name} ({comp.employee_id})

Pay Comparison:
  - Previous month net pay  : {fmt(comp.prev_net)}
  - Current month net pay   : {fmt(comp.curr_net)}
  - Change                  : {fmt(comp.net_delta)} ({pct(comp.net_delta_pct)})
  - Previous gross pay      : {fmt(comp.prev_gross)}
  - Current gross pay       : {fmt(comp.curr_gross)}
  - Previous total deductions: {fmt(comp.prev_deductions)}
  - Current total deductions : {fmt(comp.curr_deductions)}{deduction_info}

System-detected anomaly reasons: {", ".join(flagged.anomaly_reasons)}
{emp_context}

Classify this anomaly. Respond with JSON only."""


# ---------------------------------------------------------------------------
# Fallback: rule-based categorization when LLM is unavailable
# ---------------------------------------------------------------------------

def _fallback_category(reasons: list[str]) -> AnomalyCategory:
    """Determine category from reasons list without LLM."""
    if "new_employee" in reasons:
        return AnomalyCategory.new_employee
    if "missing_slip" in reasons:
        return AnomalyCategory.missing_slip
    if "missing_deduction" in reasons or "deduction_anomaly" in reasons:
        return AnomalyCategory.missing_deduction
    if "large_net_change" in reasons:
        return AnomalyCategory.data_error
    return AnomalyCategory.data_error


def _fallback_action(category: AnomalyCategory) -> str:
    """Generate a suggested action without LLM."""
    actions = {
        AnomalyCategory.new_employee: "Verify onboarding date and confirm salary structure is correctly assigned.",
        AnomalyCategory.salary_revision: "Cross-check with the approved salary revision letter.",
        AnomalyCategory.data_error: "Escalate to payroll admin for immediate manual review.",
        AnomalyCategory.missing_deduction: "Check if the deduction component was accidentally removed from the salary structure.",
        AnomalyCategory.missing_slip: "Verify if the employee is still active and re-run payroll if required.",
    }
    return actions.get(category, "Manual review required.")


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class CategorizationAgent:
    """
    Enriches flagged payroll anomalies using LLM-based classification.

    Each flagged record is sent to the LLM with full employee context.
    Results are returned as AnomalyRecord objects with:
      - anomaly_category (enum)
      - suggested_action (HR-friendly action string)
      - llm_explanation (brief plain-English explanation)

    Call:
        agent = CategorizationAgent()
        anomalies = await agent.run(flagged_records, employee_details)
    """

    async def run(
        self,
        flagged_records: list[FlaggedRecord],
        employee_details: dict[str, EmployeeDetail],
    ) -> list[AnomalyRecord]:
        """
        Categorize all flagged records in parallel.

        Args:
            flagged_records:  Output from AnomalyDetectorAgent.
            employee_details: dict of employee_id → EmployeeDetail (from DataFetchAgent).

        Returns:
            List of AnomalyRecord, one per flagged employee.
        """
        if not flagged_records:
            logger.info("[CategorizationAgent] No flagged records to categorize")
            return []

        logger.info(
            "[CategorizationAgent] Categorizing %d flagged record(s) via LLM",
            len(flagged_records),
        )

        # Run all LLM calls concurrently
        tasks = [
            self._categorize_one(fr, employee_details.get(fr.comparison.employee_id))
            for fr in flagged_records
        ]
        results = await asyncio.gather(*tasks)

        logger.info("[CategorizationAgent] Categorization complete")
        return list(results)

    async def _categorize_one(
        self,
        flagged: FlaggedRecord,
        employee: EmployeeDetail | None,
    ) -> AnomalyRecord:
        """
        Categorize a single flagged record.

        Tries LLM first. Falls back to rule-based if:
          - LLM API call fails
          - LLM returns invalid JSON
          - LLM returns an unrecognized category value
        """
        comp = flagged.comparison
        logger.debug("[CategorizationAgent] Processing: %s", comp.employee_name)

        category = None
        suggested_action = ""
        explanation = ""
        used_fallback = False

        try:
            prompt = _build_user_prompt(flagged, employee)
            result = await llm.ask_json(prompt, system_prompt=_SYSTEM_PROMPT)

            # Validate category value — reject anything not in the enum
            raw_category = result.get("category", "")
            try:
                category = AnomalyCategory(raw_category)
            except ValueError:
                logger.warning(
                    "[CategorizationAgent] LLM returned unknown category '%s' for %s — using fallback",
                    raw_category,
                    comp.employee_name,
                )
                category = _fallback_category(flagged.anomaly_reasons)
                used_fallback = True

            suggested_action = result.get("suggested_action", "").strip()
            explanation = result.get("explanation", "").strip()

            if not suggested_action:
                suggested_action = _fallback_action(category)

        except Exception as e:
            logger.warning(
                "[CategorizationAgent] LLM failed for %s: %s — using fallback",
                comp.employee_name,
                e,
            )
            category = _fallback_category(flagged.anomaly_reasons)
            suggested_action = _fallback_action(category)
            explanation = "Automated rule-based classification (LLM unavailable)."
            used_fallback = True

        if used_fallback:
            logger.info(
                "[CategorizationAgent] %s → %s (fallback)",
                comp.employee_name,
                category.value,
            )
        else:
            logger.info(
                "[CategorizationAgent] %s → %s (LLM)",
                comp.employee_name,
                category.value,
            )

        return AnomalyRecord(
            employee_id=comp.employee_id,
            employee_name=comp.employee_name,
            prev_net_pay=comp.prev_net,
            curr_net_pay=comp.curr_net,
            pct_change=comp.net_delta_pct,
            prev_deductions=comp.prev_deductions,
            curr_deductions=comp.curr_deductions,
            missing_deduction_components=comp.missing_deduction_components,
            anomaly_category=category,
            anomaly_reasons=flagged.anomaly_reasons,
            suggested_action=suggested_action,
            llm_explanation=explanation,
        )