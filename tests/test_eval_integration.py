"""Integration tests for the eval framework — mock both LLM and HTTP, verify full pipeline.

These tests do NOT call the real OpenAI API. They mock the LLM response to verify
the eval pipeline (settings patching, respx mock setup, tool call extraction, scoring,
judge parsing) works end-to-end.
"""

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.eval.judge import judge_answer
from src.eval.models import EvalCase, ExpectedTools


@pytest.mark.integration
class TestJudgeAnswer:
    """Test judge_answer with a mocked LLM."""

    async def test_judge_passes_good_answer(self) -> None:
        mock_response = AIMessage(content='{"passed": true, "explanation": "Answer covers all rubric points"}')
        with patch("src.eval.judge.ChatOpenAI") as mock_llm_cls:
            mock_instance = AsyncMock()
            mock_instance.ainvoke.return_value = mock_response
            mock_llm_cls.return_value = mock_instance

            score = await judge_answer(
                question="What alerts are firing?",
                answer="The HighCPU alert is firing on jellyfin with warning severity.",
                rubric="Should mention HighCPU and jellyfin",
                openai_api_key="sk-fake",
            )

        assert score.passed
        assert "rubric" in score.explanation.lower() or "covers" in score.explanation.lower()

    async def test_judge_fails_bad_answer(self) -> None:
        mock_response = AIMessage(content='{"passed": false, "explanation": "Answer does not mention the alert"}')
        with patch("src.eval.judge.ChatOpenAI") as mock_llm_cls:
            mock_instance = AsyncMock()
            mock_instance.ainvoke.return_value = mock_response
            mock_llm_cls.return_value = mock_instance

            score = await judge_answer(
                question="What alerts are firing?",
                answer="I'm not sure.",
                rubric="Should mention HighCPU",
                openai_api_key="sk-fake",
            )

        assert not score.passed

    async def test_judge_strips_markdown_fences(self) -> None:
        fenced = '```json\n{"passed": true, "explanation": "All criteria met"}\n```'
        mock_response = AIMessage(content=fenced)
        with patch("src.eval.judge.ChatOpenAI") as mock_llm_cls:
            mock_instance = AsyncMock()
            mock_instance.ainvoke.return_value = mock_response
            mock_llm_cls.return_value = mock_instance

            score = await judge_answer(
                question="What alerts are firing?",
                answer="HighCPU on jellyfin",
                rubric="Should mention HighCPU",
                openai_api_key="sk-fake",
            )

        assert score.passed
        assert "criteria" in score.explanation.lower()

    async def test_judge_handles_malformed_json(self) -> None:
        mock_response = AIMessage(content="This is not JSON at all")
        with patch("src.eval.judge.ChatOpenAI") as mock_llm_cls:
            mock_instance = AsyncMock()
            mock_instance.ainvoke.return_value = mock_response
            mock_llm_cls.return_value = mock_instance

            score = await judge_answer(
                question="Test?",
                answer="Answer",
                rubric="Rubric",
                openai_api_key="sk-fake",
            )

        assert not score.passed
        assert "Failed to parse" in score.explanation

    async def test_judge_extracts_last_json_from_multi_object_response(self) -> None:
        """Judge self-corrects with two JSON objects — use the last one."""
        multi_json = (
            '{"passed": false, "explanation": "Wait, let me reconsider"}\n\n'
            '{"passed": true, "explanation": "All criteria met on review"}'
        )
        mock_response = AIMessage(content=multi_json)
        with patch("src.eval.judge.ChatOpenAI") as mock_llm_cls:
            mock_instance = AsyncMock()
            mock_instance.ainvoke.return_value = mock_response
            mock_llm_cls.return_value = mock_instance

            score = await judge_answer(
                question="Test?",
                answer="Answer",
                rubric="Rubric",
                openai_api_key="sk-fake",
            )

        assert score.passed
        assert "review" in score.explanation.lower()


@pytest.mark.integration
class TestRunnerSettingsPatching:
    """Test that the runner correctly builds fake settings."""

    def test_build_fake_settings_enables_required_services(self) -> None:
        from src.eval.runner import _build_fake_settings

        case = EvalCase(
            id="test",
            description="Test",
            question="Test?",
            required_services=["proxmox", "loki"],
            expected_tools=ExpectedTools(must_call=[]),
            mocks=[],
            rubric="Test",
        )
        settings = _build_fake_settings(case, "sk-fake", "gpt-4o-mini")
        assert settings.proxmox_url == "https://proxmox.test:8006"  # type: ignore[attr-defined]
        assert settings.loki_url == "http://loki.test:3100"  # type: ignore[attr-defined]
        # Services not in required_services should be empty
        assert settings.truenas_url == ""  # type: ignore[attr-defined]
        assert settings.pbs_url == ""  # type: ignore[attr-defined]

    def test_build_fake_settings_always_has_prometheus_and_grafana(self) -> None:
        from src.eval.runner import _build_fake_settings

        case = EvalCase(
            id="test",
            description="Test",
            question="Test?",
            required_services=[],
            expected_tools=ExpectedTools(must_call=[]),
            mocks=[],
            rubric="Test",
        )
        settings = _build_fake_settings(case, "sk-fake", "gpt-4o-mini")
        assert settings.prometheus_url  # type: ignore[attr-defined]
        assert settings.grafana_url  # type: ignore[attr-defined]


@pytest.mark.integration
class TestEvalCaseYamlParsing:
    """Verify all shipped YAML eval cases parse and have sensible content."""

    def test_all_cases_load_successfully(self) -> None:
        from src.eval.loader import load_eval_cases

        cases = load_eval_cases()
        assert len(cases) >= 15, f"Expected at least 15 cases, got {len(cases)}"

    def test_all_case_ids_are_unique(self) -> None:
        from src.eval.loader import load_eval_cases

        cases = load_eval_cases()
        ids = [c.id for c in cases]
        assert len(ids) == len(set(ids)), f"Duplicate case IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_all_non_memory_cases_have_at_least_one_mock(self) -> None:
        from src.eval.loader import load_eval_cases

        cases = load_eval_cases()
        for case in cases:
            # Memory-only cases use local SQLite — no HTTP mocks needed
            if case.id.startswith("memory-"):
                continue
            # Cases with only prometheus/grafana (always-on) still need mocks
            assert len(case.mocks) > 0, f"Case {case.id} has no mocks"

    def test_all_cases_have_rubric(self) -> None:
        from src.eval.loader import load_eval_cases

        cases = load_eval_cases()
        for case in cases:
            assert len(case.rubric.strip()) > 10, f"Case {case.id} has too short a rubric"
