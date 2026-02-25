# Phase 7: Agent Memory Store — Design Document

## Problem Statement

The SRE assistant has no structured persistent memory. Every session starts from zero context.
Reports are generated from scratch with no awareness of previous reports. When the agent
investigates an alert and finds a root cause, that knowledge is lost. There is no way to
answer "has this happened before?" or "what did last week's report say?"

The conversation history files exist on disk but are flat JSON — not queryable, not indexed,
not usable by the agent.

## Aims

1. Give the agent persistent, queryable memory that survives restarts and accumulates value
   over time.
2. Enable the weekly report to reference previous reports — track whether recommendations
   were addressed, flag recurring issues, show trends beyond raw metrics.
3. Build an incident journal so the agent can correlate new alerts with past root causes.
4. Establish metric baselines so the agent can distinguish "normal" from "anomalous."

## Non-Goals

- No multi-user access control (single homelab, single user).
- No real-time event streaming — memory is written at natural checkpoints (report generation,
  incident recording), not on every metric scrape.
- No automated remediation — the agent records and advises, never acts.
- No migration from conversation history files — they serve a different purpose (audit trail).

## Architecture

### Storage: SQLite

**Why SQLite:**
- Zero infrastructure — no new service to deploy or maintain.
- File-based — volume-mountable in Docker, same pattern as conversation history.
- Sufficient for single-instance homelab workload (hundreds of records, not millions).
- Full SQL for querying — incidents by service, reports by date range, baselines by metric.
- Python stdlib `sqlite3` — no new dependency.

**Location:** Configurable via `MEMORY_DB_PATH` env var. Default in Docker: `/app/memory.db`.
Empty string = memory disabled (graceful degradation, same as all optional features).

### Schema

```sql
-- Archived weekly reports
CREATE TABLE reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at    TEXT NOT NULL,        -- ISO 8601
    lookback_days   INTEGER NOT NULL,
    report_markdown TEXT NOT NULL,        -- full formatted report text
    report_data     TEXT NOT NULL,        -- JSON-serialized ReportData
    active_alerts   INTEGER DEFAULT 0,
    slo_failures    INTEGER DEFAULT 0,
    total_log_errors INTEGER DEFAULT 0,
    estimated_cost  REAL DEFAULT 0.0
);
CREATE INDEX idx_reports_generated ON reports(generated_at);

-- Incident journal
CREATE TABLE incidents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,           -- ISO 8601
    resolved_at  TEXT,                    -- NULL if open
    alert_name   TEXT,                    -- triggering alert (optional)
    title        TEXT NOT NULL,           -- one-line summary
    description  TEXT NOT NULL,           -- what happened
    root_cause   TEXT,                    -- identified cause
    resolution   TEXT,                    -- what fixed it
    severity     TEXT DEFAULT 'info',     -- info | warning | critical
    services     TEXT DEFAULT '',         -- comma-separated service names
    session_id   TEXT                     -- conversation that created this
);
CREATE INDEX idx_incidents_alert ON incidents(alert_name);
CREATE INDEX idx_incidents_created ON incidents(created_at);

-- Computed metric baselines
CREATE TABLE metric_baselines (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name  TEXT NOT NULL,           -- e.g. "node_cpu_seconds_total"
    labels       TEXT DEFAULT '{}',       -- JSON label set
    avg_value    REAL NOT NULL,
    p95_value    REAL,
    min_value    REAL,
    max_value    REAL,
    sample_count INTEGER NOT NULL,
    window_days  INTEGER NOT NULL,        -- lookback used to compute
    computed_at  TEXT NOT NULL             -- ISO 8601
);
CREATE INDEX idx_baselines_lookup ON metric_baselines(metric_name, computed_at);
```

### Source Layout

```
src/memory/
├── __init__.py
├── store.py        # Connection management, schema init, low-level CRUD
├── models.py       # TypedDicts for memory records (ReportRecord, IncidentRecord, etc.)
└── tools.py        # LangChain tools: search_incidents, record_incident, get_previous_report
```

### Configuration

```python
# Added to src/config.py
memory_db_path: str = ""   # Empty = memory disabled
```

Follows the same conditional pattern as PBS, Loki, TrueNAS — empty string means not configured,
all memory features degrade gracefully.

## Components

### 1. Database Layer (`src/memory/store.py`)

Core responsibilities:
- Initialize DB and create tables on first access (idempotent `CREATE TABLE IF NOT EXISTS`).
- Thread-safe connection management (SQLite supports concurrent reads, serialized writes).
- Typed CRUD functions — no raw SQL in callers.

