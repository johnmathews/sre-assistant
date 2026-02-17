"""Composite tool for HDD power state: current status, disk identity, and transition history.

Combines Prometheus disk_power_state metrics with TrueNAS disk inventory to produce
human-readable HDD summaries without requiring the LLM to chain multiple queries.
"""

import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
from langchain_core.tools import ToolException, tool  # pyright: ignore[reportUnknownVariableType]
from pydantic import BaseModel, Field

from src.agent.tools.prometheus import (
    DEFAULT_TIMEOUT_SECONDS as PROM_TIMEOUT,
)
from src.agent.tools.prometheus import (
    PrometheusSeries,
    _parse_duration,
    _query_prometheus,
)
from src.agent.tools.truenas import (
    TruenasDiskEntry,
    _format_bytes,
    _truenas_get,
)
from src.config import get_settings

logger = logging.getLogger(__name__)

# Prometheus power state values (from disk-status-exporter)
# See: https://github.com/johnmathews/disk-status-exporter
POWER_STATE_LABELS: dict[int, str] = {
    -2: "error",
    -1: "unknown",
    0: "standby",
    1: "idle",
    2: "active_or_idle",
    3: "idle_a",
    4: "idle_b",
    5: "idle_c",
    6: "active",
    7: "sleep",
}

# States that mean "disk is spun up / active"
_ACTIVE_STATES = {1, 2, 3, 4, 5, 6}
# States that mean "disk is spun down / not spinning"
_STANDBY_STATES = {0, 7}
# States that are error/indeterminate
_ERROR_STATES = {-2, -1}


def _state_group(value: float) -> str:
    """Classify a numeric power state into a meaningful group.

    Sub-state fluctuations (e.g. idle_a ↔ idle_b) are NOT real transitions.
    Only transitions between these groups count as real state changes.
    """
    int_val = int(value)
    if int_val in _ACTIVE_STATES:
        return "active"
    if int_val in _STANDBY_STATES:
        return "standby"
    return "error"


class DiskStats:
    """Per-disk stats computed from a 24h range query."""

    __slots__ = ("change_count", "standby_pct", "active_pct", "error_pct")

    def __init__(
        self,
        change_count: int,
        standby_pct: float,
        active_pct: float,
        error_pct: float,
    ) -> None:
        self.change_count = change_count
        self.standby_pct = standby_pct
        self.active_pct = active_pct
        self.error_pct = error_pct


def _compute_time_in_state(values: Sequence[object]) -> dict[str, float]:
    """Compute fraction of time spent in each state group from range query values.

    Uses the step duration between consecutive samples. Returns a dict
    mapping group name ("active"/"standby"/"error") to percentage (0-100).
    """
    if len(values) < 2:
        return {"active": 0.0, "standby": 0.0, "error": 0.0}

    group_seconds: dict[str, float] = {"active": 0.0, "standby": 0.0, "error": 0.0}

    for i in range(len(values) - 1):
        curr = values[i]
        nxt = values[i + 1]
        if not isinstance(curr, list) or not isinstance(nxt, list):
            continue
        if len(curr) < 2 or len(nxt) < 2:
            continue
        ts_curr = float(curr[0])
        ts_next = float(nxt[0])
        duration = ts_next - ts_curr
        group = _state_group(float(str(curr[1])))
        group_seconds[group] = group_seconds.get(group, 0.0) + duration

    total = sum(group_seconds.values())
    if total == 0:
        return {"active": 0.0, "standby": 0.0, "error": 0.0}
    return {g: round(s / total * 100, 1) for g, s in group_seconds.items()}


