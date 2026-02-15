"""LangChain agent assembly — wires tools, system prompt, and memory together."""

import logging

from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]
from langchain_core.messages import AIMessage, HumanMessage  # pyright: ignore[reportUnknownVariableType]
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from src.agent.tools.grafana_alerts import grafana_get_alert_rules, grafana_get_alerts
from src.agent.tools.prometheus import (
    prometheus_instant_query,
    prometheus_range_query,
    prometheus_search_metrics,
)
from src.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an SRE assistant for a Proxmox homelab running 80+ services across multiple VMs and LXCs.

You have access to live infrastructure tools and a knowledge base of operational runbooks.

## Tool Selection Guide

**For live system state** (metrics, alerts, what's happening right now):
- `prometheus_search_metrics` — discover available metric names matching a keyword
- `prometheus_instant_query` — current metric values (CPU, memory, disk, network)
- `prometheus_range_query` — metric trends over a time range
- `grafana_get_alerts` — active/firing alerts and their state
- `grafana_get_alert_rules` — configured alert rule definitions

**For operational knowledge** (how things work, how to fix them, architecture):
- `runbook_search` — search runbooks for procedures, troubleshooting steps, architecture docs

## Guidelines

- When unsure of a metric name, **search first** with `prometheus_search_metrics` to discover \
available metrics before querying. Do not guess metric names.
- When investigating an issue, **query metrics first** to understand what's happening, \
then **search runbooks** for relevant procedures or context.
- When asked about alerts, fetch live alert data — don't guess from runbooks.
- When asked "how do I fix X" or "what's the procedure for Y", search runbooks.
- Be specific about which host, service, or metric you're referencing.
- If a tool call fails, tell the user clearly and suggest what to check.
- Never fabricate metric values or alert states — only report what the tools return.
- Keep answers concise and actionable. Lead with the answer, then provide supporting detail.
"""


def _get_tools() -> list:  # type: ignore[type-arg]
    """Collect all agent tools, conditionally including runbook search."""
    tools: list = [  # type: ignore[type-arg]
        prometheus_search_metrics,
        prometheus_instant_query,
        prometheus_range_query,
        grafana_get_alerts,
        grafana_get_alert_rules,
    ]

    # Only include runbook search if vector store exists
    try:
        from src.agent.retrieval.runbooks import runbook_search

        tools.append(runbook_search)
    except Exception:
        logger.warning("Runbook search tool unavailable — run 'make ingest' to build the vector store")

    return tools


def build_agent(
    model_name: str = "gpt-4o-mini",
    temperature: float = 0.0,
) -> CompiledStateGraph:  # type: ignore[type-arg]
    """Build and return the SRE assistant agent.

    Args:
        model_name: OpenAI model to use (default: gpt-4o-mini for cost efficiency).
        temperature: LLM temperature (0.0 for deterministic tool-calling).

    Returns:
        A compiled LangGraph agent with tool-calling and conversation memory.
    """
    settings = get_settings()

    llm = ChatOpenAI(
        model=model_name,
        temperature=temperature,
        api_key=settings.openai_api_key,  # type: ignore[arg-type]
    )

    tools = _get_tools()
    logger.info("Building agent with %d tools: %s", len(tools), [t.name for t in tools])

    checkpointer = MemorySaver()

    agent: CompiledStateGraph = create_agent(  # type: ignore[type-arg]
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )

    return agent


async def invoke_agent(
    agent: CompiledStateGraph,  # type: ignore[type-arg]
    message: str,
    session_id: str = "default",
) -> str:
    """Send a message to the agent and return the text response.

    Uses ainvoke because the tools are async (httpx-based).

    Args:
        agent: The compiled agent from build_agent().
        message: User's question.
        session_id: Conversation session ID for memory isolation.

    Returns:
        The agent's text response.
    """
    config = {"configurable": {"thread_id": session_id}}

    result = await agent.ainvoke(  # pyright: ignore[reportUnknownMemberType]
        {"messages": [HumanMessage(content=message)]},
        config=config,  # type: ignore[arg-type]
    )

    # Extract the last AI message from the result
    messages = result.get("messages", [])  # pyright: ignore[reportUnknownMemberType]
    for msg in reversed(messages):  # pyright: ignore[reportUnknownVariableType]
        if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content:
            return msg.content

    return "No response generated."
