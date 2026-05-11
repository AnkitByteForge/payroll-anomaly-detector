"""
schemas/models.py

Single source of truth for ALL Pydantic models used in this project.

Changes from previous version:
  - Added AnomalySeverity enum (critical / high / medium / low)
  - AnomalyRecord gains: severity, requires_manual_review
  - AnomalyReport: total_employees_current renamed to
    employees_evaluated + employees_in_current_payroll
  - HITLPreview: total_employees renamed to employees_in_current_payroll
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AnomalyCategory(str, Enum):
    new_employee = "new_employee"
    salary_revision = "salary_revision"
    data_error = "data_error"
    missing_deduction = "missing_deduction"
    missing_slip = "missing_slip"
    no_anomaly = "no_anomaly"


class AnomalySeverity(str, Enum):
    """
    Severity is intentionally separate from category.

    A salary_revision can be medium severity (routine appraisal)
    or critical severity (90%+ increase that still needs manual sign-off).

    Severity drives requires_manual_review and report sort order.
    Category drives the HR action and explanation.
    """
    critical = "critical"   # >= 90% net pay change
    high = "high"           # >= 50% change  OR  missing_slip
    medium = "medium"       # >= 15% change  OR  missing_deduction
    low = "low"             # new_employee   OR  < 15% change


class SessionStatus(str, Enum):
    awaiting_confirmation = "awaiting_confirmation"
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"


class DocStatus(int, Enum):
    draft = 0
    submitted = 1
    cancelled = 2


# ---------------------------------------------------------------------------
# ERPNext raw data models
# ---------------------------------------------------------------------------


class SalarySlipSummary(BaseModel):
    name: str
    employee: str
    employee_name: str
    net_pay: float = 0.0
    gross_pay: float = 0.0
    total_deduction: float = 0.0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    docstatus: int = 0

    @property
    def status_label(self) -> str:
        return {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(
            self.docstatus, "Unknown"
        )


class SalaryComponent(BaseModel):
    salary_component: str
    amount: float = 0.0
    component_type: Optional[str] = None


class SalarySlipDetail(BaseModel):
    name: str
    employee: str
    employee_name: str
    net_pay: float = 0.0
    gross_pay: float = 0.0
    total_deduction: float = 0.0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    docstatus: int = 0
    earnings: list[SalaryComponent] = Field(default_factory=list)
    deductions: list[SalaryComponent] = Field(default_factory=list)


class EmployeeSummary(BaseModel):
    name: str
    employee_name: str
    department: Optional[str] = None
    status: Optional[str] = None


class EmployeeDetail(BaseModel):
    name: str
    employee_name: str
    department: Optional[str] = None
    date_of_joining: Optional[str] = None
    status: Optional[str] = None
    salary_mode: Optional[str] = None
    designation: Optional[str] = None
    company: Optional[str] = None


# ---------------------------------------------------------------------------
# Pipeline processing models
# ---------------------------------------------------------------------------


class ComparisonRecord(BaseModel):
    employee_id: str
    employee_name: str

    prev_net: Optional[float] = None
    curr_net: Optional[float] = None
    net_delta: Optional[float] = None
    net_delta_pct: Optional[float] = None

    prev_gross: Optional[float] = None
    curr_gross: Optional[float] = None

    prev_deductions: Optional[float] = None
    curr_deductions: Optional[float] = None
    deduction_delta: Optional[float] = None

    prev_slip_name: Optional[str] = None
    curr_slip_name: Optional[str] = None

    prev_deduction_components: list[str] = Field(default_factory=list)
    curr_deduction_components: list[str] = Field(default_factory=list)

    # Empty list when curr_slip is None (missing_slip case).
    # Comparing deductions against a missing slip is meaningless.
    missing_deduction_components: list[str] = Field(default_factory=list)


class FlaggedRecord(BaseModel):
    comparison: ComparisonRecord
    anomaly_reasons: list[str]


class AnomalyRecord(BaseModel):
    """
    Fully enriched anomaly entry.

    category and severity are SEPARATE concepts:
      category  = what kind of payroll event this is
      severity  = how urgently it needs human attention

    Example: salary_revision + critical severity means the revision was
    so large it still needs a manual sign-off even though it may be valid.
    """

    employee_id: str
    employee_name: str
    prev_net_pay: Optional[float] = None
    curr_net_pay: Optional[float] = None
    pct_change: Optional[float] = None
    prev_deductions: Optional[float] = None
    curr_deductions: Optional[float] = None
    missing_deduction_components: list[str] = Field(default_factory=list)

    anomaly_category: AnomalyCategory
    severity: AnomalySeverity
    requires_manual_review: bool

    anomaly_reasons: list[str] = Field(default_factory=list)
    suggested_action: str
    llm_explanation: Optional[str] = None


# ---------------------------------------------------------------------------
# HITL models
# ---------------------------------------------------------------------------


class AnomalyBreakdown(BaseModel):
    new_employee: int = 0
    salary_revision: int = 0
    data_error: int = 0
    missing_deduction: int = 0
    missing_slip: int = 0


class TopAnomaly(BaseModel):
    employee_name: str
    prev_net_pay: Optional[float]
    curr_net_pay: Optional[float]
    pct_change: Optional[float]
    category: AnomalyCategory
    severity: AnomalySeverity


class HITLPreview(BaseModel):
    employees_in_current_payroll: int
    total_anomalies: int
    breakdown: AnomalyBreakdown
    top_3_anomalies: list[TopAnomaly]
    confirmation_prompt: str


# ---------------------------------------------------------------------------
# Final report model
# ---------------------------------------------------------------------------


class AnomalyReport(BaseModel):
    generated_at: datetime
    period_current: str
    period_previous: str

    employees_evaluated: int            # union of both months
    employees_in_current_payroll: int   # current month only

    total_anomalies: int
    threshold_pct: float
    anomalies: list[AnomalyRecord]
    agents_involved: list[str]
    summary: str


# ---------------------------------------------------------------------------
# Session store model
# ---------------------------------------------------------------------------


class PendingSession(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    status: SessionStatus = SessionStatus.awaiting_confirmation
    prompt: str
    preview: Optional[HITLPreview] = None
    pending_report: Optional[AnomalyReport] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# FastAPI request / response models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    prompt: str = Field(
        ...,
        json_schema_extra={
            "example": "Review this month's payroll and flag anything that seems off."
        },
    )


class RunResponse(BaseModel):
    status: str
    session_id: str
    preview: Optional[HITLPreview] = None
    report: Optional[AnomalyReport] = None
    message: Optional[str] = None


class ConfirmRequest(BaseModel):
    session_id: str
    confirmed: bool


class ConfirmResponse(BaseModel):
    status: str
    session_id: str
    message: Optional[str] = None
    report: Optional[AnomalyReport] = None


class HealthResponse(BaseModel):
    service: str
    version: str
    status: str
    environment: str


class SessionSummary(BaseModel):
    session_id: str
    status: SessionStatus
    prompt: str
    created_at: datetime
    resolved_at: Optional[datetime]
    total_anomalies: Optional[int] = None