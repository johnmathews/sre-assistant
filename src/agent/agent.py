"""LangChain agent assembly — wires tools, system prompt, and memory together."""

import logging
from typing import Any

from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from pydantic import SecretStr

from src.agent.tools.grafana_alerts import grafana_get_alert_rules, grafana_get_alerts
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
from src.config import get_settings

logger = logging.getLogger(__name__)

# LangGraph has no public type stubs — the compiled agent type is opaque to
# static analysers.  Using Any avoids cascading "partially unknown" warnings
# in every module that imports build_agent / invoke_agent.
type AgentGraph = Any

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

**For Proxmox VE** (VM/container management, node health):
- `proxmox_list_guests` — list all VMs and containers with status and resource usage
- `proxmox_get_guest_config` — detailed config for a specific VM/container (disks, network, boot)
- `proxmox_node_status` — host node CPU, memory, load, PVE version
- `proxmox_list_tasks` — recent Proxmox tasks (migrations, snapshots, backups)

**For Proxmox Backup Server** (backup status, storage):
- `pbs_datastore_status` — backup storage usage across datastores
- `pbs_list_backups` — backup groups showing last backup time and snapshot count per guest
- `pbs_list_tasks` — recent PBS tasks (backup jobs, GC, verification)

**For operational knowledge** (how things work, how to fix them, architecture):
- `runbook_search` — search runbooks for procedures, troubleshooting steps, architecture docs

## Proxmox API vs Prometheus pve_* Metrics

Both provide VM/LXC information but serve different purposes:
- **Proxmox API tools** (`proxmox_*`): detailed configuration (disks, network interfaces, boot \
order), guest management tasks, node-level system info. Use when asked about specific guest \
config, hardware assignments, or recent PVE operations.
- **Prometheus pve_* metrics** (via `prometheus_*` tools): time-series resource usage \
(CPU %, memory %, disk I/O, network traffic), historical trends, alerting thresholds. Use when \
asked about performance over time or current utilization.
- **PBS tools** (`pbs_*`): backup-specific questions (space left, last backup time, failed jobs).

## Infrastructure Inventory via Prometheus

Prometheus scrapes `pve_exporter`, which exposes VM and LXC inventory as metrics:
- `pve_guest_info` — one series per guest with labels: `name`, `id`, `type` (qemu=VM, lxc=container), `status`, `node`
- Use `count(pve_guest_info{type="qemu"})` to count VMs, `count(pve_guest_info{type="lxc"})` for LXCs
- Use `pve_guest_info` (without count) to list all guests with their labels
- Other `pve_*` metrics cover guest CPU, memory, disk, network, and uptime

## Common PromQL Patterns

Use these patterns when constructing Prometheus queries:

**Ranking / "which has the highest...":**
- `topk(5, pve_cpu_usage_ratio)` — top 5 guests by current CPU usage
- `bottomk(3, node_filesystem_avail_bytes)` — 3 filesystems with least free space

**Grouping / "per host" or "per VM":**
- `count by (hostname) (container_last_seen)` — container count per host
- `sum by (hostname) (node_memory_MemTotal_bytes)` — total memory per host

**Historical aggregation / "average over the last day":**
- `topk(5, avg_over_time(pve_cpu_usage_ratio[1d]))` — highest average CPU over last day
- `max_over_time(node_load1{hostname="jellyfin"}[6h])` — peak 1-min load in last 6 hours

**Rates for counters / "how fast is...":**
- `rate(node_network_receive_bytes_total{hostname="media"}[5m])` — network receive rate
- `rate(node_cpu_seconds_total{mode="idle"}[5m])` — CPU idle rate (subtract from 1 for usage)

**Disk and memory:**
- `node_filesystem_avail_bytes / node_filesystem_size_bytes` — filesystem usage ratio
- `1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)` — memory usage ratio

**Key metric prefixes:**
- `node_*` — node_exporter (host-level CPU, memory, disk, network)
- `container_*` — cadvisor (Docker container metrics)
- `pve_*` — pve_exporter (Proxmox guest metrics: `pve_cpu_usage_ratio`, \
`pve_memory_usage_bytes`, `pve_disk_usage_bytes`, `pve_up`)
- `mktxp_*` — MikroTik router metrics

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

    return tools


def build_agent(
    model_name: str = "gpt-4o-mini",
    temperature: float = 0.0,
) -> AgentGraph:
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
        api_key=SecretStr(settings.openai_api_key),
    )

    tools = _get_tools()
    logger.info("Building agent with %d tools: %s", len(tools), [t.name for t in tools])

    checkpointer = MemorySaver()

    agent: AgentGraph = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )

    return agent


async def invoke_agent(
    agent: AgentGraph,
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
    config: RunnableConfig = {"configurable": {"thread_id": session_id}}

    result: dict[str, Any] = await agent.ainvoke(
        {"messages": [HumanMessage(content=message)]},
        config=config,
    )

    # Extract the last AI message from the result
    messages: list[Any] = result.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content:
            return msg.content

    return "No response generated."
