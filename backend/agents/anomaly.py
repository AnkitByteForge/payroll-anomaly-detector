"""
agents/anomaly.py

AnomalyDetectorAgent — pure rule-based flagging. No I/O, no LLM.

Responsibilities:
  - Apply the configured threshold (ANOMALY_THRESHOLD_PCT) to net pay changes
  - Detect missing deduction components
  - Detect deductions that dropped to zero while gross pay stayed flat
  - Detect new employees (no previous slip)
  - Detect missing slips (was in prev month, absent this month)
  - Return only the records that need human attention

Threshold: configurable via ANOMALY_THRESHOLD_PCT in .env (default 15.0%)
  → An employee whose net pay changed by more than ±15% month-over-month
    will be flagged for review.

Input:  list[ComparisonRecord] (from ComparisonAgent)
Output: list[FlaggedRecord]

Agno compliance:
  - AgnoAnomalyDetectorAgent wraps AnomalyDetectorAgent as an agno.agent.Agent.
  - All rule-based logic is delegated to AnomalyDetectorAgent.run().
  - No business rules are moved into prompts.
"""

import logging

from agno.agent.agent import Agent
from config import settings
from schemas.models import ComparisonRecord, FlaggedRecord

logger = logging.getLogger(__name__)

# Minimum absolute deduction drop that triggers a "missing_deduction" flag
# when no component-level detail is available.
# e.g. if total_deduction dropped by > ₹500 while gross was flat → flag it.
_DEDUCTION_DROP_THRESHOLD = 500.0