def _count_group_transitions(values: Sequence[object]) -> int:
    """Count how many times the state group changes in a Prometheus range result.

    Each element of `values` is [timestamp, string_value].
    Only transitions between groups (active/standby/error) are counted.
    """
    if len(values) < 2:
        return 0
    transitions = 0
    first_pair: list[object] = values[0] if isinstance(values[0], list) else []
    prev_group = _state_group(float(str(first_pair[1]))) if len(first_pair) > 1 else "error"
    for raw_pair in values[1:]:
        pair: list[object] = raw_pair if isinstance(raw_pair, list) else []
        if len(pair) < 2:
            continue
        curr_group = _state_group(float(str(pair[1])))
        if curr_group != prev_group:
            transitions += 1
            prev_group = curr_group
    return transitions


# Time windows for progressive changes() widening (seconds)
TRANSITION_WINDOWS = ["1h", "6h", "24h", "7d"]

# Consolidated window-to-seconds mapping (used by multiple functions)
WINDOW_SECONDS: dict[str, int] = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}


def _select_step(duration_seconds: int) -> str:
    """Choose an appropriate Prometheus range query step for the given duration."""
    if duration_seconds <= 3600:
        return "15s"
    if duration_seconds <= 86400:
        return "60s"
    return "5m"


def _build_promql(pool: str | None = None) -> str:
    """Build the PromQL selector for HDD power state, optionally filtered by pool."""
    if pool:
        return f'disk_power_state{{type="hdd", pool="{pool}"}}'
    return 'disk_power_state{type="hdd"}'


# --- Hex extraction for cross-referencing ---


def _extract_hex(s: str) -> str:
    """Extract the longest hex substring (>= 8 chars) from a string.

    Used to match Prometheus device_id (e.g. '/dev/disk/by-id/wwn-0x5000c500eb02b449')
    with TrueNAS identifier (e.g. '{serial_lunid}5000c500eb02b449').
    """
    matches = re.findall(r"[0-9a-fA-F]{8,}", s)
    return max(matches, key=len).lower() if matches else ""


def _build_disk_lookup(disks: list[TruenasDiskEntry]) -> dict[str, TruenasDiskEntry]:
    """Build a lookup table from hex-extracted identifier to disk entry."""
    lookup: dict[str, TruenasDiskEntry] = {}
    for disk in disks:
        identifier = disk.get("identifier", "")
        hex_key = _extract_hex(identifier)
        if hex_key:
            lookup[hex_key] = disk
    return lookup


def _format_disk_name(disk: TruenasDiskEntry | None, device_id: str) -> str:
    """Format a disk as 'name: model (size)' or fall back to device_id."""
    if disk:
        name = disk.get("name", "?")
        model = disk.get("model", "?")
        size = _format_bytes(disk.get("size", 0))
        serial = disk.get("serial", "")
        serial_str = f", serial={serial}" if serial else ""
        return f"{name}: {model} ({size}{serial_str})"
    # Strip path prefix for readability
    short_id = device_id.rsplit("/", 1)[-1] if "/" in device_id else device_id
    return short_id


def _format_power_state(value: float) -> str:
    """Map a numeric power state value to a human-readable label."""
    int_val = int(value)
    label = POWER_STATE_LABELS.get(int_val)
    if label:
        return f"{label} ({int_val})"
    return f"unknown state ({int_val})"


# --- Prometheus queries ---


async def _get_current_power_states(pool: str | None = None) -> list[PrometheusSeries]:
    """Get current disk_power_state{type='hdd'} from Prometheus."""
    data = await _query_prometheus(
        "/api/v1/query",
        {"query": _build_promql(pool)},
    )
    if data.get("status") != "success":
        return []
    return list(data.get("data", {}).get("result", []))


