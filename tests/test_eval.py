"""Unit tests for the evaluation framework — YAML parsing, tool scoring, extraction."""

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.eval.loader import load_eval_cases
from src.eval.models import EvalCase, EvalResult, ExpectedTools, JudgeScore, MockResponse, ToolScore
from src.eval.report import print_case_result, print_summary
from src.eval.runner import _extract_answer, _extract_tool_calls, _score_tools

# --- Model validation tests ---


class TestModels:
    def test_mock_response_defaults(self) -> None:
        mock = MockResponse(url="http://test.com/api")
        assert mock.method == "GET"
        assert mock.status == 200
        assert mock.body == {}

    def test_expected_tools_defaults(self) -> None:
        et = ExpectedTools()
        assert et.must_call == []
        assert et.may_call == []
        assert et.must_not_call == []

    def test_eval_case_from_dict(self) -> None:
        data: dict[str, Any] = {
            "id": "test-case",
            "description": "A test case",
            "question": "What is happening?",
            "required_services": ["grafana"],
            "expected_tools": {
                "must_call": ["grafana_get_alerts"],
                "may_call": ["prometheus_instant_query"],
                "must_not_call": [],
            },
            "mocks": [
                {
                    "url": "http://grafana.test:3000/api/alerts",
                    "method": "GET",
                    "status": 200,
                    "body": [],
                }
            ],
            "rubric": "Should mention alerts",
        }
        case = EvalCase.model_validate(data)
        assert case.id == "test-case"
        assert case.expected_tools.must_call == ["grafana_get_alerts"]
        assert len(case.mocks) == 1
        assert case.mocks[0].url == "http://grafana.test:3000/api/alerts"

    def test_eval_case_required_services_defaults_empty(self) -> None:
        data: dict[str, Any] = {
            "id": "minimal",
            "description": "Minimal case",
            "question": "Test?",
            "expected_tools": {"must_call": []},
            "mocks": [],
            "rubric": "Anything",
        }
        case = EvalCase.model_validate(data)
        assert case.required_services == []

    def test_tool_score_model(self) -> None:
        ts = ToolScore(
            passed=False,
            called_tools=["prometheus_instant_query"],
            missing_tools=["grafana_get_alerts"],
            forbidden_tools=[],
        )
        assert not ts.passed
        assert ts.missing_tools == ["grafana_get_alerts"]

    def test_eval_result_model(self) -> None:
        result = EvalResult(
            case_id="test",
            description="Test",
            tool_score=ToolScore(passed=True, called_tools=["grafana_get_alerts"]),
            judge_score=JudgeScore(passed=True, explanation="Good"),
            agent_answer="The alert is firing",
            passed=True,
        )
        assert result.passed


# --- Tool call extraction tests ---


class TestExtractToolCalls:
    def test_extracts_tool_names_from_ai_messages(self) -> None:
        messages: list[Any] = [
            HumanMessage(content="What alerts?"),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "grafana_get_alerts", "args": {}, "id": "1", "type": "tool_call"},
                    {"name": "prometheus_instant_query", "args": {"query": "up"}, "id": "2", "type": "tool_call"},
                ],
            ),
            ToolMessage(content="result1", tool_call_id="1"),
            ToolMessage(content="result2", tool_call_id="2"),
            AIMessage(content="Here are the alerts..."),
        ]
        tool_names = _extract_tool_calls(messages)
        assert tool_names == ["grafana_get_alerts", "prometheus_instant_query"]

    def test_empty_messages(self) -> None:
        assert _extract_tool_calls([]) == []

    def test_no_tool_calls(self) -> None:
        messages: list[Any] = [
            HumanMessage(content="Hi"),
            AIMessage(content="Hello!"),
        ]
        assert _extract_tool_calls(messages) == []

    def test_multiple_ai_messages_with_tool_calls(self) -> None:
        messages: list[Any] = [
            AIMessage(
                content="",
                tool_calls=[{"name": "tool_a", "args": {}, "id": "1", "type": "tool_call"}],
            ),
            ToolMessage(content="result", tool_call_id="1"),
            AIMessage(
                content="",
                tool_calls=[{"name": "tool_b", "args": {}, "id": "2", "type": "tool_call"}],
            ),
            ToolMessage(content="result", tool_call_id="2"),
            AIMessage(content="Done"),
        ]
        assert _extract_tool_calls(messages) == ["tool_a", "tool_b"]


# --- Answer extraction tests ---


class TestExtractAnswer:
    def test_extracts_last_ai_message_content(self) -> None:
        messages: list[Any] = [
            AIMessage(content="Thinking..."),
            AIMessage(content="Here is the answer."),
        ]
        assert _extract_answer(messages) == "Here is the answer."

    def test_skips_empty_content(self) -> None:
        messages: list[Any] = [
            AIMessage(content="Real answer"),
            AIMessage(content=""),
        ]
        assert _extract_answer(messages) == "Real answer"

    def test_empty_messages_returns_default(self) -> None:
        assert _extract_answer([]) == "No response generated."


