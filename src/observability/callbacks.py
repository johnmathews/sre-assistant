"""LangChain callback handler that records Prometheus metrics.

Create a fresh ``MetricsCallbackHandler`` per request and pass it via
``config["callbacks"]``.  The handler writes to module-level metric
singletons defined in :mod:`src.observability.metrics`.

All callback methods are wrapped in try/except — metrics collection must
never crash a request.
"""

import logging
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from src.observability.metrics import (
    COST_PER_TOKEN,
    DEFAULT_COST_PER_TOKEN,
    LLM_CALLS_TOTAL,
    LLM_ESTIMATED_COST,
    LLM_TOKEN_USAGE,
    TOOL_CALL_DURATION,
    TOOL_CALLS_TOTAL,
)

logger = logging.getLogger(__name__)


class MetricsCallbackHandler(BaseCallbackHandler):
    """Captures tool-call and LLM metrics inside LangGraph's execution loop.

    Per-request instance — ``_start_times`` is request-scoped, but all
    counter/histogram writes go to the module-level Prometheus singletons.
    """

    def __init__(self) -> None:
        super().__init__()
        # run_id → (start_time, tool_name)
        self._start_times: dict[UUID, tuple[float, str]] = {}

    # ------------------------------------------------------------------
    # Tool callbacks
    # ------------------------------------------------------------------

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            tool_name: str = serialized.get("name", "unknown")
            self._start_times[run_id] = (time.monotonic(), tool_name)
        except Exception:
            logger.debug("metrics: on_tool_start failed", exc_info=True)

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            entry = self._start_times.pop(run_id, None)
            if entry is not None:
                start_time, tool_name = entry
                duration = time.monotonic() - start_time
                TOOL_CALL_DURATION.labels(tool_name=tool_name).observe(duration)
                TOOL_CALLS_TOTAL.labels(tool_name=tool_name, status="success").inc()
        except Exception:
            logger.debug("metrics: on_tool_end failed", exc_info=True)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            entry = self._start_times.pop(run_id, None)
            if entry is not None:
                start_time, tool_name = entry
                duration = time.monotonic() - start_time
                TOOL_CALL_DURATION.labels(tool_name=tool_name).observe(duration)
                TOOL_CALLS_TOTAL.labels(tool_name=tool_name, status="error").inc()
        except Exception:
            logger.debug("metrics: on_tool_error failed", exc_info=True)

    # ------------------------------------------------------------------
    # LLM callbacks
    # ------------------------------------------------------------------

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            LLM_CALLS_TOTAL.labels(status="success").inc()

            llm_output = response.llm_output
            if not llm_output:
                return

            token_usage: dict[str, int] | None = llm_output.get("token_usage")
            if not token_usage:
                return

            prompt_tokens = token_usage.get("prompt_tokens", 0)
            completion_tokens = token_usage.get("completion_tokens", 0)

            LLM_TOKEN_USAGE.labels(type="prompt").inc(prompt_tokens)
            LLM_TOKEN_USAGE.labels(type="completion").inc(completion_tokens)

            # Cost estimation
            model_name: str = llm_output.get("model_name", "")
            pricing = DEFAULT_COST_PER_TOKEN
            for prefix, costs in COST_PER_TOKEN.items():
                if model_name.startswith(prefix):
                    pricing = costs
                    break

            cost = (prompt_tokens * pricing["prompt"]) + (completion_tokens * pricing["completion"])
            LLM_ESTIMATED_COST.inc(cost)
        except Exception:
            logger.debug("metrics: on_llm_end failed", exc_info=True)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            LLM_CALLS_TOTAL.labels(status="error").inc()
        except Exception:
            logger.debug("metrics: on_llm_error failed", exc_info=True)