async def _find_transition_window(pool: str | None = None) -> tuple[str | None, dict[str, int]]:
    """Find the shortest time window that contains power state group changes.

    Uses range queries with progressive widening: 1h → 6h → 24h → 7d.
    Only counts transitions between state groups (active/standby/error),
    not sub-state fluctuations (e.g. idle_a ↔ idle_b).
    Returns (window_string, per_device_change_counts) or (None, {}).
    """
    query = _build_promql(pool)
    for window in TRANSITION_WINDOWS:
        duration = WINDOW_SECONDS.get(window, 3600)
        now = datetime.now(UTC)
        start_ts = str(int(now.timestamp()) - duration)
        end_ts = str(int(now.timestamp()))
        step = _select_step(duration)

        data = await _query_prometheus(
            "/api/v1/query_range",
            {
                "query": query,
                "start": start_ts,
                "end": end_ts,
                "step": step,
            },
        )
        if data.get("status") != "success":
            continue
        results = data.get("data", {}).get("result", [])
        counts: dict[str, int] = {}
        has_changes = False
        for series in results:
            metric = series.get("metric", {})
            device_id = metric.get("device_id", "unknown") if isinstance(metric, dict) else "unknown"
            values = series.get("values", [])
            count = _count_group_transitions(values if isinstance(values, list) else [])
            counts[device_id] = count
            if count > 0:
                has_changes = True
        if has_changes:
            return window, counts
    return None, {}


async def _get_stats(
    duration_seconds: int = 86400,
    pool: str | None = None,
) -> dict[str, DiskStats]:
    """Get per-disk stats for a given duration: group transition count and time-in-state.

    Counts transitions between groups (active/standby/error), not sub-state
    fluctuations like idle_a ↔ idle_b.
    """
    now = datetime.now(UTC)
    start_ts = str(int(now.timestamp()) - duration_seconds)
    end_ts = str(int(now.timestamp()))

    data = await _query_prometheus(
        "/api/v1/query_range",
        {
            "query": _build_promql(pool),
            "start": start_ts,
            "end": end_ts,
            "step": _select_step(duration_seconds),
        },
    )
    if data.get("status") != "success":
        return {}
    stats: dict[str, DiskStats] = {}
    for series in data.get("data", {}).get("result", []):
        metric = series.get("metric", {})
        device_id = metric.get("device_id", "unknown") if isinstance(metric, dict) else "unknown"
        values = series.get("values", [])
        vals = values if isinstance(values, list) else []
        pcts = _compute_time_in_state(vals)
        stats[device_id] = DiskStats(
            change_count=_count_group_transitions(vals),
            standby_pct=pcts["standby"],
            active_pct=pcts["active"],
            error_pct=pcts["error"],
        )
    return stats


async def _find_transition_times(
    window: str,
    pool: str | None = None,
) -> dict[str, str]:
    """Pinpoint when each disk last changed state by range-querying within the window.

    Returns a dict mapping device_id to a human-readable transition description.
    """
    duration = WINDOW_SECONDS.get(window, 3600)
    now = datetime.now(UTC)
    start_ts = str(int(now.timestamp()) - duration)
    end_ts = str(int(now.timestamp()))
    step = _select_step(duration)

    data = await _query_prometheus(
        "/api/v1/query_range",
        {
            "query": _build_promql(pool),
            "start": start_ts,
            "end": end_ts,
            "step": step,
        },
    )
    if data.get("status") != "success":
        return {}

    transitions: dict[str, str] = {}
    for series in data.get("data", {}).get("result", []):
        device_id = series.get("metric", {}).get("device_id", "unknown")
        values = series.get("values", [])
        if len(values) < 2:
            continue

        # Walk backwards to find the most recent group transition
        # (active ↔ standby ↔ error), ignoring sub-state fluctuations
        last_transition_ts: float | None = None
        from_state: float | None = None
        to_state: float | None = None

        for i in range(len(values) - 1, 0, -1):
            curr_val = float(values[i][1])
            prev_val = float(values[i - 1][1])
            if _state_group(curr_val) != _state_group(prev_val):
                last_transition_ts = float(values[i][0])
                from_state = prev_val
                to_state = curr_val
                break

        if last_transition_ts is not None and from_state is not None and to_state is not None:
            dt = datetime.fromtimestamp(last_transition_ts, tz=UTC)
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            from_label = _format_power_state(from_state)
            to_label = _format_power_state(to_state)
            transitions[device_id] = f"{time_str} ({from_label} → {to_label})"

    return transitions