class AnomalyDetectorAgent:
    """
    Scans ComparisonRecords and flags those that cross any anomaly threshold.

    Call:
        agent = AnomalyDetectorAgent()
        flagged = agent.run(comparison_records)
    """

    def __init__(self, threshold_pct: float | None = None):
        """
        Args:
            threshold_pct: Override the env-configured threshold.
                           If None, reads from settings.anomaly_threshold_pct.
        """
        self.threshold_pct = threshold_pct or settings.anomaly_threshold_pct

    def run(self, records: list[ComparisonRecord]) -> list[FlaggedRecord]:
        """
        Evaluate each comparison record against all anomaly rules.

        Returns:
            Only the records that triggered at least one rule.
        """
        logger.info(
            "[AnomalyDetectorAgent] Evaluating %d record(s) | threshold=%.1f%%",
            len(records),
            self.threshold_pct,
        )

        flagged: list[FlaggedRecord] = []

        for record in records:
            reasons = self._detect_reasons(record)
            if reasons:
                flagged.append(
                    FlaggedRecord(comparison=record, anomaly_reasons=reasons)
                )
                logger.debug(
                    "[AnomalyDetectorAgent] FLAGGED %s — reasons: %s",
                    record.employee_name,
                    reasons,
                )

        logger.info(
            "[AnomalyDetectorAgent] Flagged %d / %d employee(s)",
            len(flagged),
            len(records),
        )
        return flagged

    # ------------------------------------------------------------------
    # Rule engine
    # ------------------------------------------------------------------

    def _detect_reasons(self, rec: ComparisonRecord) -> list[str]:
        """
        Apply all anomaly rules to one ComparisonRecord.
        Returns a list of reason strings — empty list means no anomaly.

        Rules are evaluated independently so an employee can trigger
        multiple reasons (e.g. large_net_change + missing_deduction).
        """
        reasons: list[str] = []

        # Rule 1: No previous slip — brand new employee
        if rec.prev_net is None and rec.curr_net is not None:
            reasons.append("new_employee")
            # New employees don't need further net-pay comparison
            return reasons

        # Rule 2: No current slip — employee present last month but not this month
        if rec.curr_net is None and rec.prev_net is not None:
            reasons.append("missing_slip")
            return reasons

        # Rule 3: Net pay change exceeds threshold
        if (
            rec.net_delta_pct is not None
            and abs(rec.net_delta_pct) > self.threshold_pct
        ):
            reasons.append("large_net_change")

        # Rule 4: Specific deduction component(s) present last month but gone this month
        if rec.missing_deduction_components:
            reasons.append("missing_deduction")

        # Rule 5: Total deductions dropped to zero while gross pay was unchanged
        # (catches cases where component data isn't available)
        if self._deductions_dropped_to_zero_with_flat_gross(rec):
            if "missing_deduction" not in reasons:
                reasons.append("missing_deduction")

        # Rule 6: Deductions changed significantly but gross pay didn't
        # (suggests a deduction was modified without a corresponding salary change)
        if self._deduction_anomaly_without_gross_change(rec):
            if "missing_deduction" not in reasons:
                reasons.append("deduction_anomaly")

        return reasons

    # ------------------------------------------------------------------
    # Individual rule predicates
    # ------------------------------------------------------------------

    def _deductions_dropped_to_zero_with_flat_gross(
        self, rec: ComparisonRecord
    ) -> bool:
        """
        Returns True if:
          - Previous total_deduction was non-zero
          - Current total_deduction is exactly 0
          - Gross pay is roughly the same (within ₹100)
        """
        if rec.prev_deductions is None or rec.curr_deductions is None:
            return False
        if rec.prev_gross is None or rec.curr_gross is None:
            return False

        prev_ded_positive = rec.prev_deductions > 0
        curr_ded_zero = rec.curr_deductions == 0.0
        gross_flat = abs((rec.curr_gross or 0) - (rec.prev_gross or 0)) < 100.0

        return prev_ded_positive and curr_ded_zero and gross_flat

    def _deduction_anomaly_without_gross_change(
        self, rec: ComparisonRecord
    ) -> bool:
        """
        Returns True if:
          - Total deductions changed by more than _DEDUCTION_DROP_THRESHOLD
          - Gross pay is roughly the same (within ₹100)

        This catches silent deduction modifications that weren't paired
        with a legitimate salary revision.
        """
        if rec.deduction_delta is None:
            return False
        if rec.prev_gross is None or rec.curr_gross is None:
            return False

        deduction_changed_significantly = (
            abs(rec.deduction_delta) > _DEDUCTION_DROP_THRESHOLD
        )
        gross_flat = abs((rec.curr_gross or 0) - (rec.prev_gross or 0)) < 100.0

        return deduction_changed_significantly and gross_flat

    # ------------------------------------------------------------------
    # Utility: get summary stats (used by ReportBuilderAgent)
    # ------------------------------------------------------------------

    def summary_stats(self, flagged: list[FlaggedRecord]) -> dict:
        """
        Returns a breakdown of flagged records by reason.
        Useful for logging and HITL preview.
        """
        counts: dict[str, int] = {}
        for fr in flagged:
            for reason in fr.anomaly_reasons:
                counts[reason] = counts.get(reason, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Agno compliance wrapper
# ---------------------------------------------------------------------------


class AgnoAnomalyDetectorAgent(Agent):
    """
    Agno-compliant wrapper around AnomalyDetectorAgent.

    Exposes the deterministic rule engine as an agno.agent.Agent so the
    PayrollAnalysisTeam (agno.team.Team) can declare it as a named member.

    No anomaly rules are moved into prompts — all logic stays in
    AnomalyDetectorAgent._detect_reasons().
    """

    def __init__(self, threshold_pct: float | None = None):
        super().__init__(
            name="AnomalyDetectorAgent",
            description=(
                "Rule-based payroll anomaly detector. "
                "Flags employees whose net pay changed beyond the configured "
                "threshold, or who have missing slips, missing deduction "
                "components, or suspicious deduction drops. "
                "Entirely deterministic — no LLM."
            ),
        )
        self._impl = AnomalyDetectorAgent(threshold_pct=threshold_pct)
        logger.debug(
            "[AgnoAnomalyDetectorAgent] Initialised | threshold=%.1f%%",
            self._impl.threshold_pct,
        )

    def run_detection(self, records):
        """Delegate to the underlying AnomalyDetectorAgent."""
        return self._impl.run(records)