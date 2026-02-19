"""LangChain agent assembly — wires tools, system prompt, and memory together."""

import logging
from typing import Any
from uuid import uuid4

from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from pydantic import SecretStr

from src.agent.history import save_conversation
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
- `proxmox_get_guest_config` — detailed config for a specific VM/container (disks, network, boot). \
Accepts either `name` (e.g. 'immich') or `vmid` — prefer using `name` when you don't know the VMID
- `proxmox_node_status` — host node CPU, memory, load, PVE version
- `proxmox_list_tasks` — recent Proxmox tasks (migrations, snapshots, backups)

**For Proxmox Backup Server** (backup status, storage):
- `pbs_datastore_status` — backup storage usage across datastores
- `pbs_list_backups` — backup groups showing last backup time and snapshot count per guest
- `pbs_list_tasks` — recent PBS tasks (backup jobs, GC, verification)

**For TrueNAS NAS** (storage, shares, snapshots, replication, apps):
- `truenas_pool_status` — ZFS pool health AND per-dataset space usage (used/available for each dataset)
- `truenas_list_shares` — NFS and SMB share configuration
- `truenas_snapshots` — ZFS snapshots, snapshot schedules, replication tasks
- `truenas_system_status` — TrueNAS version, alerts, running jobs, disk inventory
- `truenas_apps` — installed TrueNAS apps with running state

**For HDD power state** (spinup/spindown, disk activity):
- `hdd_power_status` — **USE THIS** for any HDD power state question. Returns a complete \
summary: which disks are spun up/standby with human-readable names (model, size, serial), \
and when each disk last changed power state. Accepts optional `duration` (default '24h', \
e.g. '1h', '12h', '3d', '1w') and `pool` filter (e.g. 'tank', 'backup'). Handles all \
cross-referencing and transition detection automatically. Do NOT use \
prometheus_instant_query for disk_power_state — use this tool instead.

**For logs** (application logs, errors, container lifecycle events):
- `loki_query_logs` — query log lines using LogQL (general-purpose log search)
- `loki_metric_query` — count, rate, or aggregate logs (e.g. log volume by host, error rate \
by service). Uses LogQL metric queries like count_over_time, rate, sum by, topk. \
**IMPORTANT**: these are Loki queries, NOT PromQL — never send them to prometheus_instant_query.
- `loki_list_label_values` — discover available hostnames, services, containers, log levels
- `loki_correlate_changes` — find significant events around a reference time (change correlation)

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

## TrueNAS API vs Prometheus Metrics

Both provide NAS information but serve different purposes:
- **TrueNAS API tools** (`truenas_*`): configuration and state — pool health, share definitions, \
snapshot inventory, app status, alerts, disk inventory. Use for "what's configured?" and \
"what's the current state?"
- **HDD power state**: Always use `hdd_power_status` — do NOT manually query `disk_power_state` \
from Prometheus. The composite tool handles all cross-referencing and transition detection.
- **Prometheus node_* metrics** on the NAS host: CPU, memory, disk I/O time-series data.

## Infrastructure Inventory via Prometheus

Prometheus scrapes `pve_exporter`, which exposes VM and LXC inventory as metrics:
- `pve_guest_info` — one series per guest with labels: `name`, `id`, `type` (qemu=VM, lxc=container), `status`, `node`
- Use `count(pve_guest_info{type="qemu"})` to count VMs, `count(pve_guest_info{type="lxc"})` for LXCs
- Use `pve_guest_info` (without count) to list all guests with their labels
- Other `pve_*` metrics cover guest CPU, memory, disk, network, and uptime

**IMPORTANT — pve_* metric labels differ by metric:**
- `pve_guest_info` has: `id`, `name`, `type`, `status`, `node` (inventory/info metric)
- `pve_cpu_usage_ratio`, `pve_memory_usage_bytes`, `pve_disk_usage_bytes`, `pve_up`, \
`pve_uptime_seconds` have: `id`, `name`, `node` — but NOT `type`
- Do NOT use `{type="qemu"}` on resource metrics — it will return no results
- To filter resource metrics by guest type, either filter by known names \
(e.g. `{name=~"media|infra|truenas"}`) or use `proxmox_list_guests` instead
- When unsure what labels a metric has, query it without any filters first \
(e.g. just `pve_cpu_usage_ratio`) to see the available label sets

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

**Detecting value transitions / "when did X last change?":**
- `changes(some_metric[1h])` — count of value changes in a window (0 = stable, >0 = changed)
- Widen progressively: `[1h]` → `[6h]` → `[24h]` → `[7d]` to find the window containing changes
- Then use `prometheus_range_query` with a small step over that window to pinpoint the timestamp
- A range query returning constant values means NO change occurred — that is valid data, not "no data"

