"""
agents/categorization.py

CategorizationAgent — LLM used ONLY for human-readable text.

Architecture after this refactor:
  Step 1  _determine_category()      → deterministic, rule-based, no LLM
  Step 2  _assign_severity()         → deterministic, rule-based, no LLM
  Step 3  _requires_manual_review()  → deterministic, rule-based, no LLM
  Step 4  _llm_explain()             → LLM generates explanation + suggested_action
  Step 5  Assemble AnomalyRecord

Why deterministic category matters:
  - The same input always produces the same category regardless of LLM mood
  - Can be unit-tested without an API key
  - Category logic is auditable by reading code, not prompts
  - LLM hallucinations cannot corrupt the category field

Why LLM still adds value:
  - HR teams need natural language explanations, not codes
  - Suggested actions vary with department, joining date, designation context
  - Executive summaries require synthesis across multiple records

Category decision logic uses PAYROLL CONTEXT, not just percentage thresholds:
  - new_employee   → no previous slip exists
  - missing_slip   → no current slip exists
  - missing_deduction → specific component names disappeared between months
  - salary_revision → gross pay also changed (earnings structure changed)
  - data_error     → large net change but gross stayed flat (suspicious)

This means the system correctly handles cases like:
  - 20% net increase + gross increased proportionally → salary_revision (planned)
  - 20% net increase + gross unchanged → data_error (suspicious deduction removal)
"""

import asyncio
import logging

from agno.agent.agent import Agent
from schemas.models import (
    AnomalyCategory,
    AnomalySeverity,
    AnomalyRecord,
    EmployeeDetail,
    FlaggedRecord,
    ComparisonRecord,
)
from services.llm_client import llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gross-change threshold for "salary_revision" detection.
# If gross pay changed by more than this percentage alongside a net change,
# we treat it as a planned earnings structure change (salary_revision).
# Below this, a net change with flat gross is suspicious (data_error or
# missing_deduction).
# ---------------------------------------------------------------------------
_GROSS_CHANGE_THRESHOLD_PCT = 2.0


# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a payroll audit reviewer preparing concise commentary.
The anomaly category and severity are already determined by the system.
Write:
    1) A 1-2 sentence explanation in a professional audit tone.
    2) A short, operational suggested action (1 sentence).

Style rules:
- Avoid repetitive openings (do not start with "This record was flagged...").
- Vary phrasing naturally across records.
- Keep language executive-ready and concrete.

Respond ONLY with valid JSON — no markdown, no text outside the JSON object:
{"explanation": "...", "suggested_action": "..."}"""


def _build_llm_prompt(
    flagged: FlaggedRecord,
    employee: EmployeeDetail | None,
    category: AnomalyCategory,
    severity: AnomalySeverity,
    requires_manual_review: bool,
) -> str:
    comp = flagged.comparison

    def fmt(v) -> str:
        return f"{v:,.2f}" if v is not None else "N/A"

    def pct(v) -> str:
        if v is None:
            return "N/A"
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.1f}%"

    gross_context = ""
    if comp.prev_gross is not None and comp.curr_gross is not None:
        gross_delta = comp.curr_gross - comp.prev_gross
        gross_context = (
            f"\n  - Gross pay: {fmt(comp.prev_gross)} → {fmt(comp.curr_gross)} "
            f"(Δ {fmt(gross_delta)})"
        )

    deduction_context = ""
    if comp.missing_deduction_components:
        deduction_context = (
            f"\n  - Deduction components removed this month: "
            f"{', '.join(comp.missing_deduction_components)}"
        )

    emp_context = ""
    if employee:
        emp_context = (
            f"\nEmployee context:"
            f"\n  - Department  : {employee.department or 'Unknown'}"
            f"\n  - Designation : {employee.designation or 'Unknown'}"
            f"\n  - Joined      : {employee.date_of_joining or 'Unknown'}"
            f"\n  - Status      : {employee.status or 'Unknown'}"
        )

    return f"""Payroll anomaly to explain:

