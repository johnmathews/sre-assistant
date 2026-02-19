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


class EvalCase(BaseModel):
    """A single evaluation case loaded from YAML."""

    id: str
    description: str
    question: str
    required_services: list[str] = Field(default_factory=list)
    expected_tools: ExpectedTools
    mocks: list[MockResponse]
    rubric: str


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
