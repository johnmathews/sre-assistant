# Architecture

## System Overview

The HomeLab SRE Assistant is a LangChain-based AI agent that connects to live infrastructure telemetry and a knowledge
base to answer operational questions about a Proxmox homelab with 80+ services.

## Data Flow

The agent uses two distinct data access patterns:

```
                    User Question
                         |
                    FastAPI /ask
                         |
                  LangChain Agent
                   (tool router)
                    /    |    \
                   /     |     \
    Live Tool Calls    RAG       Proxmox APIs
         |           Retrieval      |
         v              |           v
  +-----------+         v     +-----------+
  |Prometheus |   +---------+ |Proxmox VE |
  |  (metrics)|   | Chroma  | |  (config) |
  +-----------+   | Vector  | +-----------+
  +-----------+   |  Store  | +-----------+
  |  Grafana  |   +---------+ |   PBS     |
  | (alerts)  |       |       | (backups) |
  +-----------+   Runbooks    +-----------+
                  Playbooks
```

### Live Tool Calls

Structured API queries executed in real-time. Used for questions about current system state.

- **Prometheus** (`prometheus_*` tools) — metrics: CPU, memory, disk, network, custom exporters
- **Grafana** (`grafana_*` tools) — alert states, alert rule definitions
- **Proxmox VE** (`proxmox_*` tools) — VM/container config, node status, tasks
- **PBS** (`pbs_*` tools) — backup storage, backup groups, backup tasks

### RAG Retrieval

Embedded documents retrieved by semantic similarity. Used for operational knowledge.

- **Runbooks** — troubleshooting procedures, architecture docs, service configs
- **Ansible playbooks** — infrastructure-as-code, role definitions

The LangChain agent decides which approach to use based on the question.

## Service Dependencies

```
HomeLab SRE Assistant
  |
  +-- OpenAI API (LLM inference)
  |
  +-- Prometheus (metrics, scraping pve_exporter, node_exporter, cadvisor, etc.)
  |
  +-- Grafana (alerting API, unified alerting)
  |
  +-- Proxmox VE API (optional — VM/container management)
  |
  +-- Proxmox Backup Server API (optional — backup status)
  |
  +-- Chroma vector store (local, on-disk)
```

Required: OpenAI API, Prometheus, Grafana.
Optional: Proxmox VE, PBS (tools are conditionally registered based on config).
Local: Chroma vector store (rebuilt via `make ingest`).

## Request Lifecycle

See [code-flow.md](code-flow.md) for the detailed request lifecycle.

## Failure Handling

Every external dependency has explicit error handling:

- **ConnectError** — "Cannot connect to {service} at {url}"
- **TimeoutException** — "{service} request timed out after {n}s"
- **HTTPStatusError** — "{service} API error: HTTP {code} - {body}"

All tools set `handle_tool_error = True` so errors are returned to the LLM as text (not raised as exceptions), allowing
the agent to report failures gracefully to the user.

## Configuration

Settings are loaded from environment variables via `pydantic-settings`. The `Settings` class in `src/config.py` defines
all configuration with sensible defaults. Optional integrations (Proxmox VE, PBS) default to empty strings, which
disables their tools.
