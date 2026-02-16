# Code Flow

## Request Lifecycle

### 1. HTTP Request

A user question arrives via one of three interfaces:

- **FastAPI** — `POST /ask` with JSON body `{"question": "...", "session_id": "..."}` (`src/api/main.py`)
- **Streamlit UI** — chat input sends to FastAPI backend (`src/ui/streamlit_app.py`)
- **CLI** — interactive REPL calls the agent directly (`src/cli.py`)

### 2. Agent Invocation

```
src/api/main.py::ask()
  -> invoke_agent(agent, message, session_id)
    -> agent.ainvoke({"messages": [HumanMessage(content=message)]}, config)
```

The agent is built once at startup via `build_agent()` and stored in `app.state.agent`. Each request passes through
`invoke_agent()` which wraps the LangGraph `ainvoke` call with a session-scoped config for conversation memory.

### 3. Tool Selection

LangGraph's `create_agent` compiles the LLM + tools into a graph. The LLM:

1. Reads the system prompt (which includes the tool selection guide)
2. Examines the user's question
3. Decides which tool(s) to call (or responds directly)
4. Calls tools, reads results, and reasons about the response

### 4. Tool Execution

Each tool follows the same pattern:

```python
@tool("tool_name", args_schema=InputSchema)
async def tool_name(param: str) -> str:
    # 1. Guard: check if service is configured
    # 2. Build HTTP request (URL, headers, params)
    # 3. Make async httpx request with timeout
    # 4. Handle errors (ConnectError, Timeout, HTTPStatusError)
    # 5. Format response data into readable string
    # 6. Return formatted string to LLM
```

### 5. Response Formatting

Tool results are formatted into human-readable strings (not raw JSON) so the LLM can reason about them effectively.
The LLM composes a final answer from tool results and its knowledge.

### 6. Response Return

```
agent.ainvoke() returns {"messages": [...]}
  -> invoke_agent extracts last AIMessage.content
    -> FastAPI returns AskResponse(response=..., session_id=...)
```

## Tool Registration

Tools are registered in `src/agent/agent.py::_get_tools()`:

```
Always included:
  - prometheus_search_metrics, prometheus_instant_query, prometheus_range_query
  - grafana_get_alerts, grafana_get_alert_rules

Conditional (config-dependent):
  - proxmox_* tools  (if PROXMOX_URL is set)
  - pbs_* tools      (if PBS_URL is set)
  - runbook_search   (if vector store directory exists)
```

## Settings Loading

`src/config.py::Settings` uses `pydantic-settings` to load from environment variables and `.env`:

```
get_settings() -> Settings()  [cached via @lru_cache]
  1. Reads .env file
  2. Validates required fields (openai_api_key, prometheus_url, grafana_url, grafana_service_account_token)
  3. Optional fields default to empty string (proxmox_url, pbs_url, etc.)
  4. Returns singleton Settings instance
```

Each tool module imports `get_settings` independently. In tests, `conftest.py::mock_settings` patches `get_settings`
at every import site.

## Health Check Flow

`GET /health` checks each dependency:

1. Prometheus — `GET /-/healthy`
2. Grafana — `GET /api/health` with auth header
3. Proxmox VE (if configured) — `GET /api2/json/version` with API token
4. PBS (if configured) — `GET /api2/json/version` with API token
5. Vector store — checks if `chroma_db/` directory exists

Returns overall status: "healthy" (all OK), "degraded" (some failing), "unhealthy" (all failing).
