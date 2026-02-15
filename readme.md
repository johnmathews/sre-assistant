# HomeLab SRE Assistant

An AI-powered Site Reliability Engineering assistant for homelab infrastructure, built with LangChain. It ingests real
telemetry, runbooks, and infrastructure-as-code to answer operational questions, explain alerts, correlate changes, and
generate incident reports — all while treating itself as a production service with its own SLIs, SLOs, and failure
handling.

---

## Table of Contents

- [Getting Started](#getting-started)
  - [macOS Tahoe / Sequoia: Local Network Access](#macos-tahoe--sequoia-local-network-access)
- [Motivation \& Context](#motivation--context)
- [Goals](#goals)
- [What It Should Achieve](#what-it-should-achieve)
- [Architecture](#architecture)
- [Use Cases](#use-cases)
- [SLIs/SLOs About Itself](#slisslos-about-itself)
- [Failure Modes \& Handling](#failure-modes--handling)
- [Evaluation Framework](#evaluation-framework)
- [Conversation Memory](#conversation-memory)
- [Cost Awareness](#cost-awareness)
- [Tech Stack](#tech-stack)
- [Build Order](#build-order)
  - [Phase 1: Alert Explainer (Core Agent)](#phase-1-alert-explainer-core-agent)
  - [Phase 2: Synthetic Incident Generator](#phase-2-synthetic-incident-generator)
  - [Phase 3: Change Correlation](#phase-3-change-correlation)
  - [Phase 4: SLI/SLO Dashboard \& Instrumentation](#phase-4-slislo-dashboard--instrumentation)
  - [Phase 5: Evaluation Framework](#phase-5-evaluation-framework)
  - [Phase 6: Weekly Reliability Report](#phase-6-weekly-reliability-report)
- [Repository Structure (Planned)](#repository-structure-planned)
- [Non-Goals](#non-goals)
- [License](#license)

---

## Getting Started

```bash
# Install dependencies
make dev

# Copy and fill in your API keys
cp .env.example .env
# Edit .env with your OPENAI_API_KEY, PROMETHEUS_URL, GRAFANA_URL, GRAFANA_SERVICE_ACCOUNT_TOKEN

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

On macOS 15+ (Sequoia) and macOS 26+ (Tahoe), Apple restricts local network access for
processes that aren't children of a recognized terminal app. This affects `make chat` if you
run it inside **tmux** — the agent's Prometheus/Grafana tool calls will fail with
`[Errno 65] No route to host` because tmux runs as a daemon under `launchd`, breaking the
terminal's local network exemption.

**Workaround:** Run `make chat` directly in your terminal (kitty, iTerm, Terminal.app) without
tmux. Apple-signed binaries (`/usr/bin/curl`, `/usr/bin/python3`) are exempt and always work,
but Python installed via uv, pyenv, or Homebrew is not Apple-signed and inherits permissions
from the parent process chain.

This only affects local development on macOS. The agent runs without restrictions when deployed
in Docker on Linux.

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
3. **Be demo-ready** — the project must be demonstrable on demand without relying on something being broken in the
   homelab. Synthetic incident generation solves this.
4. **Be honest about trade-offs** — document what works, what doesn't, and what the limitations are. This is more
   impressive than a polished facade.

---

## What It Should Achieve

### Core Capabilities

The assistant ingests data from two categories of sources and uses them in fundamentally different ways:

**Live sources (queried in real-time via LangChain tools):**

- Prometheus metrics (CPU, memory, disk, network, custom metrics)
- Alertmanager alerts (active, silenced, inhibited)
- Logs (via Loki or direct log access)

**Knowledge base (embedded and retrieved via RAG):**

- Runbooks (markdown documentation for operational procedures)
- Ansible playbooks and inventory (infrastructure-as-code)
- Past incident summaries (generated by the system itself over time)

### Questions It Can Answer

- "Why is CPU high on the Jellyfin VM?"
- "What changed in the last 24 hours?"
- "Summarize all active alerts"
- "Is there a runbook for restarting the DNS stack?"
- "Which Ansible role manages the Prometheus configuration?"

### Artifacts It Can Generate

- Root cause analysis (RCA) drafts
- Incident summaries
- Suggested remediation steps based on runbooks and historical context
- Weekly reliability reports

---

## Architecture

```
Live Sources (LangChain Tools)       Knowledge Base (RAG)
├── Prometheus API                   ├── Runbooks (.md)
├── Alertmanager API                 ├── Ansible playbooks & inventory
├── Loki / Log API                   └── Past incident summaries
│                                           ↓
│                                    Vector Store (Chroma / FAISS)
│                                           ↓
└──────────────┬────────────────────────────┘
               ↓
        LangChain Agent
        (routes between tools and retrieval)
               ↓
           LLM (Claude API)
               ↓
         FastAPI Backend
               ↓
       CLI / Streamlit UI
```

The key architectural distinction is that **not everything is a RAG problem**. Live telemetry is queried via tool calls
with structured APIs. Static knowledge (runbooks, playbooks) is embedded and retrieved. The agent decides which approach
to use based on the question.

---

## Use Cases

### 1. Alert Explainer (Primary)

Given an active alert from Alertmanager, the agent:

1. Fetches the alert details (name, labels, severity, duration)
2. Queries Prometheus for relevant metrics around the alert (CPU, memory, disk — context-dependent)
3. Searches runbooks for matching procedures
4. Produces a plain-English explanation: what's happening, why it likely matters, and what to do about it

**Example:** An alert fires for high memory on a VM. The agent queries memory metrics, sees it spiked after a recent
container restart, finds the runbook for that service, and explains that the service is likely rebuilding its cache
post-restart and should stabilize within 30 minutes.

### 2. Change Correlation

When asked "what changed recently?", the agent:

1. Queries Prometheus for annotation markers and metric shifts
2. Checks Ansible run logs for recent playbook executions
3. Checks Alertmanager for alert state transitions
4. Correlates these into a timeline of changes

This is valuable for answering "did a recent change cause this alert?" — a core SRE workflow.

### 3. Weekly Reliability Report

A scheduled job (or on-demand request) that:

1. Summarizes alert frequency and duration over the past week
2. Highlights any SLO breaches
3. Notes significant changes or deployments
4. Produces a markdown report

Lower priority — technically straightforward but shows operational maturity.

### 4. Synthetic Incident Generator

**Critical for demos.** This component can:

1. Inject artificial load or metric anomalies into the homelab (e.g., CPU stress test, fill a disk, kill a service)
2. Trigger real alerts through Alertmanager
3. Let the SRE assistant investigate and explain the "incident" live

Without this, every demo depends on something coincidentally being broken. With it, the project is always demo-ready.

---

## SLIs/SLOs About Itself

The assistant treats itself as a production service. It tracks and dashboards:

| SLI                          | Target SLO              | How It's Measured                     |
| ---------------------------- | ----------------------- | ------------------------------------- |
| Agent response latency (p95) | < 15 seconds            | Timer around full agent execution     |
| Tool call success rate       | > 99%                   | Success/failure counts per tool       |
| RAG retrieval relevance      | > 80% relevant in top-3 | Manual evaluation + automated scoring |
| End-to-end availability      | > 99.5%                 | Health check endpoint on FastAPI      |
| LLM API error rate           | < 1%                    | HTTP status tracking on API calls     |

These metrics are exported to Prometheus and visualized in a dedicated Grafana dashboard.

---

## Failure Modes & Handling

| Failure                    | Impact               | Mitigation                                                                                                                   |
| -------------------------- | -------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| LLM API down/timeout       | Agent cannot reason  | Circuit breaker with exponential backoff. Return cached/templated responses for common queries. Clear error message to user. |
| Prometheus unreachable     | No live metrics      | Agent acknowledges gap, falls back to last-known state. Answers from knowledge base only.                                    |
| Alertmanager unreachable   | No alert context     | Agent states it cannot reach alert data, offers to check metrics directly.                                                   |
| Vector store empty/corrupt | No runbook retrieval | Agent proceeds without runbook context, flags that its answer may be less actionable.                                        |
| Token limit exceeded       | Truncated context    | Summarize metrics/logs before passing to LLM. Implement context window budgeting.                                            |

The key principle: **never silently fail**. Every degradation should be visible to the user and logged.

---

## Evaluation Framework

A set of 15–20 curated question/expected-answer pairs that validate the agent's reasoning. These serve both as regression
tests and as demo material.

**Example evaluation cases:**

| #   | Scenario            | Input                                          | Expected Behavior                                                                           |
| --- | ------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------- |
| 1   | High CPU alert      | "Why is CPU high on node-3?"                   | Queries Prometheus for CPU metrics on node-3, identifies top process, checks recent changes |
| 2   | Disk pressure       | "Is any VM running low on disk?"               | Queries disk usage across all nodes, flags any above 85%                                    |
| 3   | Recent changes      | "What changed in the last 24h?"                | Checks service logs, Prometheus annotations, Alertmanager history                           |
| 4   | Runbook lookup      | "How do I restart the DNS stack?"              | Retrieves relevant runbook via RAG, presents steps                                          |
| 5   | Alert summary       | "Summarize active alerts"                      | Fetches all firing alerts, groups by severity, explains each                                |
| 6   | Correlation         | "Did anything change before this alert fired?" | Cross-references alert start time with change log                                           |
| 7   | Unknown service     | "What's the status of a-nonexistent-service?"  | Gracefully reports no data found, suggests checking the name                                |
| 8   | Ambiguous query     | "Things seem slow"                             | Asks clarifying questions or checks broad performance metrics                               |
| 9   | LLM API failure     | Agent runs with LLM unavailable                | Returns graceful error, suggests manual check                                               |
| 10  | No relevant runbook | "How do I fix error XYZ?"                      | States no runbook found, suggests general troubleshooting steps                             |
| 11  | Playbook lookup     | "How do I setup a new LXC on my homelab?"      | Retrieves relevant Ansible playbook and tasks for LXC provisioning via RAG, presents steps  |

The evaluation framework runs these cases, scores the responses (correct tool calls, relevant retrieval, accurate
answer), and produces a pass/fail report. This can be run as CI or on-demand.

---

## Conversation Memory

The agent maintains conversation context within a session so users can have natural follow-up conversations:

- "Why is CPU high on the Jellyfin VM?" → (agent explains)
- "What about memory on the same machine?" → (agent understands "same machine" = Jellyfin VM)
- "Was there a change before that happened?" → (agent correlates with the original alert)

Implementation uses LangChain's built-in message history with a session-scoped conversation buffer. Long conversations
are summarized to stay within token limits.

---

## Cost Awareness

Every query tracks and displays:

- **Token usage**: input tokens, output tokens, total
- **Estimated cost**: based on the model's pricing
- **Tool call count**: how many external API calls the agent made
- **Latency breakdown**: time spent in LLM vs. tool calls vs. retrieval

This is surfaced in the UI per query and aggregated in the Grafana dashboard. It demonstrates awareness of the unit
economics that AI startups care deeply about.

---

## Tech Stack

| Component       | Technology                   |
| --------------- | ---------------------------- |
| Agent framework | LangChain (Python)           |
| LLM             | Claude API (Anthropic)       |
| Vector store    | Chroma or FAISS              |
| Backend         | FastAPI                      |
| Frontend        | Streamlit (MVP) or CLI       |
| Metrics         | Prometheus                   |
| Dashboards      | Grafana                      |
| Infrastructure  | Proxmox, Ansible             |
| Logs            | Loki (or direct file access) |
| Alerting        | Alertmanager                 |

---

## Build Order

The project is built incrementally, with each phase producing a working, demonstrable system:

### Phase 1: Alert Explainer (Core Agent)

- Set up LangChain agent with Prometheus and Alertmanager tool definitions
- Implement RAG pipeline over runbooks (yaml files)
- Build FastAPI backend with a single `/ask` endpoint
- Basic CLI or Streamlit interface
- **Deliverable:** Ask the agent about any active alert and get a contextualized explanation

#### Build steps

1. ~~**Project scaffolding** — `pyproject.toml`, `src/` package structure, `Makefile`, `.env.example`~~
2. ~~**Prometheus tool** — `src/agent/tools/prometheus.py`: LangChain tool wrapping Prometheus HTTP API (`/api/v1/query`, `/api/v1/query_range`). Unit and integration tests.~~
3. ~~**Grafana alerting tool** — `src/agent/tools/grafana_alerts.py`: fetches active alerts and alert rule definitions from Grafana's alerting API (not Alertmanager — Grafana is the actual alerting system in use). Unit and integration tests.~~
4. ~~**Runbook RAG pipeline** — 13 runbooks in `runbooks/` converted from homelab documentation, embedding pipeline (`src/agent/retrieval/embeddings.py`), retriever tool (`src/agent/retrieval/runbooks.py`), ingest script (`make ingest`). Unit tests for chunking, loading, and input validation.~~
5. ~~**Agent assembly** — `src/agent/agent.py`: LangChain agent with all three tools. System prompt defining when to use live queries vs. RAG. Conversation memory. Test via REPL.~~
6. ~~**FastAPI backend** — `src/api/main.py`: `POST /ask` (question + session ID → response), `GET /health`.~~
7. ~~**Basic CLI** — Simple input loop calling the agent directly. Streamlit comes later.~~

**Phase 1 complete.** All build steps finished — the agent has Prometheus tools, Grafana alerting tools, runbook RAG, a system prompt with conversation memory, a FastAPI backend (`POST /ask`, `GET /health`), and an interactive CLI. 94 tests passing.

### Phase 2: Synthetic Incident Generator

- Build scripts to inject load (CPU stress, disk fill, service kill)
- Wire them to trigger real Alertmanager alerts
- Create a "demo mode" that runs a synthetic incident and lets the agent investigate
- **Deliverable:** On-demand demo that works every time

### Phase 3: Change Correlation

- Add Ansible log ingestion as a tool (recent playbook runs, changed tasks)
- Implement timeline correlation between changes and alert state transitions
- **Deliverable:** "What changed before this alert?" produces a correlated timeline

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

---

## Repository Structure (Planned)

```
homelab-sre-assistant/
├── README.md
├── pyproject.toml
├── src/
│   ├── agent/
│   │   ├── agent.py              # LangChain agent setup
│   │   ├── tools/
│   │   │   ├── prometheus.py     # Prometheus query tool
│   │   │   ├── alertmanager.py   # Alertmanager query tool
│   │   │   ├── loki.py           # Log query tool
│   │   │   └── ansible.py        # Ansible log tool
│   │   ├── retrieval/
│   │   │   ├── embeddings.py     # Document embedding pipeline
│   │   │   ├── ansible.py        # Ansible configuration retrieval
│   │   │   └── runbooks.py       # Runbook RAG retrieval
│   │   └── memory.py             # Conversation memory management
│   ├── api/
│   │   └── main.py               # FastAPI application
│   ├── ui/
│   │   └── streamlit_app.py      # Streamlit frontend
│   ├── eval/
│   │   ├── cases.yaml            # Evaluation test cases
│   │   └── runner.py             # Evaluation execution
│   ├── incidents/
│   │   ├── generator.py          # Synthetic incident injection
│   │   └── scenarios/            # Predefined incident scenarios
│   └── observability/
│       ├── metrics.py            # Prometheus metric exports
│       └── cost_tracker.py       # Token usage and cost tracking
├── runbooks/                     # Operational runbooks (markdown)
├── ansible/                      # Symlink or submodule to ansible home-server project
├── dashboards/                   # Grafana dashboard JSON exports
├── docker-compose.yml
└── Makefile
```

---

## Non-Goals

- This is **not** a general-purpose chatbot. It is purpose-built for homelab SRE.
- This does **not** take automated remediation actions (no auto-restarting services). It advises, it doesn't act.
- This does **not** aim for enterprise-grade multi-tenancy or RBAC. It's a single-user portfolio project.

---

## License

MIT
