# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HomeLab SRE Assistant — an AI-powered SRE assistant for homelab infrastructure built with LangChain (Python). It connects
to live infrastructure telemetry (Prometheus, Alertmanager, Loki) and a RAG knowledge base (runbooks, Ansible playbooks,
past incidents) to answer operational questions, explain alerts, correlate changes, and generate incident reports.

Target environment: Proxmox homelab with 80+ services across multiple VMs and LXCs.

## Key Architectural Decision

**Not everything is RAG.** The agent uses two distinct data access patterns:

- **Live tool calls** (LangChain tools): Prometheus metrics, Alertmanager alerts, Loki logs — queried in real-time via
  structured APIs
- **RAG retrieval** (vector store): Runbooks, Ansible playbooks/inventory, past incident summaries — embedded in
  Chroma/FAISS

The LangChain agent decides which approach to use based on the question.

## Tech Stack

- **Agent framework:** LangChain (Python)
- **LLM:** Claude API (Anthropic)
- **Vector store:** Chroma or FAISS
- **Backend:** FastAPI (`/ask` endpoint)
- **Frontend:** Streamlit (MVP) or CLI
- **Observability:** Prometheus metrics, Grafana dashboards, Loki logs
- **Infrastructure:** Proxmox, Ansible, Alertmanager

## Development Conventions

- **Package manager:** uv (not pip, not poetry)
- **Python version:** latest stable with long-term support
- **All Python code must be fully typed** — type annotations on all function signatures, return types, and variables where not obvious. Use `mypy` for verification.

## Builder Ownership Rule

**Before writing any code for a new phase or major component, ask the user 2-3 questions to verify they understand what is being built and why.** This is non-negotiable. The user must be able to explain every architectural decision and implementation choice — this is a portfolio project where understanding IS the deliverable. If their answers reveal gaps, ask follow-up questions until understanding is confirmed. Do not proceed with implementation until the user demonstrates clear ownership of the design.

This applies to: starting a new build phase, introducing a new tool/integration, making significant architectural changes, or adding a new dependency. It does not apply to small bug fixes or minor refactoring within already-understood code.

## Phase 1 Build Plan: Alert Explainer

Build in this order (each step depends on the previous):

1. **Project scaffolding** — `pyproject.toml` (langchain, langchain-anthropic, fastapi, uvicorn, chromadb, pyyaml), `src/` package structure, `Makefile`, `.env.example` for API keys and endpoint URLs
2. **Prometheus tool** — `src/agent/tools/prometheus.py`: LangChain tool wrapping Prometheus HTTP API (`/api/v1/query`, `/api/v1/query_range`). Test standalone against the real instance before wiring into the agent.
3. **Alertmanager tool** — `src/agent/tools/alertmanager.py`: fetches active alerts from `/api/v2/alerts`, parses labels/annotations/severity. Simpler than Prometheus (just GET with optional filters).
4. **Runbook RAG pipeline** — Sample runbooks in `runbooks/`, embedding pipeline (`src/agent/retrieval/embeddings.py`), retriever tool (`src/agent/retrieval/runbooks.py`), ingest script to rebuild the vector store on demand.
5. **Agent assembly** — `src/agent/agent.py`: LangChain agent with the three tools. System prompt defining when to use live queries vs. RAG. `src/agent/memory.py` for session-scoped conversation buffer. Test via REPL first.
6. **FastAPI backend** — `src/api/main.py`: `POST /ask` (question + session ID → agent response), `GET /health`. Thin glue layer.
7. **Basic CLI** — Simple input loop calling the agent directly (skip HTTP for local use). Streamlit comes later.

## Commands

```bash
make dev           # Install all dependencies (including dev tools)
make lint          # Ruff check + format check
make format        # Auto-fix lint and formatting
make typecheck     # mypy strict mode
make test          # Unit + integration tests (mocked HTTP, no secrets needed)
make test-e2e      # E2E tests against real services (needs .env, costs money)
make check         # lint + typecheck + test in one command
make serve         # FastAPI dev server on :8000
uv run pytest tests/test_foo.py::test_bar  # Run a single test
```

## Testing Strategy

Three test tiers:
- **Unit tests** (`tests/test_*.py`) — pure functions (parsing, validation, formatting). No mocks, no IO.
- **Integration tests** (`tests/test_*_integration.py`, `@pytest.mark.integration`) — test tool functions with mocked HTTP via `respx`. Verify API calls, response handling, and error paths without real services.
- **E2E tests** (`@pytest.mark.e2e`) — hit real Prometheus/Alertmanager/LLM. Skipped by default, run with `--run-e2e`. Requires `.env`.

`make test` runs unit + integration (safe for CI). `make test-e2e` runs everything.

The `mock_settings` fixture in `tests/conftest.py` provides fake config so tests never need a `.env` file. When adding new modules that import `get_settings`, add a corresponding patch to this fixture.

## User Shorthand

- **DCP** = update/create documentation, commit changes, push to remote
- **MDCP** = update/add documentation, commit changes, merge to main, push to remote

## Build Phases

The project is built incrementally. Each phase produces a working, demonstrable system:

1. **Alert Explainer** — Core LangChain agent + Prometheus/Alertmanager tools + runbook RAG + FastAPI + basic UI
2. **Synthetic Incident Generator** — Scripts to inject load/faults, trigger real alerts, enable on-demand demos
3. **Change Correlation** — Ansible log ingestion, timeline correlation between changes and alerts
4. **SLI/SLO Dashboard** — Self-instrumentation with Prometheus metrics, Grafana dashboard for the assistant's own
   reliability
5. **Evaluation Framework** — Automated test cases scoring tool selection, retrieval relevance, answer quality
6. **Weekly Reliability Report** — Scheduled summarization of alerts, changes, SLO status

## Planned Source Layout

- `src/agent/` — LangChain agent setup, tools (prometheus, alertmanager, loki, ansible), retrieval (embeddings, runbooks,
  ansible), memory
- `src/api/` — FastAPI application
- `src/ui/` — Streamlit frontend
- `src/eval/` — Evaluation test cases (YAML) and runner
- `src/incidents/` — Synthetic incident generator and scenarios
- `src/observability/` — Prometheus metric exports, cost/token tracking
- `runbooks/` — Operational runbooks (markdown)
- `ansible/` — Symlink/submodule to ansible home-server project
- `dashboards/` — Grafana dashboard JSON exports

## Secrets

This is a **public repository**. Never commit secrets, API keys, tokens, or internal hostnames/IPs. Secrets are loaded at runtime from `.env` (gitignored). The `.env.example` file documents required variables without real values. For production/deployment, secrets should be managed via environment variables or a secrets manager (the homelab infra uses Ansible Vault).

## Design Principles

- **Never silently fail.** Every degradation is visible to the user and logged. Each tool has explicit failure handling
  with graceful fallback.
- **Self-observability.** The assistant tracks its own SLIs: response latency (p95 < 15s), tool call success rate (>
  99%), RAG relevance (> 80% top-3), availability (> 99.5%), LLM error rate (< 1%).
- **Cost awareness.** Every query tracks token usage, estimated cost, tool call count, and latency breakdown.
- **Advisory only.** The assistant advises but never takes automated remediation actions.
