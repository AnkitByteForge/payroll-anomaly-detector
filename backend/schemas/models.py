"""
schemas/models.py

Single source of truth for ALL Pydantic models used in this project.
Every agent, route, and tool uses these — no ad-hoc dicts anywhere.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
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
# (These mirror ERPNext's response fields — used by tools and DataFetchAgent)
# ---------------------------------------------------------------------------


class SalarySlipSummary(BaseModel):
    """Lightweight slip — returned by the list endpoint."""

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
        return {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(self.docstatus, "Unknown")


class SalaryComponent(BaseModel):
    """A single earning or deduction line inside a salary slip."""

    salary_component: str
    amount: float = 0.0
    component_type: Optional[str] = None  # "earning" or "deduction"


class SalarySlipDetail(BaseModel):
    """
    Full salary slip — returned by the document endpoint.
    Includes component-level breakdown for deduction analysis.
    """

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
    """Lightweight employee — returned by the list endpoint."""

    name: str
    employee_name: str
    department: Optional[str] = None
    status: Optional[str] = None


class EmployeeDetail(BaseModel):
    """Full employee record — returned by the document endpoint."""

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
# (Used internally by comparison, anomaly, categorization agents)
# ---------------------------------------------------------------------------


class ComparisonRecord(BaseModel):
    """
    Per-employee diff between current and previous month.
    Produced by ComparisonAgent, consumed by AnomalyDetectorAgent.
    """

    employee_id: str
    employee_name: str

    # Net pay
    prev_net: Optional[float] = None
    curr_net: Optional[float] = None
    net_delta: Optional[float] = None
    net_delta_pct: Optional[float] = None

    # Gross pay
    prev_gross: Optional[float] = None
    curr_gross: Optional[float] = None

    # Deductions
    prev_deductions: Optional[float] = None
    curr_deductions: Optional[float] = None
    deduction_delta: Optional[float] = None

    # Source slip names (for detailed lookup if needed)
    prev_slip_name: Optional[str] = None
    curr_slip_name: Optional[str] = None

    # Component-level deduction names (for missing deduction detection)
    prev_deduction_components: list[str] = Field(default_factory=list)
    curr_deduction_components: list[str] = Field(default_factory=list)
    missing_deduction_components: list[str] = Field(default_factory=list)


class FlaggedRecord(BaseModel):
    """
    A ComparisonRecord that crossed the anomaly threshold.
    Produced by AnomalyDetectorAgent, consumed by CategorizationAgent.
    """

    comparison: ComparisonRecord
    anomaly_reasons: list[str]  # e.g. ["large_net_change", "missing_deduction"]


class AnomalyRecord(BaseModel):
    """
    Fully enriched anomaly entry — the final output per employee.
    Produced by CategorizationAgent, assembled by ReportBuilderAgent.
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
    anomaly_reasons: list[str] = Field(default_factory=list)
    suggested_action: str
    llm_explanation: Optional[str] = None  # LLM-generated context (CategorizationAgent)


# ---------------------------------------------------------------------------
# HITL models
# ---------------------------------------------------------------------------


class AnomalyBreakdown(BaseModel):
    """Count of anomalies per category — shown in the HITL preview."""

    new_employee: int = 0
    salary_revision: int = 0
    data_error: int = 0
    missing_deduction: int = 0
    missing_slip: int = 0


class TopAnomaly(BaseModel):
    """Compact anomaly entry for the HITL top-3 preview."""

    employee_name: str
    prev_net_pay: Optional[float]
    curr_net_pay: Optional[float]
    pct_change: Optional[float]
    category: AnomalyCategory


class HITLPreview(BaseModel):
    """
    The data shown to the user before they confirm/cancel.
    Returned by /run when status is awaiting_confirmation.
    """

    total_employees: int
    total_anomalies: int
    breakdown: AnomalyBreakdown
    top_3_anomalies: list[TopAnomaly]
    confirmation_prompt: str


# ---------------------------------------------------------------------------
# Final report model
# ---------------------------------------------------------------------------


class AnomalyReport(BaseModel):
    """The complete finalized report returned after HITL confirmation."""

    generated_at: datetime
    period_current: str          # e.g. "2026-05"
    period_previous: str         # e.g. "2026-04"
    total_employees_current: int
    total_anomalies: int
    threshold_pct: float
    anomalies: list[AnomalyRecord]
    agents_involved: list[str]
    summary: str                 # LLM-generated executive summary


# ---------------------------------------------------------------------------
# Session store model
# ---------------------------------------------------------------------------


class PendingSession(BaseModel):
    """One HITL session stored in memory between /run and /confirm."""

    session_id: str = Field(default_factory=lambda: str(uuid4()))
    status: SessionStatus = SessionStatus.awaiting_confirmation
    prompt: str
    preview: Optional[HITLPreview] = None
    pending_report: Optional[AnomalyReport] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# FastAPI request / response models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    prompt: str = Field(
        ...,
        example="Review this month's payroll and flag anything that seems off compared to last month.",
    )


class RunResponse(BaseModel):
    status: str  # "awaiting_confirmation" | "completed"
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
    """Compact session info returned by /history."""

    session_id: str
    status: SessionStatus
    prompt: str
    created_at: datetime
    resolved_at: Optional[datetime]
    total_anomalies: Optional[int] = None