# --- Composite tool ---


class HddPowerStatusInput(BaseModel):
    """Input for HDD power status summary."""

    duration: str = Field(
        default="24h",
        description=(
            "Time window for stats and transition history. "
            "Examples: '1h', '6h', '12h', '24h', '3d', '1w'. Default '24h'."
        ),
    )
    pool: str | None = Field(
        default=None,
        description=(
            "Optional ZFS pool name to filter disks (e.g. 'tank', 'backup'). If omitted, all HDD pools are included."
        ),
    )


TOOL_DESCRIPTION = (
    "Get a complete HDD power status summary for TrueNAS: which disks are spun up "
    "or in standby, mapped to human-readable disk names (model, size, serial), "
    "how many state changes occurred in the requested duration, "
    "and when each disk last changed power state.\n\n"
    "Accepts optional `duration` (default '24h') and `pool` filter.\n\n"
    "Use this for ANY question about HDD power state, spinup, spindown, or disk activity. "
    "This tool handles all the cross-referencing and transition detection automatically.\n\n"
    "Examples:\n"
    "- 'Which HDDs are spun up?' → hdd_power_status()\n"
    "- 'Are the backup drives spun down?' → hdd_power_status(pool='backup')\n"
    "- 'How many state changes in the last 12 hours?' → hdd_power_status(duration='12h')\n"
    "- 'Were the tank HDDs active this week?' → hdd_power_status(duration='1w', pool='tank')\n"
    "- 'What fraction of the last 6h were my drives in standby?' → hdd_power_status(duration='6h')"
)


