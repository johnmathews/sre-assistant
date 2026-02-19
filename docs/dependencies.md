# Dependencies

## Python Packages

### Runtime

| Package | Purpose |
|---------|---------|
| `langchain` | Agent framework — tool orchestration, prompt management, agent graph |
| `langchain-openai` | OpenAI LLM integration for LangChain |
| `langchain-chroma` | Chroma vector store integration for RAG retrieval |
| `fastapi` | HTTP backend — `/ask` and `/health` endpoints |
| `uvicorn` | ASGI server for FastAPI |
| `httpx` | Async HTTP client for all tool API calls (Prometheus, Grafana, Loki, TrueNAS, Proxmox, PBS) |
| `pydantic` | Data validation for tool input schemas and API models |
| `pydantic-settings` | Environment variable loading with validation |
| `python-dotenv` | `.env` file parsing (used by pydantic-settings) |
| `pyyaml` | YAML parsing (runbook frontmatter, eval cases) |
| `streamlit` | Web UI for the agent |
| `prometheus-client` | Self-instrumentation — expose Prometheus metrics at `/metrics` |
| `apscheduler` | Scheduled report generation — `AsyncIOScheduler` with cron triggers |

### Development

| Package | Purpose |
|---------|---------|
| `mypy` | Static type checking (strict mode) |
| `ruff` | Linting and formatting |
| `pytest` | Test framework |
| `pytest-asyncio` | Async test support (all tools are async) |
| `respx` | HTTP mocking for httpx (integration tests, eval framework) |
| `types-PyYAML` | Type stubs for PyYAML |

## LLM Model Selection

The model is configurable via `OPENAI_MODEL` in `.env` (default: `gpt-4o-mini`).

| Model | Speed | Tool Use Quality | Cost (per 1M tokens in/out) | Best For |
|-------|-------|-----------------|----------------------------|----------|
| `gpt-4o-mini` | Fast (~2-5s) | Good — handles most tool routing correctly | ~$0.15 / $0.60 | Day-to-day use, cost-sensitive |
| `gpt-4.1-mini` | Fast (~2-5s) | Good — similar to 4o-mini, newer | ~$0.40 / $1.60 | Budget-friendly upgrade |
| `gpt-4o` | Moderate (~5-10s) | Very good — better PromQL construction | ~$2.50 / $10.00 | Complex queries, multi-step reasoning |
| `gpt-4.1` | Moderate (~5-10s) | Excellent — best at multi-step tool use | ~$2.00 / $8.00 | Debugging, incident investigation |

**Tradeoffs:**
- `gpt-4o-mini` is 15-60x cheaper than the full models and handles straightforward questions well (alert summaries,
  listing VMs, runbook lookups). It struggles with complex PromQL construction and multi-step reasoning.
- `gpt-4o` / `gpt-4.1` produce better PromQL (e.g., correctly using `topk()`, `avg_over_time()`, `by (label)`) and
  handle follow-up questions more reliably, but each query costs significantly more.
- For development and testing, `gpt-4o-mini` is recommended. Switch to a larger model for demos or when query quality
  matters more than cost.

## External Services

### Required

| Service | What it provides | Auth |
|---------|-----------------|------|
| **OpenAI API** | LLM inference (configurable via `OPENAI_MODEL`) | API key (`OPENAI_API_KEY`) |
| **Prometheus** | Metrics storage and PromQL query engine | None (HTTP) |
| **Grafana** | Unified alerting (alert states + rule definitions) | Service account token (`GRAFANA_SERVICE_ACCOUNT_TOKEN`) |

### Optional

| Service | What it provides | Auth |
|---------|-----------------|------|
| **Loki** | Log aggregation, LogQL queries, change correlation | None (HTTP) |
| **TrueNAS SCALE** | ZFS pools, NFS/SMB shares, snapshots, system status, apps | Bearer token (`TRUENAS_API_KEY`) |
| **Proxmox VE** | VM/container config, node status, task history | API token (`PROXMOX_API_TOKEN` as `user@realm!tokenid=secret`) |
| **Proxmox Backup Server** | Backup status, datastore usage, backup tasks | API token (`PBS_API_TOKEN` as `user@realm!tokenid=secret`) |

## Authentication Setup

### Prometheus

No authentication required. Ensure the Prometheus instance is accessible from the machine running the agent.

### Grafana

Create a service account with Viewer role:
1. Grafana > Administration > Service Accounts > Add
2. Create a token
3. Set `GRAFANA_SERVICE_ACCOUNT_TOKEN` to the generated token

### Loki

No authentication required. Logs are collected by Alloy and shipped to Loki. The agent queries Loki's HTTP API
directly. Set `LOKI_URL` to the Loki base URL (e.g. `http://loki:3100`).

### TrueNAS SCALE

Create an API key:
1. TrueNAS web UI > top-right user icon > API Keys > Add
2. Copy the generated key
3. Set `TRUENAS_API_KEY` to the key value (used as `Authorization: Bearer <key>`)

### Proxmox VE

Create an API token:
1. Datacenter > Permissions > API Tokens > Add
2. User: `root@pam` (or a dedicated user with PVEAuditor role)
3. Uncheck "Privilege Separation" for full read access
4. Set `PROXMOX_API_TOKEN` to `user@realm!tokenid=secret-value` (uses `=` separator)

### Proxmox Backup Server

Create an API token:
1. Configuration > Access Control > API Token > Add
2. Set `PBS_API_TOKEN` to `user@realm!tokenid:secret-value` (uses `:` separator — different from PVE)

## TLS Configuration

Proxmox VE, PBS, and TrueNAS SCALE use self-signed certificates by default. The agent skips TLS verification by
default (`PROXMOX_VERIFY_SSL=false`, `PBS_VERIFY_SSL=false`, `TRUENAS_VERIFY_SSL=false`).

To enable verification with a custom CA:
```
PROXMOX_VERIFY_SSL=true
PROXMOX_CA_CERT=/path/to/proxmox-ca.pem
```

To use the system CA bundle (e.g., if you've added the Proxmox CA to your trust store):
```
PROXMOX_VERIFY_SSL=true
PROXMOX_CA_CERT=
```
