# HomeLab SRE Assistant

An AI-powered Site Reliability Engineering assistant for homelab infrastructure, built with LangChain. It connects to
live infrastructure telemetry (Prometheus, Grafana, Loki, Proxmox VE, PBS) and a RAG knowledge base (runbooks, Ansible
playbooks) to answer operational questions, explain alerts, correlate changes, and generate incident reports.

---

## Table of Contents

- [Getting Started](#getting-started)
  - [macOS Tahoe / Sequoia: Local Network Access](#macos-tahoe--sequoia-local-network-access)
- [Deploying with Docker](#deploying-with-docker)
  - [Adding to an Existing Docker Compose Stack](#adding-to-an-existing-docker-compose-stack)
  - [Environment Variables](#environment-variables)
  - [Networking](#networking)
  - [Vector Store Persistence](#vector-store-persistence)
  - [Updating](#updating)
  - [Building from Source](#building-from-source)
- [CI/CD](#cicd)
- [Motivation \& Context](#motivation--context)
- [Goals](#goals)
- [Architecture](#architecture)
- [Current Capabilities](#current-capabilities)
- [Use Cases](#use-cases)
- [Failure Modes \& Handling](#failure-modes--handling)
- [Conversation Memory](#conversation-memory)
- [Tech Stack](#tech-stack)
- [Roadmap](#roadmap)
- [Build Order](#build-order)
  - [Phase 1: Alert Explainer (Core Agent)](#phase-1-alert-explainer-core-agent)
  - [Phase 2: Synthetic Incident Generator](#phase-2-synthetic-incident-generator)
  - [Phase 3: Loki Log Tools](#phase-3-loki-log-tools)
  - [Phase 4: SLI/SLO Dashboard \& Instrumentation](#phase-4-slislo-dashboard--instrumentation)
  - [Phase 5: Evaluation Framework](#phase-5-evaluation-framework)
  - [Phase 6: Weekly Reliability Report](#phase-6-weekly-reliability-report)
- [Repository Structure](#repository-structure)
- [Non-Goals](#non-goals)
- [License](#license)

---

## Getting Started

```bash
# Install dependencies
make dev

# Copy and fill in your API keys
cp .env.example .env
# Edit .env — required: OPENAI_API_KEY, PROMETHEUS_URL, GRAFANA_URL, GRAFANA_SERVICE_ACCOUNT_TOKEN

# Build the runbook vector store (required before first use)
make ingest

# Start the interactive CLI
make chat

# Or start the FastAPI server
make serve
# POST /ask  — send questions to the agent
# GET /health — check infrastructure component status

# Start the Streamlit web UI (requires API server running)
make ui
# Opens browser at http://localhost:8501

# Run the full check suite (lint + typecheck + tests)
make check
```

### macOS Tahoe / Sequoia: Local Network Access

On macOS 15+ (Sequoia) and macOS 26+ (Tahoe), Apple restricts local network access for processes that aren't children of
a recognized terminal app. This affects `make chat` if you run it inside **tmux** — the agent's Prometheus/Grafana tool
calls will fail with `[Errno 65] No route to host` because tmux runs as a daemon under `launchd`, breaking the terminal's
local network exemption.

**Workaround:** Run `make chat` directly in your terminal (kitty, iTerm, Terminal.app) without tmux. Apple-signed
binaries (`/usr/bin/curl`, `/usr/bin/python3`) are exempt and always work, but Python installed via uv, pyenv, or
Homebrew is not Apple-signed and inherits permissions from the parent process chain.

This only affects local development on macOS. The agent runs without restrictions when deployed in Docker on Linux.

---

## Deploying with Docker

The project builds a single Docker image that runs as three services:

| Service      | Port | Description                              |
| ------------ | ---- | ---------------------------------------- |
| `sre-ingest` | —    | One-shot: builds the Chroma vector store |
| `sre-api`    | 8000 | FastAPI backend (`/ask`, `/health`)      |
| `sre-ui`     | 8501 | Streamlit web UI                         |

The intended deployment is on a Linux host (VM, LXC, bare metal) on the same LAN as your Prometheus, Grafana, and
other monitored infrastructure. The pre-built image is published to `ghcr.io/johnmathews/sre-assistant:latest` on
every push to `main`.

### Adding to an Existing Docker Compose Stack

If you already have a `docker-compose.yml` on your target host, add these service definitions and the `chroma_data`
volume:

```yaml
  # --- SRE Assistant services ---
  sre-ingest:
    image: ghcr.io/johnmathews/sre-assistant:latest
    command: ["python", "-m", "scripts.ingest_runbooks"]
    env_file: .env
    volumes:
      - chroma_data:/app/.chroma_db

  sre-api:
    image: ghcr.io/johnmathews/sre-assistant:latest
    ports:
      - "8000:8000"
    env_file: .env
    volumes:
      - chroma_data:/app/.chroma_db
    depends_on:
      sre-ingest:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

  sre-ui:
    image: ghcr.io/johnmathews/sre-assistant:latest
    command: ["streamlit", "run", "src/ui/app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]
    ports:
      - "8501:8501"
    environment:
      - API_URL=http://sre-api:8000
    depends_on:
      sre-api:
        condition: service_healthy

# Add to your existing volumes section:
volumes:
  chroma_data:
```

Then create a `.env` file alongside your compose file (see [Environment Variables](#environment-variables) below) and
bring the services up:

```bash
docker compose up -d sre-ingest sre-api sre-ui
```

### Environment Variables

Create a `.env` file on the deployment host. See `.env.example` for the full list.

**Required:**

| Variable                         | Example                                | Notes                                      |
| -------------------------------- | -------------------------------------- | ------------------------------------------ |
| `OPENAI_API_KEY`                 | `sk-proj-...`                          | OpenAI API key                             |
| `PROMETHEUS_URL`                 | `http://192.168.2.50:9090`             | Must be reachable from inside the container |
| `GRAFANA_URL`                    | `http://192.168.2.50:3000`             | Must be reachable from inside the container |
| `GRAFANA_SERVICE_ACCOUNT_TOKEN`  | `glsa_...`                             | Grafana service account token              |

**Optional — leave empty or omit to disable the corresponding tools:**

| Variable              | Enables                     |
| --------------------- | --------------------------- |
| `PROXMOX_URL`         | Proxmox VE tools (4 tools)  |
| `PROXMOX_API_TOKEN`   | PVE API auth                |
| `PBS_URL`             | PBS backup tools (3 tools)  |
| `PBS_API_TOKEN`       | PBS API auth                |
| `LOKI_URL`            | Loki log tools (3 tools)    |
| `EXTRA_DOCS_DIRS`     | Additional RAG doc directories (comma-separated absolute paths) |

All URLs must point to addresses reachable from inside the Docker container — see [Networking](#networking).

### Networking

Docker containers on the default bridge network can reach hosts on the LAN. Use LAN IP addresses (e.g.,
`http://192.168.2.50:9090`) in your `.env`, not `localhost` — `localhost` inside a container refers to the
container itself, not the Docker host.

If your compose stack uses a custom network, make sure the SRE services are on one with LAN access. If your
infrastructure services (Prometheus, Grafana, etc.) are also in Docker on the same host, you can put them on a shared
Docker network and use container names as hostnames instead of IPs.

### Vector Store Persistence

The Chroma vector store is stored in the `chroma_data` Docker volume. It persists across container restarts and image
updates.

To rebuild the vector store (e.g., after adding or editing runbooks):

```bash
docker compose run --rm sre-ingest
```

The ingest process reads `.md` files from the bundled `runbooks/` directory (baked into the image) and any directories
listed in `EXTRA_DOCS_DIRS`. It is strictly read-only — it only writes to the Chroma database.

### Updating

Pull the latest image and restart:

```bash
docker compose pull sre-api sre-ui sre-ingest
docker compose up -d sre-api sre-ui
```

If runbooks have changed in the new image, re-run ingest:

```bash
docker compose run --rm sre-ingest
```

### Building from Source

For local development or if you want to modify the image:

```bash
git clone https://github.com/johnmathews/sre-assistant.git
cd sre-assistant
cp .env.example .env
# Edit .env with your values
docker compose up -d
```

The repo's `docker-compose.yml` uses `build: .` instead of `image:` so it builds locally.

---

## CI/CD

A single **GitHub Actions** workflow (`.github/workflows/ci.yml`) handles everything:

1. **Check** — Runs `make check` (lint + typecheck + tests) on every push to `main` and on pull requests.
2. **Build** — On pushes to `main` only: builds the Docker image and pushes to
   `ghcr.io/johnmathews/sre-assistant:latest` (and `:sha-<commit>`). Only runs after check passes.

**Pre-push hook** — Install a local git hook that runs `make check` before allowing pushes:

```bash
make hooks
```

This blocks pushes if lint, typecheck, or tests fail — catching issues before they reach CI.

---

## Motivation & Context

Most AI portfolio projects are chatbots over static documents. This project is different: it connects to live
infrastructure telemetry, reasons about real system state, and applies SRE principles not just as a domain topic but to
its own operation.

The target homelab runs Proxmox with 80+ services across multiple VMs and LXCs, monitored by Prometheus and Grafana,
configured via Ansible, and protected by layered DNS and networking. This is a real environment with real operational
complexity — not a toy setup.

This project exists to demonstrate:

- Practical LangChain agent design with a mix of live tool calls and RAG retrieval
- Deep understanding of SRE concepts (observability, SLIs/SLOs, incident response, infrastructure-as-code)
- Production-mindset AI engineering: evaluation, cost tracking, failure handling, and graceful degradation
- The ability to build AI systems that are themselves reliable and observable

---

## Goals

1. **Show AI engineering depth** — not just "I can call an LLM API" but agent design, tool orchestration, retrieval
   strategy, evaluation, and cost management.
2. **Show SRE fluency** — demonstrate real understanding of observability, alerting, change management, incident
   response, and reliability targets.
3. **Be demo-ready** — the project is demonstrable on demand using the live homelab. Real infrastructure provides
   real signals — alerts, metric patterns, log events — without needing synthetic incidents.
4. **Be honest about trade-offs** — document what works, what doesn't, and what the limitations are. This is more
   impressive than a polished facade.

---

## Architecture

```
Live Sources (LangChain Tools)       Knowledge Base (RAG)
├── Prometheus API                   ├── Runbooks (.md)
├── Grafana Alerting API             ├── Ansible playbooks & inventory
├── Loki API (optional)              └── Past incident summaries
├── Proxmox VE API (optional)               ↓
├── PBS API (optional)               Vector Store (Chroma)
│                                           ↓
└──────────────┬────────────────────────────┘
               ↓
        LangChain Agent
        (routes between tools and retrieval)
               ↓
           LLM (OpenAI API)
               ↓
         FastAPI Backend
               ↓
       CLI / Streamlit UI
```

The key architectural distinction is that **not everything is a RAG problem**. Live telemetry is queried via tool calls
with structured APIs. Static knowledge (runbooks, playbooks) is embedded and retrieved. The agent decides which approach
to use based on the question.

---

## Current Capabilities

The assistant has **up to 16 tools** across 6 categories, depending on which integrations are configured:

**Always available (6 tools):**
- Prometheus — metric search, instant queries, range queries (3 tools)
- Grafana — active alerts, alert rule definitions (2 tools)
- Runbook RAG — semantic search over operational runbooks (1 tool)

**Available when configured (10 tools):**
- Proxmox VE — guest listing, guest config, node status, task history (4 tools, requires `PROXMOX_URL`)
- PBS — datastore status, backup groups, task history (3 tools, requires `PBS_URL`)
- Loki — log queries, label discovery, change correlation timelines (3 tools, requires `LOKI_URL`)

**Questions it can answer today:**
- "Why is CPU high on the Jellyfin VM?"
- "Summarize all active alerts"
- "Is there a runbook for restarting the DNS stack?"
- "What errors appeared in the last hour?"
- "Show me backup status for the PBS datastore"
- "What containers are running on the Proxmox node?"

**Artifacts it can generate:**
- Root cause analysis (RCA) drafts
- Incident summaries
- Suggested remediation steps based on runbooks

---

## Use Cases

### 1. Alert Explainer

Given an active alert, the agent:

1. Fetches the alert details (name, labels, severity, duration)
2. Queries Prometheus for relevant metrics around the alert (CPU, memory, disk — context-dependent)
3. Searches runbooks for matching procedures
4. Produces a plain-English explanation: what's happening, why it likely matters, and what to do about it

**Example:** An alert fires for high memory on a VM. The agent queries memory metrics, sees it spiked after a recent
container restart, finds the runbook for that service, and explains that the service is likely rebuilding its cache
post-restart and should stabilize within 30 minutes.

### 2. Log Correlation

When asked "what changed recently?" or "what happened before this alert?", the agent:

1. Queries Loki for error/warning log spikes around the time of interest
2. Searches for container lifecycle events (restarts, OOM kills, crashes)
3. Correlates these into a chronological timeline grouped by service

This is valuable for answering "did something go wrong before this alert fired?" — a core SRE workflow using log data.

### 3. Infrastructure Inspection

The agent can query Proxmox VE and PBS APIs to answer questions about infrastructure state:

- VM/container inventory and resource allocation
- Node health and resource usage
- Backup status and history
- Recent task outcomes (migrations, backups, restores)

---

## Failure Modes & Handling

| Failure                    | Impact               | Mitigation                                                                                                                   |
| -------------------------- | -------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| LLM API down/timeout       | Agent cannot reason  | Circuit breaker with exponential backoff. Return cached/templated responses for common queries. Clear error message to user. |
| Prometheus unreachable     | No live metrics      | Agent acknowledges gap, falls back to last-known state. Answers from knowledge base only.                                    |
| Alertmanager unreachable   | No alert context     | Agent states it cannot reach alert data, offers to check metrics directly.                                                   |
| Vector store empty/corrupt | No runbook retrieval | Agent proceeds without runbook context, flags that its answer may be less actionable.                                        |
| Token limit exceeded       | Truncated context    | Summarize metrics/logs before passing to LLM. Implement context window budgeting.                                            |

The key principle: **never silently fail**. Every degradation is visible to the user and logged.

---

## Conversation Memory

The agent maintains conversation context within a session so users can have natural follow-up conversations:

- "Why is CPU high on the Jellyfin VM?" → (agent explains)
- "What about memory on the same machine?" → (agent understands "same machine" = Jellyfin VM)
- "Was there a change before that happened?" → (agent correlates with the original alert)

Implementation uses LangChain's built-in message history with a session-scoped conversation buffer.

---

## Tech Stack

| Component       | Technology                   |
| --------------- | ---------------------------- |
| Agent framework | LangChain (Python)           |
| LLM             | OpenAI API (GPT)             |
| Vector store    | Chroma                       |
| Backend         | FastAPI                      |
| Frontend        | Streamlit + CLI              |
| Metrics         | Prometheus                   |
| Dashboards      | Grafana                      |
| Logs            | Loki                         |
| Infrastructure  | Proxmox, Ansible             |
| Alerting        | Grafana unified alerting     |

---

## Roadmap

Features planned for upcoming phases:

### SLI/SLO Dashboard (Phase 4)

Self-instrumentation with Prometheus metrics. The assistant will track its own reliability:

| SLI                          | Target SLO              | How It's Measured                     |
| ---------------------------- | ----------------------- | ------------------------------------- |
| Agent response latency (p95) | < 15 seconds            | Timer around full agent execution     |
| Tool call success rate       | > 99%                   | Success/failure counts per tool       |
| RAG retrieval relevance      | > 80% relevant in top-3 | Manual evaluation + automated scoring |
| End-to-end availability      | > 99.5%                 | Health check endpoint on FastAPI      |
| LLM API error rate           | < 1%                    | HTTP status tracking on API calls     |

These metrics will be exported to Prometheus and visualized in a dedicated Grafana dashboard.

### Cost Awareness (Phase 4)

Per-query tracking of token usage, estimated cost, tool call count, and latency breakdown — surfaced in the UI and
aggregated in the Grafana dashboard.

### Evaluation Framework (Phase 5)

15–20 curated question/expected-answer pairs that validate the agent's reasoning:

| #   | Scenario            | Input                                          | Expected Behavior                                                                           |
| --- | ------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------- |
| 1   | High CPU alert      | "Why is CPU high on node-3?"                   | Queries Prometheus for CPU metrics on node-3, identifies top process, checks recent changes |
| 2   | Disk pressure       | "Is any VM running low on disk?"               | Queries disk usage across all nodes, flags any above 85%                                    |
| 3   | Recent changes      | "What changed in the last 24h?"                | Checks service logs, Prometheus annotations, Alertmanager history                           |
| 4   | Runbook lookup      | "How do I restart the DNS stack?"              | Retrieves relevant runbook via RAG, presents steps                                          |
| 5   | Alert summary       | "Summarize active alerts"                      | Fetches all firing alerts, groups by severity, explains each                                |
| 6   | Correlation         | "Did anything change before this alert fired?" | Cross-references alert start time with log data                                             |
| 7   | Unknown service     | "What's the status of a-nonexistent-service?"  | Gracefully reports no data found, suggests checking the name                                |
| 8   | Ambiguous query     | "Things seem slow"                             | Asks clarifying questions or checks broad performance metrics                               |
| 9   | LLM API failure     | Agent runs with LLM unavailable                | Returns graceful error, suggests manual check                                               |
| 10  | No relevant runbook | "How do I fix error XYZ?"                      | States no runbook found, suggests general troubleshooting steps                             |

### Weekly Reliability Report (Phase 6)

Scheduled summarization of the past week's alerts, changes, and SLO status — output as a markdown report.

---

## Build Order

The project is built incrementally, with each phase producing a working, demonstrable system.

### Phase 1: Alert Explainer (Core Agent)

- Set up LangChain agent with Prometheus and Alertmanager tool definitions
- Implement RAG pipeline over runbooks (yaml files)
- Build FastAPI backend with a single `/ask` endpoint
- Basic CLI or Streamlit interface
- **Deliverable:** Ask the agent about any active alert and get a contextualized explanation

#### Build steps

1. ~~**Project scaffolding** — `pyproject.toml`, `src/` package structure, `Makefile`, `.env.example`~~
2. ~~**Prometheus tool** — `src/agent/tools/prometheus.py`: LangChain tool wrapping Prometheus HTTP API (`/api/v1/query`,
   `/api/v1/query_range`). Unit and integration tests.~~
3. ~~**Grafana alerting tool** — `src/agent/tools/grafana_alerts.py`: fetches active alerts and alert rule definitions
   from Grafana's alerting API (not Alertmanager — Grafana is the actual alerting system in use). Unit and integration
   tests.~~
4. ~~**Runbook RAG pipeline** — 13 runbooks in `runbooks/` converted from homelab documentation, embedding pipeline
   (`src/agent/retrieval/embeddings.py`), retriever tool (`src/agent/retrieval/runbooks.py`), ingest script
   (`make ingest`). Unit tests for chunking, loading, and input validation.~~
5. ~~**Agent assembly** — `src/agent/agent.py`: LangChain agent with all three tools. System prompt defining when to use
   live queries vs. RAG. Conversation memory. Test via REPL.~~
6. ~~**FastAPI backend** — `src/api/main.py`: `POST /ask` (question + session ID → response), `GET /health`.~~
7. ~~**Basic CLI** — Simple input loop calling the agent directly. Streamlit comes later.~~

8. ~~**Proxmox VE tools** — `src/agent/tools/proxmox.py`: 4 tools for VM/container listing, guest config, node status,
   and task history. Conditional registration (only when `PROXMOX_URL` is set). Unit and integration tests.~~
9. ~~**PBS tools** — `src/agent/tools/pbs.py`: 3 tools for datastore status, backup groups, and task history. Conditional
   registration (only when `PBS_URL` is set). Unit and integration tests.~~
10. ~~**Design documentation** — `docs/architecture.md`, `docs/tool-reference.md`, `docs/code-flow.md`,
    `docs/dependencies.md`.~~

**Phase 1 complete.** All build steps finished — the agent has Prometheus tools, Grafana alerting tools, Proxmox VE
tools, PBS tools, runbook RAG, a system prompt with conversation memory, a FastAPI backend (`POST /ask`, `GET /health`),
an interactive CLI, and design documentation.

### Phase 2: Synthetic Incident Generator — _Shelved_

- ~~Build scripts to inject load (CPU stress, disk fill, service kill)~~
- ~~Wire them to trigger real Alertmanager alerts~~
- ~~Create a "demo mode" that runs a synthetic incident and lets the agent investigate~~
- ~~**Deliverable:** On-demand demo that works every time~~

**Shelved.** The live homelab generates enough real incidents and patterns to demo and test the agent's reasoning.
Considered three approaches (mock HTTP scenario server, tool-level interception, separate Prometheus instance) but
decided the complexity isn't justified when real infrastructure provides adequate test signals. May revisit if the
project needs a portable offline demo.

### Phase 3: Loki Log Tools

- Add Loki log query tools for general-purpose log access
- Implement log-based change correlation (error spikes, container lifecycle events)
- **Deliverable:** "What errors appeared recently?" and "What changed before this alert?" answered via Loki

#### Build steps

1. ~~**Config plumbing** — Add `LOKI_URL` to Settings, `.env.example`, and test fixtures~~
2. ~~**`loki_query_logs` tool** — General-purpose LogQL query with relative time parsing, limit handling, and formatted
   output. Unit and integration tests.~~
3. ~~**`loki_list_label_values` tool** — Label discovery for hostnames, services, containers, and log levels. Integration
   tests.~~
4. ~~**`loki_correlate_changes` tool** — Higher-level change correlation: searches for error/warn/fatal spikes and
   container lifecycle events around a reference time, returns a chronological timeline grouped by service. Unit tests
   for timeline building, integration tests with multiple mocked queries.~~
5. ~~**Agent integration** — Loki section in system prompt (labels, LogQL tips, when to use each tool), conditional tool
   registration, health check (`GET /ready`).~~
6. ~~**Documentation** — `runbooks/loki-logging.md` (Alloy pipeline, labels, LogQL reference), updated tool reference,
   updated readme build steps.~~

**Phase 3 complete.** All build steps finished — the agent has 3 Loki tools (query logs, list label values, correlate
changes), conditional registration when `LOKI_URL` is set, a health check, system prompt guidance for LogQL queries,
and documentation. 230 tests passing.

### Phase 4: SLI/SLO Dashboard & Instrumentation

- Instrument agent with Prometheus metrics (latency, tool success rates, token usage)
- Build Grafana dashboard for the assistant's own reliability
- Define and display SLO compliance
- **Deliverable:** A Grafana dashboard showing the AI system's own health

### Phase 5: Evaluation Framework

- Curate 15–20 test cases with expected behaviors
- Build automated evaluation runner (scores tool selection, retrieval relevance, answer quality)
- Integrate as a script that can run on demand or in CI
- **Deliverable:** `make eval` produces a pass/fail report

### Phase 6: Weekly Reliability Report

- Scheduled summarization of the past week's alerts, changes, and SLO status
- Output as a markdown report
- **Deliverable:** Automated weekly report generation

### Deployment

The agent runs as Docker containers on the Infra VM, deployed via the existing
[home-server](https://github.com/johnmathews/home-server) Ansible project. See [Deploying with Docker](#deploying-with-docker)
for container setup and [docs/architecture.md](docs/architecture.md) for the full deployment plan.

Production secrets (API keys, tokens) are managed via Ansible Vault and injected as environment variables
at deploy time — never committed to this repo.

---

## Repository Structure

```
homelab-sre-assistant/
├── Dockerfile                    # Multi-stage build (builder + runtime)
├── docker-compose.yml            # Local dev: 3 services (ingest, api, ui)
├── .dockerignore
├── .github/workflows/
│   └── ci.yml                    # CI (lint/typecheck/test) + Docker build/push
├── Makefile
├── pyproject.toml
├── src/
│   ├── config.py                 # Settings via pydantic-settings
│   ├── cli.py                    # Interactive CLI REPL
│   ├── agent/
│   │   ├── agent.py              # LangChain agent setup
│   │   ├── tools/
│   │   │   ├── prometheus.py     # Prometheus query tools (3)
│   │   │   ├── grafana_alerts.py # Grafana alerting tools (2)
│   │   │   ├── proxmox.py        # Proxmox VE tools (4, optional)
│   │   │   ├── pbs.py            # PBS backup tools (3, optional)
│   │   │   └── loki.py           # Loki log query tools (3, optional)
│   │   └── retrieval/
│   │       ├── embeddings.py     # Document embedding pipeline
│   │       └── runbooks.py       # Runbook RAG retrieval
│   ├── api/
│   │   └── main.py               # FastAPI application
│   └── ui/
│       └── app.py                # Streamlit frontend
├── scripts/
│   ├── ingest_runbooks.py        # Rebuild Chroma vector store
│   └── install-hooks.sh          # Install git pre-push hook
├── tests/                        # Unit + integration tests (230 passing)
├── docs/                         # Design documentation
│   ├── architecture.md           # System overview, data flow, deployment
│   ├── tool-reference.md         # All tools with inputs and examples
│   ├── code-flow.md              # Request lifecycle, tool registration
│   └── dependencies.md           # Python packages, external services
└── runbooks/                     # Operational runbooks (markdown, ingested into RAG)
```

---

## Non-Goals

- This is **not** a general-purpose chatbot. It is purpose-built for homelab SRE.
- This does **not** take automated remediation actions (no auto-restarting services). It advises, it doesn't act.
- This does **not** aim for enterprise-grade multi-tenancy or RBAC. It's a single-user portfolio project.

---

## License

MIT