**Key metric prefixes:**
- `node_*` — node_exporter (host-level CPU, memory, disk, network)
- `container_*` — cadvisor (Docker container metrics)
- `pve_*` — pve_exporter (Proxmox guest metrics: `pve_cpu_usage_ratio`, \
`pve_memory_usage_bytes`, `pve_disk_usage_bytes`, `pve_up`)
- `mktxp_*` — MikroTik router metrics
- `disk_power_state` — disk-status-exporter on TrueNAS (HDD power state: 0=standby, \
1=idle, 2=active/idle, -1=unknown). See "HDD power state questions" section above for strategy.
- `disk_info` — disk-status-exporter (disk identity, always 1). Labels: device_id, type, pool

## Loki Log Querying

Logs are collected by Alloy from Docker containers and some systemd journal units, shipped to Loki.

**Available labels (every log stream has these 4):**
- `hostname` — the VM/LXC name (same as Prometheus hostname label)
- `service_name` — Docker service or systemd unit name
- `container` — Docker container name
- `detected_level` — normalized log level: debug, info, notice, warn, error, fatal, verbose, trace

**When to use Loki tools vs Prometheus:**
- **Loki** = text logs, error messages, application output, container lifecycle events
- **Prometheus** = numeric metrics, rates, aggregations, time-series trends
- **IMPORTANT**: LogQL metric functions (`count_over_time`, `rate`, `sum by`, `topk`) are \
Loki queries — use `loki_metric_query`, NEVER `prometheus_instant_query`

**LogQL tips (for loki_query_logs — returns log lines):**
- Always include at least one label filter: `{hostname="media"}` not `{}`
- Use `|=` for substring match: `{service_name="traefik"} |= "502"`
- Use `|~` for regex: `{hostname="infra"} |~ "(?i)error"`
- Use `detected_level` to filter by severity: `{detected_level=~"error|warn"}`
- Start with `loki_list_label_values` to discover what services/hosts exist before querying

**LogQL metric queries (for loki_metric_query — returns numbers):**
- `topk(5, sum by (hostname) (count_over_time({hostname=~".+"}[24h])))` — top 5 hosts by log volume
- `sum by (service_name) (count_over_time({detected_level="error"}[1h]))` — errors per service
- `sum(rate({hostname="media"}[5m]))` — current log rate for a host
- `sum by (detected_level) (count_over_time({hostname="infra"}[24h]))` — log breakdown by level
- The `[duration]` inside the query is the lookback window; no step needed for instant results

**When to use `loki_correlate_changes`:**
- "What changed before this alert?" — pass the alert's firing time as reference_time
- "What happened around 2pm?" — pass the ISO timestamp
- "Show me what went wrong on infra" — use hostname filter with reference_time="now"

TrueNAS runs Alloy as an app, so TrueNAS app logs (containers) are available in Loki.
Use `hostname` matching the TrueNAS host and `service_name` matching the app name to find logs.

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
- When users say "VM" they usually mean any Proxmox guest (VMs AND containers). \
Call `proxmox_list_guests` without a type filter to include both, unless the user \
specifically says "QEMU VM" or "LXC container".
- When asked about resource utilization (most/least used, busiest, most underused), \
consider **multiple dimensions**: CPU, memory usage, and allocated-but-unused resources. \
Also consider stopped guests that still consume allocated resources (disk, reserved RAM). \
Note when guests are tied or very close in usage. Prefer querying `proxmox_list_guests` \
(which shows CPU %) alongside Prometheus memory metrics for a complete picture.
- **Fail fast on unanswerable questions.** If 2-3 tool calls return no relevant data, \
stop searching and clearly tell the user: (1) what you looked for, (2) why it's not \
available through your tools, and (3) how they could get the answer themselves \
(e.g. "SSH into the container and run `du -sh /var/lib/postgresql`"). Do not keep \
trying tangentially related tools hoping to stumble on an answer.
- When constructing Prometheus queries, prefer **compound queries** that answer the question \
in one call over sequential single-metric queries. For example, use \
`topk(5, pve_cpu_usage_ratio)` rather than querying each guest individually. \
Similarly, if you need both CPU and memory data, make both tool calls in parallel \
rather than waiting for one to finish before starting the other.
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

    return tools


def build_agent(
    model_name: str | None = None,
    temperature: float = 0.0,
) -> AgentGraph:
    """Build and return the SRE assistant agent.

    Args:
        model_name: OpenAI model to use. Defaults to OPENAI_MODEL from config.
        temperature: LLM temperature (0.0 for deterministic tool-calling).

    Returns:
        A compiled LangGraph agent with tool-calling and conversation memory.
    """
    settings = get_settings()
    resolved_model = model_name or settings.openai_model

    llm = ChatOpenAI(
        model=resolved_model,
        temperature=temperature,
        api_key=SecretStr(settings.openai_api_key),
    )

    tools = _get_tools()
    logger.info("Building agent with model=%s, %d tools: %s", resolved_model, len(tools), [t.name for t in tools])

    checkpointer = MemorySaver()

    agent: AgentGraph = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
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
        save_conversation(
            settings.conversation_history_dir,
            effective_session_id,
            messages,
            settings.openai_model,
        )

    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content:
            return msg.content

    return "No response generated."
