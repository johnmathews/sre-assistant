"""Pydantic models for eval cases, scores, and results."""

from pydantic import BaseModel, Field


class MockResponse(BaseModel):
    """A mocked HTTP response for a specific URL pattern."""

    url: str
    method: str = "GET"
    status: int = 200
    body: list[dict[str, object]] | dict[str, object] | str = Field(default_factory=dict)


class ExpectedTools(BaseModel):
    """Tool selection expectations for an eval case."""

    must_call: list[str] = Field(default_factory=list)
    may_call: list[str] = Field(default_factory=list)
    must_not_call: list[str] = Field(default_factory=list)


class MemorySeedBaseline(BaseModel):
    """Seed data for a metric baseline record."""

    metric_name: str
    labels: str = "{}"
    avg_value: float
    p95_value: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    sample_count: int = 100
    window_days: int = 7


class MemorySeedIncident(BaseModel):
    """Seed data for an incident record."""

    title: str
    description: str
    alert_name: str | None = None
    root_cause: str | None = None
    resolution: str | None = None
    severity: str = "info"
    services: str = ""


class MemorySeedReport(BaseModel):
    """Seed data for an archived report record."""

    report_markdown: str
    report_data: str = "{}"
    lookback_days: int = 7
    active_alerts: int = 0
    slo_failures: int = 0
    total_log_errors: int = 0
    estimated_cost: float = 0.0


class MemorySeed(BaseModel):
    """Seed data for the agent memory store (SQLite)."""

    baselines: list[MemorySeedBaseline] = Field(default_factory=list)
    incidents: list[MemorySeedIncident] = Field(default_factory=list)
    reports: list[MemorySeedReport] = Field(default_factory=list)


class EvalCase(BaseModel):
    """A single evaluation case loaded from YAML."""

    id: str
    description: str
    question: str
    required_services: list[str] = Field(default_factory=list)
    expected_tools: ExpectedTools
    mocks: list[MockResponse]
    rubric: str
    memory_seed: MemorySeed | None = None


class ToolScore(BaseModel):
    """Deterministic tool selection score."""

    passed: bool
    called_tools: list[str]
    missing_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)


class JudgeScore(BaseModel):
    """LLM-as-judge answer quality score."""

    passed: bool
    explanation: str


class EvalResult(BaseModel):
    """Full result for a single eval case."""

    case_id: str
    description: str
    tool_score: ToolScore
    judge_score: JudgeScore
    agent_answer: str
    passed: bool
