"""Tests for agent failure classification."""

from unittest.mock import patch

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
