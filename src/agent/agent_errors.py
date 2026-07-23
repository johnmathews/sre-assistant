"""Classify agent/SDK failures into typed, operator-actionable errors.

The Claude Agent SDK surfaces most failures as an opaque
``Command failed with exit code 1``.  This module maps a failure to a stable
``reason`` code plus an operator-facing message and the raw cause, so the API
can emit a structured SSE error event and the webapp can render specific copy.

Classification is best-effort and MUST NOT raise — it runs inside the failure
path, so any error here falls back to the generic bucket.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum

from src.agent.oauth_refresh import get_token_health

logger = logging.getLogger(__name__)

_AUTH_FAILED_MESSAGE = (
    "The agent can't authenticate to the LLM provider — its credential was "
    "rejected. An operator needs to renew it on the host and restart the agent."
)
_NO_ANSWER_MESSAGE = "The agent processed your question but couldn't produce an answer. Retrying is usually safe."


class AgentErrorReason(StrEnum):
    """Stable machine-readable failure codes shared with the webapp."""

    LLM_AUTH_FAILED = "llm_auth_failed"
    AGENT_NO_ANSWER = "agent_no_answer"
    # Reserved for later: LLM_PROVIDER_ERROR, TOOL_ERROR, RATE_LIMITED.


@dataclass(frozen=True)
class AgentError:
    """A classified failure: a reason code, a human message, and the raw cause."""

    reason: AgentErrorReason
    message: str
    detail: str


def classify_agent_failure(exc: Exception) -> AgentError:
    """Map an SDK failure to a typed, actionable error. Never raises."""
    try:
        status, detail = get_token_health()
        if status == "unhealthy":
            return AgentError(
                reason=AgentErrorReason.LLM_AUTH_FAILED,
                message=_AUTH_FAILED_MESSAGE,
                detail=detail or "OAuth credential unhealthy",
            )
    except Exception:
        # Classification must never mask the original failure, but a broken
        # health probe would misclassify auth failures as generic — leave a
        # debug trail without escalating (never raise from here).
        logger.debug("token-health probe failed during classification", exc_info=True)
    return AgentError(
        reason=AgentErrorReason.AGENT_NO_ANSWER,
        message=_NO_ANSWER_MESSAGE,
        detail=str(exc),
    )
