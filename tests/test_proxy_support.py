"""Tests for OpenAI-compatible proxy support (base_url passthrough)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.observability.metrics import COST_PER_TOKEN

# ---------------------------------------------------------------------------
# Unit tests — no mocks, no IO
# ---------------------------------------------------------------------------


class TestCostTableClaudeModels:
    """Verify Claude model entries exist in the cost table."""

    def test_claude_sonnet_4_in_cost_table(self) -> None:
        assert "claude-sonnet-4" in COST_PER_TOKEN
        entry = COST_PER_TOKEN["claude-sonnet-4"]
        assert "prompt" in entry
        assert "completion" in entry

    def test_claude_opus_4_in_cost_table(self) -> None:
        assert "claude-opus-4" in COST_PER_TOKEN
        entry = COST_PER_TOKEN["claude-opus-4"]
        assert "prompt" in entry
        assert "completion" in entry

    def test_claude_haiku_4_in_cost_table(self) -> None:
        assert "claude-haiku-4" in COST_PER_TOKEN
        entry = COST_PER_TOKEN["claude-haiku-4"]
        assert "prompt" in entry
        assert "completion" in entry


class TestCallbackClaudeModelMatching:
    """Verify prefix matching finds Claude models in the cost table."""

    @pytest.mark.parametrize(
        "model_name,expected_prefix",
        [
            ("claude-sonnet-4-20250514", "claude-sonnet-4"),
            ("claude-opus-4-20250514", "claude-opus-4"),
            ("claude-haiku-4-20251001", "claude-haiku-4"),
        ],
    )
    def test_prefix_matching_finds_claude_model(self, model_name: str, expected_prefix: str) -> None:
        """The callback's prefix-matching loop should match versioned Claude model names."""
        matched_prefix: str | None = None
        for prefix in COST_PER_TOKEN:
            if model_name.startswith(prefix):
                matched_prefix = prefix
                break
        assert matched_prefix == expected_prefix


class TestBaseUrlConversion:
    """Verify the empty-string-to-None conversion pattern."""

    def test_empty_string_converts_to_none(self) -> None:
        base_url = ""
        assert (base_url or None) is None

    def test_non_empty_string_passes_through(self) -> None:
        base_url = "http://localhost:3456/v1"
        assert (base_url or None) == "http://localhost:3456/v1"


# ---------------------------------------------------------------------------
# Integration tests — mocked ChatOpenAI, no real LLM calls
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAgentBuilderBaseUrl:
    """Verify build_agent passes base_url to ChatOpenAI."""

    def test_base_url_passed_when_configured(self, mock_settings: MagicMock) -> None:
        mock_settings.openai_base_url = "http://localhost:3456/v1"
        with (
            patch("src.agent.agent.ChatOpenAI") as mock_llm_cls,
            patch("src.agent.agent.create_agent") as mock_create,
        ):
            mock_create.return_value = MagicMock()
            from src.agent.agent import build_agent

            build_agent()
            mock_llm_cls.assert_called_once()
            call_kwargs = mock_llm_cls.call_args.kwargs
            assert call_kwargs["base_url"] == "http://localhost:3456/v1"

    def test_base_url_none_when_empty(self, mock_settings: MagicMock) -> None:
        mock_settings.openai_base_url = ""
        with (
            patch("src.agent.agent.ChatOpenAI") as mock_llm_cls,
            patch("src.agent.agent.create_agent") as mock_create,
        ):
            mock_create.return_value = MagicMock()
            from src.agent.agent import build_agent

            build_agent()
            mock_llm_cls.assert_called_once()
            call_kwargs = mock_llm_cls.call_args.kwargs
            assert call_kwargs["base_url"] is None