Key functions:

```python
def get_connection() -> sqlite3.Connection
def init_schema(conn: sqlite3.Connection) -> None

# Reports
def save_report(conn, generated_at, lookback_days, markdown, data_json, ...) -> int
def get_latest_report(conn) -> ReportRecord | None
def get_reports(conn, limit=10) -> list[ReportRecord]

# Incidents
def save_incident(conn, title, description, ...) -> int
def update_incident(conn, incident_id, ...) -> None
def search_incidents(conn, query=None, alert_name=None, service=None, limit=20) -> list[IncidentRecord]
def get_open_incidents(conn) -> list[IncidentRecord]

# Baselines
def save_baselines(conn, baselines: list[BaselineRecord]) -> None
def get_baseline(conn, metric_name, labels=None) -> BaselineRecord | None
```

### 2. Report Archive Integration (`src/report/generator.py`)

Changes to existing report generator:

**After report generation:** Automatically save the report to the memory store.
```python
async def generate_report(lookback_days=None) -> str:
    # ... existing collection + formatting ...
    markdown = format_report_markdown(report_data)

    # NEW: archive to memory store (if configured)
    _archive_report(report_data, markdown)

    return markdown
```

**Before LLM narrative:** Load the previous report and pass it to the LLM prompt as context.
```python
async def _generate_narrative(collected_data, previous_report=None) -> str:
    prompt = (
        "... existing prompt ..."
        + (f"\n\nPrevious report summary:\n{previous_report}" if previous_report else "")
    )
```

This is a lightweight change — the report generator already has the data, we just persist it
and feed the previous one back in.

### 3. Agent Tools (`src/memory/tools.py`)

Four new tools, conditionally registered when `MEMORY_DB_PATH` is set:

**`memory_search_incidents`** (read)
- Search past incidents by keyword, alert name, or service name.
- Returns formatted list of matching incidents with root causes and resolutions.
- Use case: "Has this alert fired before? What was the root cause last time?"

**`memory_record_incident`** (write)
- Record a new incident during investigation.
- Input: title, description, alert_name (optional), root_cause (optional),
  resolution (optional), severity, services.
- Use case: Agent identifies root cause → records it for future reference.
- The agent's system prompt will guide it to propose recording only when a clear
  root cause or resolution has been identified.

**`memory_get_previous_report`** (read)
- Retrieve the most recent archived report (or N reports).
- Returns the markdown text and/or structured data.
- Use case: "What did last week's report say about node_exporter?"

**`memory_check_baseline`** (read)
- Check whether a metric value is within the normal range.
- Input: metric_name, current_value, labels (optional).
- Returns: baseline stats + assessment ("within normal range" / "above p95" / etc.).
- Use case: "Is 85% CPU normal for this host?"

### 4. Metric Baselines (`src/memory/baselines.py`)

Computed automatically after each weekly report generation:

1. Query Prometheus for key metrics over the report's lookback window.
2. Compute avg, p95, min, max for each.
3. Store in `metric_baselines` table.

**Which metrics to baseline:**
- Per-host CPU usage (`node_cpu_seconds_total`)
- Per-host memory usage (`node_memory_MemAvailable_bytes`)
- Per-host disk usage (`node_filesystem_avail_bytes`)
- Per-service error rate (from Loki error counts)
- Agent's own SLIs (p95 latency, tool success rate)

The metric list is configurable but ships with sensible defaults. Baselines are cheap to
compute (one PromQL query per metric) and small to store.

### 5. System Prompt Update

Add guidance for when to use memory tools:

```
When investigating alerts or anomalies:
- Search incident history first to check for known patterns.
- Check metric baselines to determine if values are abnormal.

When you identify a root cause or resolution:
- Record it as an incident so it can be referenced in future investigations.

When asked about past reports or trends:
- Use the report archive to retrieve previous findings.
```

## Testing Strategy

### Unit Tests (`tests/test_memory.py`)

Pure function tests, no I/O:
- Schema creation (in-memory SQLite)
- CRUD operations on all three tables
- Search/filter logic (keyword matching, date ranges)
- Baseline comparison logic (within range, above p95, etc.)
- Model serialization/deserialization
- Edge cases: empty DB, duplicate inserts, NULL fields

### Integration Tests (`tests/test_memory_integration.py`)

Mocked HTTP via respx, real SQLite (in-memory):
- Report archive flow: generate report → save → retrieve previous
- Incident tool invocation via LangChain tool interface
- Baseline computation with mocked Prometheus responses
- Search incidents with various filter combinations
- Graceful degradation when memory is not configured

