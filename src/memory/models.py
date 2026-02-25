"""TypedDict models for memory store records."""

from typing import TypedDict


class ReportRecord(TypedDict):
    id: int
    generated_at: str  # ISO 8601
    lookback_days: int
    report_markdown: str
    report_data: str  # JSON-serialized ReportData
    active_alerts: int
    slo_failures: int
    total_log_errors: int
    estimated_cost: float


class IncidentRecord(TypedDict):
    id: int
    created_at: str  # ISO 8601
    resolved_at: str | None
    alert_name: str | None
    title: str
    description: str
    root_cause: str | None
    resolution: str | None
    severity: str  # info | warning | critical
    services: str  # comma-separated
    session_id: str | None


class BaselineRecord(TypedDict):
    id: int
    metric_name: str
    labels: str  # JSON label set
    avg_value: float
    p95_value: float | None
    min_value: float | None
    max_value: float | None
    sample_count: int
    window_days: int
    computed_at: str  # ISO 8601


class QueryPatternRecord(TypedDict):
    id: int
    question: str  # Truncated user question (first 200 chars)
    tool_names: str  # Comma-separated tool names used
    created_at: str  # ISO 8601