@pytest.mark.integration
class TestReportGeneratorBaseUrl:
    """Verify _generate_narrative passes base_url to ChatOpenAI."""

    @pytest.mark.asyncio
    async def test_base_url_passed_when_configured(self, mock_settings: MagicMock) -> None:
        mock_settings.openai_base_url = "http://localhost:3456/v1"
        with patch("src.report.generator.ChatOpenAI") as mock_llm_cls:
            mock_llm = MagicMock()
            mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="test narrative"))
            mock_llm_cls.return_value = mock_llm

            from src.report.generator import _generate_narrative

            await _generate_narrative({"alerts": None})
            mock_llm_cls.assert_called_once()
            call_kwargs = mock_llm_cls.call_args.kwargs
            assert call_kwargs["base_url"] == "http://localhost:3456/v1"

    @pytest.mark.asyncio
    async def test_base_url_none_when_empty(self, mock_settings: MagicMock) -> None:
        mock_settings.openai_base_url = ""
        with patch("src.report.generator.ChatOpenAI") as mock_llm_cls:
            mock_llm = MagicMock()
            mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="test narrative"))
            mock_llm_cls.return_value = mock_llm

            from src.report.generator import _generate_narrative

            await _generate_narrative({"alerts": None})
            mock_llm_cls.assert_called_once()
            call_kwargs = mock_llm_cls.call_args.kwargs
            assert call_kwargs["base_url"] is None


@pytest.mark.integration
class TestJudgeBaseUrl:
    """Verify judge_answer passes base_url to ChatOpenAI."""

    @pytest.mark.asyncio
    async def test_base_url_passed_when_provided(self) -> None:
        with patch("src.eval.judge.ChatOpenAI") as mock_llm_cls:
            mock_llm = MagicMock()
            mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"passed": true, "explanation": "ok"}'))
            mock_llm_cls.return_value = mock_llm

            from src.eval.judge import judge_answer

            await judge_answer(
                question="test?",
                answer="test answer",
                rubric="test rubric",
                openai_api_key="sk-test",
                base_url="http://localhost:3456/v1",
            )
            mock_llm_cls.assert_called_once()
            call_kwargs = mock_llm_cls.call_args.kwargs
            assert call_kwargs["base_url"] == "http://localhost:3456/v1"

    @pytest.mark.asyncio
    async def test_base_url_none_by_default(self) -> None:
        with patch("src.eval.judge.ChatOpenAI") as mock_llm_cls:
            mock_llm = MagicMock()
            mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"passed": true, "explanation": "ok"}'))
            mock_llm_cls.return_value = mock_llm

            from src.eval.judge import judge_answer

            await judge_answer(
                question="test?",
                answer="test answer",
                rubric="test rubric",
                openai_api_key="sk-test",
            )
            mock_llm_cls.assert_called_once()
            call_kwargs = mock_llm_cls.call_args.kwargs
            assert call_kwargs["base_url"] is None


@pytest.mark.integration
class TestEvalRunnerBaseUrl:
    """Verify _build_fake_settings includes openai_base_url."""

    def test_base_url_set_when_provided(self) -> None:
        from src.eval.models import EvalCase, ExpectedTools, MockResponse

        case = EvalCase(
            id="test",
            description="test case",
            question="test?",
            expected_tools=ExpectedTools(must_call=[], must_not_call=[]),
            rubric="test",
            mocks=[MockResponse(method="GET", url="http://test.test", status=200, body="{}")],
        )
        from src.eval.runner import _build_fake_settings

        settings = _build_fake_settings(case, "sk-test", "gpt-4o-mini", "http://localhost:3456/v1")
        assert settings.openai_base_url == "http://localhost:3456/v1"  # type: ignore[union-attr]

    def test_base_url_empty_by_default(self) -> None:
        from src.eval.models import EvalCase, ExpectedTools, MockResponse

        case = EvalCase(
            id="test",
            description="test case",
            question="test?",
            expected_tools=ExpectedTools(must_call=[], must_not_call=[]),
            rubric="test",
            mocks=[MockResponse(method="GET", url="http://test.test", status=200, body="{}")],
        )
        from src.eval.runner import _build_fake_settings

        settings = _build_fake_settings(case, "sk-test", "gpt-4o-mini")
        assert settings.openai_base_url == ""  # type: ignore[union-attr]