@tool("hdd_power_status", args_schema=HddPowerStatusInput)  # pyright: ignore[reportUnknownParameterType]
async def hdd_power_status(
    duration: str = "24h",
    pool: str | None = None,
) -> str:
    """Get complete HDD power status summary. See TOOL_DESCRIPTION."""
    # Parse and validate the duration
    duration_seconds = _parse_duration(duration)
    if duration_seconds is None or duration_seconds <= 0:
        raise ToolException(f"Invalid duration '{duration}'. Use a value like '1h', '6h', '12h', '24h', '3d', or '1w'.")
    dur_int = int(duration_seconds)

    settings = get_settings()

    # Step 1: Get current power states from Prometheus
    try:
        power_states = await _get_current_power_states(pool=pool)
    except httpx.ConnectError as e:
        raise ToolException(f"Cannot connect to Prometheus: {e}") from e
    except httpx.TimeoutException as e:
        raise ToolException(f"Prometheus query timed out after {PROM_TIMEOUT}s: {e}") from e
    except httpx.HTTPStatusError as e:
        raise ToolException(f"Prometheus API error: HTTP {e.response.status_code} - {e.response.text[:500]}") from e

    if not power_states:
        raise ToolException("No disk_power_state metrics found. Check that disk-status-exporter is running on TrueNAS.")

    # Step 2: Get disk inventory from TrueNAS (if configured)
    disk_lookup: dict[str, TruenasDiskEntry] = {}
    if settings.truenas_url:
        try:
            disks_raw = await _truenas_get("/disk")
            disks: list[TruenasDiskEntry] = disks_raw if isinstance(disks_raw, list) else []
            disk_lookup = _build_disk_lookup(disks)
        except Exception:
            logger.warning("Failed to fetch TrueNAS disk inventory; showing device IDs only")

    # Step 3: Cross-reference and format current state
    lines: list[str] = ["HDD Power Status:\n"]

    active_disks: list[str] = []
    standby_disks: list[str] = []
    other_disks: list[str] = []

    for series in power_states:
        device_id = series.get("metric", {}).get("device_id", "unknown")
        series_pool = series.get("metric", {}).get("pool", "")
        value_pair = series.get("value", [0, "0"])
        power_value = float(str(value_pair[1])) if len(value_pair) > 1 else -1
        power_int = int(power_value)

        # Cross-reference with TrueNAS disk inventory
        hex_key = _extract_hex(device_id)
        disk_entry = disk_lookup.get(hex_key)
        disk_name = _format_disk_name(disk_entry, device_id)
        state_label = _format_power_state(power_value)
        pool_str = f" [pool: {series_pool}]" if series_pool else ""

        line = f"  {disk_name} — {state_label}{pool_str}"
        if power_int in _STANDBY_STATES:
            standby_disks.append(line)
        elif power_int in _ACTIVE_STATES:
            active_disks.append(line)
        else:
            other_disks.append(line)

    if active_disks:
        lines.append(f"Spun up ({len(active_disks)}):")
        lines.extend(active_disks)
    if standby_disks:
        if active_disks:
            lines.append("")
        lines.append(f"In standby ({len(standby_disks)}):")
        lines.extend(standby_disks)
    if other_disks:
        if active_disks or standby_disks:
            lines.append("")
        lines.append(f"Other ({len(other_disks)}):")
        lines.extend(other_disks)

    # Step 4: Get stats for the requested duration (change counts + time-in-state)
    period_stats: dict[str, DiskStats] = {}
    try:
        period_stats = await _get_stats(dur_int, pool)
    except Exception:
        logger.warning("Failed to query stats for %s", duration, exc_info=True)

    if period_stats:
        total_changes = sum(s.change_count for s in period_stats.values())
        lines.append(f"\nLast {duration}: {total_changes} state change(s) total")
        # Build pool lookup from current power states (metrics have pool label)
        pool_by_device: dict[str, str] = {}
        for series in power_states:
            did = series.get("metric", {}).get("device_id", "unknown")
            pool_by_device[did] = series.get("metric", {}).get("pool", "")
        for device_id, stats in period_stats.items():
            hex_key = _extract_hex(device_id)
            disk_entry = disk_lookup.get(hex_key)
            disk_name = _format_disk_name(disk_entry, device_id)
            dev_pool = pool_by_device.get(device_id, "")
            pool_str = f" [pool: {dev_pool}]" if dev_pool else ""
            lines.append(
                f"  {disk_name}{pool_str} — {stats.change_count} change(s), "
                f"standby {stats.standby_pct}%, active {stats.active_pct}%"
            )

    # Step 5: Find last state transitions
    lines.append("\nLast power state change:")
    try:
        window, _ = await _find_transition_window(pool=pool)
        if window is None:
            lines.append(
                "  No power state changes detected in the last 7 days. "
                "All disks have been in their current state for at least 7 days."
            )
        else:
            transitions = await _find_transition_times(window, pool=pool)
            if not transitions:
                lines.append(f"  Changes detected in the last {window} but could not pinpoint exact times.")
            else:
                for device_id, transition_desc in transitions.items():
                    hex_key = _extract_hex(device_id)
                    disk_entry = disk_lookup.get(hex_key)
                    disk_name = _format_disk_name(disk_entry, device_id)
                    lines.append(f"  {disk_name} — {transition_desc}")

                # Note any disks without transitions in this window
                transition_hex_keys = {_extract_hex(did) for did in transitions}
                for series in power_states:
                    device_id = series.get("metric", {}).get("device_id", "unknown")
                    hex_key = _extract_hex(device_id)
                    if hex_key not in transition_hex_keys:
                        disk_entry = disk_lookup.get(hex_key)
                        disk_name = _format_disk_name(disk_entry, device_id)
                        lines.append(f"  {disk_name} — no change in the last {window}")

    except Exception:
        logger.warning("Failed to query transition history", exc_info=True)
        lines.append("  Could not determine transition history (Prometheus query failed).")

    return "\n".join(lines)


hdd_power_status.description = TOOL_DESCRIPTION
hdd_power_status.handle_tool_error = True