Employee    : {comp.employee_name} ({comp.employee_id})
Category    : {category.value}  ← already determined, do not change
Severity    : {severity.value}
Manual review required: {requires_manual_review}

Pay figures:
  - Previous net pay : {fmt(comp.prev_net)}
  - Current net pay  : {fmt(comp.curr_net)}
  - Net change       : {fmt(comp.net_delta)} ({pct(comp.net_delta_pct)}){gross_context}
  - Previous deductions: {fmt(comp.prev_deductions)}
  - Current deductions : {fmt(comp.curr_deductions)}{deduction_context}
{emp_context}

Write the explanation and suggested_action JSON."""


# ---------------------------------------------------------------------------
# Fallback text (used when LLM is unavailable)
# ---------------------------------------------------------------------------

_FALLBACK_EXPLANATIONS: dict[AnomalyCategory, str] = {
    AnomalyCategory.new_employee: (
        "This employee appears only in the current payroll cycle, so no historical "
        "payroll baseline exists for comparison."
    ),
    AnomalyCategory.missing_slip: (
        "The current payroll slip is absent, preventing complete payroll verification "
        "for this employee."
    ),
    AnomalyCategory.missing_deduction: (
        "Deduction components present last month are absent this month, "
        "indicating a deduction removal or capture issue."
    ),
    AnomalyCategory.salary_revision: (
        "Gross and net pay both changed between months, consistent with a "
        "salary revision or appraisal."
    ),
    AnomalyCategory.data_error: (
        "Net pay shifted materially while gross pay remained flat, which is "
        "inconsistent with an earnings structure change."
    ),
}

_FALLBACK_ACTIONS: dict[AnomalyCategory, str] = {
    AnomalyCategory.new_employee: (
        "Confirm onboarding date and salary structure assignment before payout."
    ),
    AnomalyCategory.missing_slip: (
        "Verify employment status and re-run payroll for the missing slip."
    ),
    AnomalyCategory.missing_deduction: (
        "Validate the deduction change against approved policy or structure."
    ),
    AnomalyCategory.salary_revision: (
        "Verify approved revision documentation and confirm payroll accuracy."
    ),
    AnomalyCategory.data_error: (
        "Escalate for immediate payroll review and correction before payout."
    ),
}


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class CategorizationAgent:
    """
    Enriches flagged payroll records with category, severity, review flag,
    and LLM-generated human-readable explanation and suggested action.

    Call:
        agent = CategorizationAgent()
        anomalies = await agent.run(flagged_records, employee_details)
    """

    async def run(
        self,
        flagged_records: list[FlaggedRecord],
        employee_details: dict[str, EmployeeDetail],
    ) -> list[AnomalyRecord]:
        if not flagged_records:
            logger.info("[CategorizationAgent] No flagged records to categorize")
            return []

        logger.info(
            "[CategorizationAgent] Categorizing %d record(s) — deterministic rules + LLM text",
            len(flagged_records),
        )

        tasks = [
            self._categorize_one(fr, employee_details.get(fr.comparison.employee_id))
            for fr in flagged_records
        ]
        results = await asyncio.gather(*tasks)
        logger.info("[CategorizationAgent] Done")
        return list(results)

    # ------------------------------------------------------------------
    # Core pipeline for a single record
    # ------------------------------------------------------------------

    async def _categorize_one(
        self,
        flagged: FlaggedRecord,
        employee: EmployeeDetail | None,
    ) -> AnomalyRecord:
        comp = flagged.comparison

        # Step 1: Deterministic category — no LLM
        category = self._determine_category(flagged)

        # Step 2: Deterministic severity — no LLM
        severity = self._assign_severity(category, comp.net_delta_pct)

        # Step 3: Deterministic review flag — no LLM
        requires_review = self._requires_manual_review(category, severity)

        logger.info(
            "[CategorizationAgent] %s → category=%s | severity=%s | review=%s",
            comp.employee_name,
            category.value,
            severity.value,
            requires_review,
        )

        # Step 4: LLM for explanation + suggested_action only
        explanation, suggested_action = await self._llm_explain(
            flagged, employee, category, severity, requires_review
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
            severity=severity,
            requires_manual_review=requires_review,
            anomaly_reasons=flagged.anomaly_reasons,
            suggested_action=suggested_action,
            llm_explanation=explanation,
        )

    # ------------------------------------------------------------------
    # Step 1: Deterministic category
    # ------------------------------------------------------------------

    def _determine_category(self, flagged: FlaggedRecord) -> AnomalyCategory:
        """
        Assign anomaly category from payroll context — no LLM, no randomness.

          Decision tree (evaluated top-to-bottom, first match wins):

          1. No previous slip → new_employee
              (absence of history is unambiguous)

          2. No current slip → missing_slip
              (employee disappeared from payroll this month)

          3. Gross pay also changed significantly → salary_revision
              (both earnings structure AND net changed together = explainable)

          4. Specific deduction components removed → missing_deduction
              (we have component-level evidence, not just totals)

          5. Large net change (>30%) with flat gross → data_error
              (net changed without an earnings cause = suspicious)

          6. Moderate deduction anomaly with flat gross → missing_deduction
              (deductions changed, gross didn't, no component detail available)

          7. Fallback → data_error
        """
        comp = flagged.comparison
        reasons = flagged.anomaly_reasons

        # Rule 1
        if "new_employee" in reasons:
            return AnomalyCategory.new_employee

        # Rule 2
        if "missing_slip" in reasons:
            return AnomalyCategory.missing_slip

        # Rules 3 & 5 use gross pay context
        gross_change_pct = self._gross_change_pct(comp)
        gross_changed = gross_change_pct > _GROSS_CHANGE_THRESHOLD_PCT

        # Rule 3 — earnings structure changed alongside net
        if gross_changed:
            return AnomalyCategory.salary_revision

        # Rule 4 — explicit component-level evidence
        if comp.missing_deduction_components:
            return AnomalyCategory.missing_deduction

        # Rule 5 — large net change, gross flat → suspicious
        abs_delta = abs(comp.net_delta_pct or 0)
        if abs_delta > 30:
            return AnomalyCategory.data_error

        # Rule 6 — deduction anomaly detected by total-amount rule (no components)
        if "deduction_anomaly" in reasons:
            return AnomalyCategory.missing_deduction

        # Fallback
        return AnomalyCategory.data_error

    # ------------------------------------------------------------------
    # Step 2: Deterministic severity
    # ------------------------------------------------------------------

    def _assign_severity(
        self, category: AnomalyCategory, delta_pct: float | None
    ) -> AnomalySeverity:
        """
        Assign severity from category + magnitude.

                Override rules (override percentage logic):
                    - missing_slip  → high (employee missing from payroll is urgent)
                    - missing_deduction → medium (needs checking, not immediately critical)

                Percentage-based rules for remaining cases:
                    - > 90%  → critical
                    - > 70%  → high
                    - > 30%  → medium
                    - <= 30% → low
        """
        if category == AnomalyCategory.missing_slip:
            return AnomalySeverity.high

        if category == AnomalyCategory.missing_deduction:
            return AnomalySeverity.medium

        # For salary_revision and data_error, use delta_pct
        abs_delta = abs(delta_pct or 0)
        if abs_delta > 90:
            return AnomalySeverity.critical
        if abs_delta > 70:
            return AnomalySeverity.high
        if abs_delta > 30:
            return AnomalySeverity.medium
        return AnomalySeverity.low

    # ------------------------------------------------------------------
    # Step 3: Deterministic review flag
    # ------------------------------------------------------------------

    def _requires_manual_review(
        self, category: AnomalyCategory, severity: AnomalySeverity
    ) -> bool:
        """
        Determine whether a human must review this record before payroll close.

        Rules:
          - data_error always requires review (unexplained change)
          - missing_slip always requires review (employee absent from payroll)
          - missing_deduction always requires review (deduction was silently removed)
          - critical or high severity always requires review regardless of category
            (a valid salary_revision at +80% still needs a sign-off)
          - salary_revision at medium → True (good practice to verify revision letter)
          - new_employee at low → False (standard onboarding check, not urgent)
        """
        if category in (AnomalyCategory.data_error, AnomalyCategory.missing_slip):
            return True

        if category == AnomalyCategory.missing_deduction:
            return True

        if severity in (AnomalySeverity.critical, AnomalySeverity.high):
            return True

        if category == AnomalyCategory.salary_revision and severity == AnomalySeverity.medium:
            return True

        return False

    # ------------------------------------------------------------------
    # Step 4: LLM — explanation + suggested_action only
    # ------------------------------------------------------------------

    async def _llm_explain(
        self,
        flagged: FlaggedRecord,
        employee: EmployeeDetail | None,
        category: AnomalyCategory,
        severity: AnomalySeverity,
        requires_review: bool,
    ) -> tuple[str, str]:
        """
        Ask the LLM to write a human-readable explanation and suggested action.
        The category and severity are passed IN — the LLM cannot change them.

        Returns (explanation, suggested_action).
        Falls back to static text if the LLM call fails.
        """
        try:
            prompt = _build_llm_prompt(
                flagged, employee, category, severity, requires_review
            )
            result = await llm.ask_json(prompt, system_prompt=_SYSTEM_PROMPT)

            explanation = str(result.get("explanation", "")).strip()
            suggested_action = str(result.get("suggested_action", "")).strip()

            if not explanation:
                explanation = _FALLBACK_EXPLANATIONS.get(category, "Anomaly detected.")
            if not suggested_action:
                suggested_action = _FALLBACK_ACTIONS.get(category, "Manual review required.")

            return explanation, suggested_action

        except Exception as e:
            logger.warning(
                "[CategorizationAgent] LLM call failed for %s: %s — using fallback text",
                flagged.comparison.employee_name,
                e,
            )
            return (
                _FALLBACK_EXPLANATIONS.get(category, "Anomaly detected."),
                _FALLBACK_ACTIONS.get(category, "Manual review required."),
            )

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _gross_change_pct(comp: ComparisonRecord) -> float:
        """
        Returns the absolute percentage change in gross pay.
        Returns 0.0 if either value is missing or previous gross is zero.
        """
        if comp.prev_gross is None or comp.curr_gross is None:
            return 0.0
        if comp.prev_gross == 0:
            return 100.0 if comp.curr_gross > 0 else 0.0
        return abs((comp.curr_gross - comp.prev_gross) / comp.prev_gross) * 100


# ---------------------------------------------------------------------------
# Agno compliance wrapper
# ---------------------------------------------------------------------------


class AgnoCategorizationAgent(Agent):
    """
    Agno-compliant wrapper around CategorizationAgent.

    Exposes the categorization stage as an agno.agent.Agent so the
    PayrollAnalysisTeam (agno.team.Team) can declare it as a named member.

    The wrapper keeps a strict boundary:
      - Deterministic steps (category, severity, review flag) stay in
        CategorizationAgent._determine_category / _assign_severity /
        _requires_manual_review.
      - LLM is used ONLY for human-readable explanation and suggested_action
        via CategorizationAgent._llm_explain().
      - Nothing is moved into the Agent prompt.
    """

    def __init__(self):
        super().__init__(
            name="CategorizationAgent",
            description=(
                "Payroll anomaly categorization agent. "
                "Assigns category (new_employee / missing_slip / "
                "missing_deduction / salary_revision / data_error), "
                "severity (low / medium / high / critical), and review flag "
                "via deterministic rules. Uses LLM only for explanation text "
                "and suggested action wording."
            ),
        )
        self._impl = CategorizationAgent()
        logger.debug("[AgnoCategorizationAgent] Initialised (wraps CategorizationAgent)")

    async def run_categorization(self, flagged_records, employee_details):
        """Delegate to the underlying async CategorizationAgent."""
        return await self._impl.run(flagged_records, employee_details)