# --- Tool scoring tests ---


class TestScoreTools:
    def _make_case(
        self,
        must_call: list[str] | None = None,
        may_call: list[str] | None = None,
        must_not_call: list[str] | None = None,
    ) -> EvalCase:
        return EvalCase(
            id="test",
            description="Test",
            question="Test?",
            expected_tools=ExpectedTools(
                must_call=must_call or [],
                may_call=may_call or [],
                must_not_call=must_not_call or [],
            ),
            mocks=[],
            rubric="Test",
        )

    def test_all_must_call_present_passes(self) -> None:
        case = self._make_case(must_call=["tool_a", "tool_b"])
        score = _score_tools(case, ["tool_a", "tool_b", "tool_c"])
        assert score.passed
        assert score.missing_tools == []

    def test_missing_must_call_fails(self) -> None:
        case = self._make_case(must_call=["tool_a", "tool_b"])
        score = _score_tools(case, ["tool_a"])
        assert not score.passed
        assert score.missing_tools == ["tool_b"]

    def test_forbidden_tool_called_fails(self) -> None:
        case = self._make_case(must_not_call=["bad_tool"])
        score = _score_tools(case, ["bad_tool", "good_tool"])
        assert not score.passed
        assert score.forbidden_tools == ["bad_tool"]

    def test_may_call_does_not_affect_score(self) -> None:
        case = self._make_case(must_call=["tool_a"], may_call=["tool_b"])
        # tool_b not called — still passes
        score = _score_tools(case, ["tool_a"])
        assert score.passed

    def test_empty_expectations_passes(self) -> None:
        case = self._make_case()
        score = _score_tools(case, ["any_tool"])
        assert score.passed

    def test_combined_missing_and_forbidden(self) -> None:
        case = self._make_case(must_call=["required"], must_not_call=["forbidden"])
        score = _score_tools(case, ["forbidden"])
        assert not score.passed
        assert score.missing_tools == ["required"]
        assert score.forbidden_tools == ["forbidden"]


# --- YAML loader tests ---


class TestLoader:
    def test_load_all_cases(self) -> None:
        cases = load_eval_cases()
        assert len(cases) > 0
        for case in cases:
            assert case.id
            assert case.question
            assert case.rubric

    def test_load_specific_case(self) -> None:
        cases = load_eval_cases(case_ids=["alert-explain-high-cpu"])
        assert len(cases) == 1
        assert cases[0].id == "alert-explain-high-cpu"

    def test_load_missing_case_raises(self) -> None:
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            load_eval_cases(case_ids=["nonexistent-case-id"])

    def test_all_cases_have_valid_structure(self) -> None:
        """Validate all YAML cases parse without errors."""
        cases = load_eval_cases()
        for case in cases:
            assert isinstance(case.expected_tools.must_call, list)
            assert isinstance(case.mocks, list)
            for mock in case.mocks:
                assert mock.url.startswith("http")
                assert mock.method in ("GET", "POST", "PUT", "DELETE")


# --- Report formatting tests ---


class TestReport:
    def test_print_case_result_pass(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = EvalResult(
            case_id="test-pass",
            description="A passing test",
            tool_score=ToolScore(passed=True, called_tools=["tool_a"]),
            judge_score=JudgeScore(passed=True, explanation="Good answer"),
            agent_answer="The answer is correct.",
            passed=True,
        )
        print_case_result(result)
        captured = capsys.readouterr()
        assert "test-pass" in captured.err
        assert "PASS" in captured.err

    def test_print_case_result_fail(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = EvalResult(
            case_id="test-fail",
            description="A failing test",
            tool_score=ToolScore(passed=False, called_tools=[], missing_tools=["grafana_get_alerts"]),
            judge_score=JudgeScore(passed=False, explanation="Bad answer"),
            agent_answer="I don't know.",
            passed=False,
        )
        print_case_result(result)
        captured = capsys.readouterr()
        assert "test-fail" in captured.err
        assert "FAIL" in captured.err
        assert "grafana_get_alerts" in captured.err

    def test_print_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        results = [
            EvalResult(
                case_id="pass-1",
                description="Pass",
                tool_score=ToolScore(passed=True, called_tools=[]),
                judge_score=JudgeScore(passed=True, explanation="OK"),
                agent_answer="Good",
                passed=True,
            ),
            EvalResult(
                case_id="fail-1",
                description="Fail",
                tool_score=ToolScore(passed=False, called_tools=[], missing_tools=["x"]),
                judge_score=JudgeScore(passed=True, explanation="OK"),
                agent_answer="Bad",
                passed=False,
            ),
        ]
        print_summary(results)
        captured = capsys.readouterr()
        assert "1/2 passed" in captured.err
        assert "fail-1" in captured.err