### Report Integration Tests

Update existing `tests/test_report_integration.py`:
- Verify report is archived after generation (when memory configured)
- Verify previous report is loaded and passed to LLM prompt
- Verify report generation still works when memory is not configured

### Eval Cases (`src/eval/cases/`)

New YAML eval cases:
- `incident-search.yaml` — "Has this alert happened before?" → must call `memory_search_incidents`
- `baseline-check.yaml` — "Is this CPU value normal?" → must call `memory_check_baseline`
- `previous-report.yaml` — "What did last week's report say?" → must call `memory_get_previous_report`

## Scope

### In Scope

- SQLite database layer with schema management
- Report archive: auto-save + retrieval + narrative integration
- Incident journal: record + search + agent tools
- Metric baselines: computation + storage + query tool
- Conditional registration (all features off when `MEMORY_DB_PATH` is empty)
- Unit + integration tests for all components
- Documentation updates (architecture.md, tool-reference.md, code-flow.md)
- System prompt updates for memory tool guidance

### Out of Scope

- Conversation mining (analyzing past conversations for patterns)
- Alert event archival (Prometheus/Alertmanager handles this adequately)
- Admin UI for browsing memory contents
- Data export/import
- Memory pruning/retention policies (can be added later if DB grows)

## Build Steps

### Step 1: Database Foundation

- Create `src/memory/store.py` with connection management and schema init
- Create `src/memory/models.py` with TypedDicts for all record types
- Add `memory_db_path` to `Settings` in `src/config.py`
- Add mock settings for `memory_db_path` in `tests/conftest.py`
- Write unit tests for schema creation and basic CRUD

### Step 2: Report Archive

- Add `save_report()` and `get_latest_report()` to store
- Integrate auto-archive into `generate_report()` in `src/report/generator.py`
- Load previous report in `_generate_narrative()` for LLM context
- Add `memory_get_previous_report` tool
- Write unit + integration tests for archive flow

### Step 3: Incident Journal

- Add incident CRUD functions to store
- Create `memory_search_incidents` and `memory_record_incident` tools
- Register tools conditionally in `src/agent/agent.py`
- Update system prompt with incident recording guidance
- Write unit + integration tests + eval case

### Step 4: Metric Baselines

- Create `src/memory/baselines.py` with baseline computation logic
- Add `save_baselines()` and `get_baseline()` to store
- Hook baseline computation into post-report-generation flow
- Create `memory_check_baseline` tool
- Write unit + integration tests + eval case

### Step 5: Documentation & Polish

- Update `docs/architecture.md` — memory store section
- Update `docs/tool-reference.md` — new tool documentation
- Update `docs/code-flow.md` — memory read/write flows
- Update `readme.md` — Phase 7 build steps + completion
- Run full `make check`, verify all tests pass

## Dependencies

**New Python packages:** None. Uses `sqlite3` from stdlib.

**New environment variables:**
- `MEMORY_DB_PATH` — path to SQLite database file (empty = disabled)

**New make targets:**
- None required (DB auto-initializes on first access). Could add `make memory-reset`
  for development if useful.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| SQLite write contention under concurrent requests | Low (single-user homelab) | WAL mode + short transactions |
| DB file corruption on container crash | Data loss | Atomic writes where possible; DB is rebuildable from Prometheus + emails |
| Unbounded DB growth | Disk usage | Not a near-term concern at ~1 report/week + handful of incidents. Add retention policy later if needed |
| Agent records low-quality incidents | Noisy memory | System prompt guidance; user can review via search tool; manual cleanup via sqlite3 CLI |
| Baseline computation fails (Prometheus down) | Missing baselines | Graceful skip; baselines are supplementary, not critical path |

## Success Criteria

Phase 7 is complete when:

1. Weekly reports reference the previous report in the LLM narrative ("last week we
   recommended X — this week Y").
2. The agent can record an incident during investigation and retrieve it in a future session.
3. The agent can answer "is this metric value normal?" using computed baselines.
4. All features degrade gracefully when `MEMORY_DB_PATH` is not set.
5. All existing tests still pass (no regressions).
6. New tests cover all memory operations at unit + integration level.

## Estimated Test Count

- ~15 unit tests (schema, CRUD, models, baseline logic)
- ~15 integration tests (tool invocations, report archive flow, search, baselines)
- ~3 eval cases (incident search, baseline check, previous report)

Total: ~33 new tests, bringing project total from 537 to ~570.
