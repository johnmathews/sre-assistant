"""Tests for agent failure classification."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.agent_errors import (
    AgentErrorReason,
    classify_agent_failure,
)


def test_unhealthy_token_classifies_as_auth_failure() -> None:
    detail = "refresh token rejected by Anthropic (invalid_grant) — re-authenticate"
    with patch(
        "src.agent.agent_errors.get_token_health",
        return_value=("unhealthy", detail),
    ):
        err = classify_agent_failure(Exception("Command failed with exit code 1"))
    assert err.reason is AgentErrorReason.LLM_AUTH_FAILED
    assert "authenticate to the LLM provider" in err.message
    assert err.detail == detail


def test_healthy_token_classifies_as_generic_no_answer() -> None:
    with patch(
        "src.agent.agent_errors.get_token_health",
        return_value=("healthy", "access token valid (7.5h), refresh token present"),
    ):
        err = classify_agent_failure(Exception("boom"))
    assert err.reason is AgentErrorReason.AGENT_NO_ANSWER
    assert err.detail == "boom"


def test_classification_never_raises_when_health_check_errors() -> None:
    with patch(
        "src.agent.agent_errors.get_token_health",
        side_effect=RuntimeError("health check blew up"),
    ):
        err = classify_agent_failure(Exception("original failure"))
    assert err.reason is AgentErrorReason.AGENT_NO_ANSWER
    assert err.detail == "original failure"


@pytest.mark.asyncio
async def test_stream_emits_structured_auth_error(mock_settings: object) -> None:
    """A dead OAuth credential yields a reason-tagged error event, not an opaque one."""
    from src.agent import sdk_agent

    async def failing_query(**kwargs: object):  # type: ignore[no-untyped-def]
        raise RuntimeError("Command failed with exit code 1")
        yield  # pragma: no cover — makes this an async generator

    options = MagicMock()
    options.model = "test-model"
    options.env = {}

    with (
        patch("src.agent.sdk_agent.query", side_effect=failing_query),
        patch("src.agent.oauth_refresh.ensure_valid_token", new_callable=AsyncMock),
        patch("src.agent.sdk_agent._build_system_prompt", return_value="test prompt"),
        patch("src.agent.sdk_agent.ClaudeAgentOptions", return_value=options),
        patch(
            "src.agent.agent_errors.get_token_health",
            return_value=("unhealthy", "refresh token rejected by Anthropic (invalid_grant)"),
        ),
    ):
        events = [ev async for ev in sdk_agent.stream_sdk_agent(options, "hi")]

    error_events = [ev for ev in events if ev["type"] == "error"]
    assert len(error_events) == 1
    err = error_events[0]
    assert err["reason"] == "llm_auth_failed"
    assert "authenticate to the LLM provider" in err["content"]
    assert "invalid_grant" in err["detail"]
