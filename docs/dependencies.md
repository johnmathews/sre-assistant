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
| `httpx` | Async HTTP client for all tool API calls (Prometheus, Grafana, Proxmox, PBS) |
| `pydantic` | Data validation for tool input schemas and API models |
| `pydantic-settings` | Environment variable loading with validation |
| `python-dotenv` | `.env` file parsing (used by pydantic-settings) |
| `pyyaml` | YAML parsing (runbook frontmatter) |
| `streamlit` | Web UI for the agent |

### Development

| Package | Purpose |
|---------|---------|
| `mypy` | Static type checking (strict mode) |
| `ruff` | Linting and formatting |
| `pytest` | Test framework |
| `pytest-asyncio` | Async test support (all tools are async) |
| `respx` | HTTP mocking for httpx (integration tests) |

## External Services

### Required

| Service | What it provides | Auth |
|---------|-----------------|------|
| **OpenAI API** | LLM inference (GPT-4o-mini default) | API key (`OPENAI_API_KEY`) |
| **Prometheus** | Metrics storage and PromQL query engine | None (HTTP) |
| **Grafana** | Unified alerting (alert states + rule definitions) | Service account token (`GRAFANA_SERVICE_ACCOUNT_TOKEN`) |

### Optional

| Service | What it provides | Auth |
|---------|-----------------|------|
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

Both Proxmox VE and PBS use self-signed certificates by default. The agent skips TLS verification by default
(`PROXMOX_VERIFY_SSL=false`, `PBS_VERIFY_SSL=false`).

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
