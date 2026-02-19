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
  - truenas_* tools  (if TRUENAS_URL is set)
  - loki_* tools     (if LOKI_URL is set)
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
  3. Optional fields default to empty string (truenas_url, loki_url, proxmox_url, pbs_url, etc.)
  4. Returns singleton Settings instance
```

Each tool module imports `get_settings` independently. In tests, `conftest.py::mock_settings` patches `get_settings`
at every import site.

## Metrics Flow

Self-instrumentation metrics are collected at two levels:

### Request level (FastAPI)

```
POST /ask
  -> REQUESTS_IN_PROGRESS.inc()
  -> start = time.monotonic()
  -> invoke_agent(...)
  -> REQUEST_DURATION.observe(elapsed)
  -> REQUESTS_TOTAL.labels(status="success"|"error").inc()
  -> REQUESTS_IN_PROGRESS.dec()  (in finally block — always runs)
```

On error, both the error counter and duration histogram are recorded _before_ the HTTPException is raised, so failed
requests are fully instrumented.

### Agent level (LangChain callback handler)

The `MetricsCallbackHandler` (`src/observability/callbacks.py`) captures tool-call and LLM metrics from _inside_
LangGraph's execution loop.

#### Why callbacks instead of decorating tools?

LangGraph runs an internal agentic loop: the LLM decides to call a tool → the tool runs → the result is fed back →
the LLM decides whether to call another tool or respond. This loop is invisible to FastAPI middleware, which only sees
the outer HTTP request. Three alternative approaches were considered:

1. **Decorating each tool function** — requires modifying every `@tool` definition and remembering to decorate new
   tools. Fragile and repetitive.
2. **FastAPI middleware** — can time the overall request but cannot see individual tool calls or LLM invocations inside
   the agent loop.
3. **LangChain callbacks** (chosen) — `BaseCallbackHandler` is invoked automatically by LangGraph at each lifecycle
   event. Zero changes to tool code. New tools are automatically instrumented. Works inside the agent's internal loop.

#### Lifecycle

A fresh `MetricsCallbackHandler` instance is created per request in `invoke_agent()` and injected via
`config["callbacks"]`. The handler instance is request-scoped (its `_start_times` dict is private), but all
counter/histogram writes target the module-level Prometheus singletons in `metrics.py`.

```
invoke_agent(agent, message, session_id)
  -> metrics_cb = MetricsCallbackHandler()
  -> config = {"configurable": {"thread_id": session_id}, "callbacks": [metrics_cb]}
  -> agent.ainvoke({"messages": [...]}, config)
```

During execution, LangGraph calls the handler at these points:

**Tool lifecycle:**
```
on_tool_start(serialized, input_str, run_id)
  -> stores run_id → (time.monotonic(), tool_name) in _start_times dict

on_tool_end(output, run_id)
  -> pops run_id from _start_times
  -> duration = now - start_time
  -> TOOL_CALL_DURATION.labels(tool_name).observe(duration)
  -> TOOL_CALLS_TOTAL.labels(tool_name, status="success").inc()

on_tool_error(error, run_id)
  -> pops run_id from _start_times
  -> TOOL_CALL_DURATION.labels(tool_name).observe(duration)
  -> TOOL_CALLS_TOTAL.labels(tool_name, status="error").inc()
```

**LLM lifecycle:**
```
on_llm_end(response, run_id)
  -> LLM_CALLS_TOTAL.labels(status="success").inc()
  -> extracts token_usage from response.llm_output (if present)
  -> LLM_TOKEN_USAGE.labels(type="prompt").inc(prompt_tokens)
  -> LLM_TOKEN_USAGE.labels(type="completion").inc(completion_tokens)
  -> looks up model pricing (falls back to gpt-4o rates for unknown models)
  -> LLM_ESTIMATED_COST.inc(calculated_cost)

on_llm_error(error, run_id)
  -> LLM_CALLS_TOTAL.labels(status="error").inc()
```

#### Error resilience

Every callback method is wrapped in `try/except`. Metrics collection must never crash a request — if a callback fails
(e.g., unexpected `llm_output` format, missing `token_usage` key), it logs at DEBUG level and continues. This is
critical because `llm_output` format varies across LLM providers and can change between library versions.

#### Cost estimation

The handler matches `model_name` from `llm_output` against a prefix-based pricing table:

| Model prefix | Prompt (per 1M tokens) | Completion (per 1M tokens) |
|-------------|------------------------|---------------------------|
| `gpt-4o-mini` | $0.15 | $0.60 |
| `gpt-4o` | $2.50 | $10.00 |
| `gpt-4-turbo` | $10.00 | $30.00 |

Unknown models fall back to `gpt-4o` pricing (the conservative default). The total cost counter is monotonically
increasing — Prometheus `rate()` computes cost per time window for the dashboard.

### Exposition

`GET /metrics` returns all metrics in Prometheus exposition format via `prometheus_client.generate_latest()`.

### Health gauge updates

`GET /health` updates the `sre_assistant_component_healthy` gauge for each component after checking its status.
This means the gauge reflects the last health check result. Prometheus scrapes `/metrics` on its own schedule,
so the gauge value between health checks remains at the last-known state.

## Health Check Flow

`GET /health` checks each dependency:

1. Prometheus — `GET /-/healthy`
2. Grafana — `GET /api/health` with auth header
3. Loki (if configured) — `GET /ready`
4. TrueNAS (if configured) — `GET /api/v2.0/core/ping` with Bearer token
5. Proxmox VE (if configured) — `GET /api2/json/version` with API token
6. PBS (if configured) — `GET /api2/json/version` with API token
7. Vector store — checks if `chroma_db/` directory exists

Returns overall status: "healthy" (all OK), "degraded" (some failing), "unhealthy" (all failing).
