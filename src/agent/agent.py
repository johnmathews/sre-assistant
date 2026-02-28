"""LangChain agent assembly — wires tools, system prompt, and memory together."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver

from src.agent.history import save_conversation
from src.agent.llm import create_llm
from src.agent.tools.grafana_alerts import grafana_get_alert_rules, grafana_get_alerts
from src.agent.tools.loki import (
    loki_correlate_changes,
    loki_list_label_values,
    loki_metric_query,
    loki_query_logs,
)
from src.agent.tools.pbs import pbs_datastore_status, pbs_list_backups, pbs_list_tasks
from src.agent.tools.prometheus import (
    prometheus_instant_query,
    prometheus_range_query,
    prometheus_search_metrics,
)
from src.agent.tools.proxmox import (
    proxmox_get_guest_config,
    proxmox_list_guests,
    proxmox_list_tasks,
    proxmox_node_status,
)
from src.agent.tools.truenas import (
    truenas_apps,
    truenas_list_shares,
    truenas_pool_status,
    truenas_snapshots,
    truenas_system_status,
)
from src.config import get_settings
from src.observability.callbacks import MetricsCallbackHandler

# Conditional import — disk_status depends on both prometheus and truenas tools
try:
    from src.agent.tools.disk_status import hdd_power_status
except Exception:  # pragma: no cover
    hdd_power_status = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# LangGraph has no public type stubs — the compiled agent type is opaque to
# static analysers.  Using Any avoids cascading "partially unknown" warnings
# in every module that imports build_agent / invoke_agent.
type AgentGraph = Any

_PROMPT_PATH = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT_TEMPLATE = _PROMPT_PATH.read_text()


def _get_memory_context() -> str:
    """Load dynamic context from memory store for the system prompt.

    Returns a string to append to the system prompt, or empty string if
    memory is not configured or on any error.
    """
    try:
        from src.memory.context import get_open_incidents_context, get_recent_patterns_context

        parts: list[str] = []
        incidents_ctx = get_open_incidents_context()
        if incidents_ctx:
            parts.append(incidents_ctx)
        patterns_ctx = get_recent_patterns_context()
        if patterns_ctx:
            parts.append(patterns_ctx)
        return "\n".join(parts)
    except Exception:
        logger.debug("Failed to load memory context for system prompt", exc_info=True)
        return ""


def _extract_tool_names(messages: list[Any]) -> list[str]:
    """Extract tool names from AIMessage tool_calls in a message list."""
    tool_names: list[str] = []
    for msg in messages:
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls"):
            for tc in msg.tool_calls:
                if isinstance(tc, dict) and "name" in tc:
                    tool_names.append(tc["name"])
    return tool_names


def _post_response_actions(messages: list[Any], question: str, response_text: str) -> str:
    """Run post-response actions: save query pattern, detect incident suggestion.

    Returns any text to append to the response (e.g. incident suggestion),
    or empty string. Never raises.
    """
    try:
        from src.memory.context import detect_incident_suggestion
        from src.memory.store import (
            cleanup_old_query_patterns,
            get_initialized_connection,
            is_memory_configured,
            save_query_pattern,
        )

        if not is_memory_configured():
            return ""

        tool_names = _extract_tool_names(messages)

        # Save query pattern
        try:
            conn = get_initialized_connection()
            try:
                save_query_pattern(conn, question=question, tool_names=",".join(tool_names))
                cleanup_old_query_patterns(conn, keep=100)
            finally:
                conn.close()
        except Exception:
            logger.debug("Failed to save query pattern", exc_info=True)

        # Check for incident suggestion
        return detect_incident_suggestion(tool_names, response_text)
    except Exception:
        logger.debug("Post-response actions failed", exc_info=True)
        return ""


def _get_tools() -> list[BaseTool]:
    """Collect all agent tools, conditionally including optional integrations."""
    tools: list[BaseTool] = [
        prometheus_search_metrics,
        prometheus_instant_query,
        prometheus_range_query,
        grafana_get_alerts,
        grafana_get_alert_rules,
    ]

    settings = get_settings()

    # Proxmox VE tools — only if configured
    if settings.proxmox_url:
        tools.extend(
            [
                proxmox_list_guests,
                proxmox_get_guest_config,
                proxmox_node_status,
                proxmox_list_tasks,
            ]
        )
    else:
        logger.info("Proxmox VE tools disabled — PROXMOX_URL not set")

    # TrueNAS SCALE tools — only if configured
    if settings.truenas_url:
        tools.extend(
            [
                truenas_pool_status,
                truenas_list_shares,
                truenas_snapshots,
                truenas_system_status,
                truenas_apps,
            ]
        )
        # Composite HDD tool — needs Prometheus (always available) + TrueNAS
        if hdd_power_status is not None:
            tools.append(hdd_power_status)  # pyright: ignore[reportUnknownArgumentType]
    else:
        logger.info("TrueNAS tools disabled — TRUENAS_URL not set")

    # Loki log tools — only if configured
    if settings.loki_url:
        tools.extend(
            [
                loki_query_logs,
                loki_metric_query,
                loki_list_label_values,
                loki_correlate_changes,
            ]
        )
    else:
        logger.info("Loki tools disabled — LOKI_URL not set")

    # Proxmox Backup Server tools — only if configured
    if settings.pbs_url:
        tools.extend(
            [
                pbs_datastore_status,
                pbs_list_backups,
                pbs_list_tasks,
            ]
        )
    else:
        logger.info("PBS tools disabled — PBS_URL not set")

    # Only include runbook search if vector store exists
    try:
        from src.agent.retrieval.runbooks import runbook_search

        tools.append(runbook_search)
    except Exception:
        logger.warning("Runbook search tool unavailable — run 'make ingest' to build the vector store")

    # Memory tools — only if MEMORY_DB_PATH is configured
    try:
        from src.memory.tools import get_memory_tools

        memory_tools = get_memory_tools()
        if memory_tools:
            tools.extend(memory_tools)
            logger.info("Memory tools enabled: %s", [t.name for t in memory_tools])
        else:
            logger.info("Memory tools disabled — MEMORY_DB_PATH not set")
    except Exception:
        logger.warning("Memory tools unavailable")

    return tools


def build_agent(
    model_name: str | None = None,
    temperature: float = 0.0,
) -> AgentGraph:
    """Build and return the SRE assistant agent.

    Args:
        model_name: LLM model to use. Defaults to the configured provider's model.
        temperature: LLM temperature (0.0 for deterministic tool-calling).

    Returns:
        A compiled LangGraph agent with tool-calling and conversation memory.
    """
    settings = get_settings()

    llm = create_llm(settings, temperature=temperature, model_override=model_name)

    tools = _get_tools()
    resolved_model = model_name or (
        settings.anthropic_model if settings.llm_provider == "anthropic" else settings.openai_model
    )
    logger.info("Building agent with model=%s, %d tools: %s", resolved_model, len(tools), [t.name for t in tools])

    now = datetime.now(UTC)
    system_prompt = (
        SYSTEM_PROMPT_TEMPLATE.replace("{current_time}", now.strftime("%Y-%m-%d %H:%M:%S"))
        .replace("{current_date}", now.strftime("%Y-%m-%d"))
        .replace("{retention_cutoff}", (now - timedelta(days=90)).strftime("%Y-%m-%d"))
    )

    # Inject dynamic context from memory store (best-effort, never fails build)
    system_prompt += _get_memory_context()

    checkpointer = MemorySaver()

    agent: AgentGraph = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )

    return agent


def _is_tool_call_pairing_error(exc: BaseException) -> bool:
    """Check if an exception is caused by orphaned tool_calls in conversation history.

    This happens when a previous request saved an AIMessage with tool_calls to the
    checkpoint but failed before the corresponding ToolMessages were added (e.g., due
    to a timeout). The OpenAI API rejects the malformed history on the next request.
    """
    msg = str(exc).lower()
    return "tool_calls" in msg and "tool messages" in msg


async def invoke_agent(
    agent: AgentGraph,
    message: str,
    session_id: str = "default",
) -> str:
    """Send a message to the agent and return the text response.

    Uses ainvoke because the tools are async (httpx-based).

    If a previous request left orphaned tool_calls in the session checkpoint
    (e.g., due to a timeout), this function detects the resulting OpenAI 400
    error and retries with a fresh session to avoid a permanently broken state.

    Args:
        agent: The compiled agent from build_agent().
        message: User's question.
        session_id: Conversation session ID for memory isolation.

    Returns:
        The agent's text response.
    """
    settings = get_settings()
    effective_session_id = session_id

    metrics_cb = MetricsCallbackHandler()
    config: RunnableConfig = {
        "configurable": {"thread_id": session_id},
        "callbacks": [metrics_cb],
    }

    try:
        result: dict[str, Any] = await agent.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config=config,
        )
    except Exception as exc:
        if _is_tool_call_pairing_error(exc):
            # Session history is corrupted — retry with a fresh thread to unblock
            fresh_id = f"{session_id}-{uuid4().hex[:6]}"
            effective_session_id = fresh_id
            logger.warning(
                "Session '%s' has corrupted tool-call history; retrying with fresh session '%s'",
                session_id,
                fresh_id,
            )
            fresh_cb = MetricsCallbackHandler()
            fresh_config: RunnableConfig = {
                "configurable": {"thread_id": fresh_id},
                "callbacks": [fresh_cb],
            }
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=message)]},
                config=fresh_config,
            )
        else:
            raise

    # Extract the last AI message from the result
    messages: list[Any] = result.get("messages", [])

    # Persist full conversation history if configured
    if settings.conversation_history_dir:
        active_model = settings.anthropic_model if settings.llm_provider == "anthropic" else settings.openai_model
        save_conversation(
            settings.conversation_history_dir,
            effective_session_id,
            messages,
            active_model,
        )

    response_text = "No response generated."
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content:
            response_text = msg.content
            break

    # Post-response: save query pattern + suggest incident recording
    suggestion = _post_response_actions(messages, message, response_text)
    if suggestion:
        response_text += suggestion

    return response_text
