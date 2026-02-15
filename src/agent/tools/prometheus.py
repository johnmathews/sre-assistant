"""LangChain tool for querying a Prometheus instance via its HTTP API."""

import logging
from datetime import UTC, datetime
from typing import TypedDict

import httpx
from langchain_core.tools import ToolException, tool  # pyright: ignore[reportUnknownVariableType]
from pydantic import BaseModel, Field

from src.config import get_settings

logger = logging.getLogger(__name__)

# --- Input validation ---

MAX_QUERY_LENGTH = 2000
MAX_RANGE_STEP_SECONDS = 86400  # 1 day
MAX_RANGE_DURATION_SECONDS = 30 * 86400  # 30 days
DEFAULT_TIMEOUT_SECONDS = 15


class PrometheusInstantInput(BaseModel):
    """Input for an instant PromQL query (single point in time)."""

    query: str = Field(
        description=(
            "A valid PromQL query string. Always include label filters "
            "(e.g. {hostname='jellyfin'}) to avoid returning thousands of time series."
        ),
        min_length=1,
        max_length=MAX_QUERY_LENGTH,
    )
    time: str | None = Field(
        default=None,
        description="Optional RFC3339 or Unix timestamp. Defaults to current time.",
    )


class PrometheusRangeInput(BaseModel):
    """Input for a range PromQL query (values over a time window)."""

    query: str = Field(
        description=(
            "A valid PromQL query string. Always include label filters "
            "(e.g. {hostname='jellyfin'}) to avoid returning thousands of time series."
        ),
        min_length=1,
        max_length=MAX_QUERY_LENGTH,
    )
    start: str = Field(
        description="Start time as RFC3339 (e.g. '2024-01-15T10:00:00Z') or Unix timestamp.",
    )
    end: str = Field(
        description="End time as RFC3339 or Unix timestamp. Must be after start.",
    )
    step: str = Field(
        default="60s",
        description="Query resolution step (e.g. '15s', '60s', '5m'). Smaller steps = more data points.",
    )


def _validate_range_params(start: str, end: str, step: str) -> list[str]:
    """Validate that range query parameters are reasonable. Returns list of error messages."""
    errors: list[str] = []

    try:
        start_ts = _parse_timestamp(start)
        end_ts = _parse_timestamp(end)
    except ValueError as e:
        errors.append(f"Invalid timestamp: {e}")
        return errors

    if end_ts <= start_ts:
        errors.append("end must be after start")

    duration = end_ts - start_ts
    if duration > MAX_RANGE_DURATION_SECONDS:
        errors.append(f"Time range too large: {duration}s exceeds maximum of {MAX_RANGE_DURATION_SECONDS}s (30 days)")

    step_seconds = _parse_duration(step)
    if step_seconds is not None and step_seconds > MAX_RANGE_STEP_SECONDS:
        errors.append(f"Step too large: {step_seconds}s exceeds maximum of {MAX_RANGE_STEP_SECONDS}s")

    if step_seconds is not None and duration / step_seconds > 11000:
        points = int(duration / step_seconds)
        errors.append(
            f"Too many data points: {duration}s range with {step_seconds}s step "
            + f"would produce {points} points. Use a larger step or shorter range."
        )

    return errors


def _parse_timestamp(ts: str) -> float:
    """Parse an RFC3339 string or numeric Unix timestamp to a float."""
    try:
        return float(ts)
    except ValueError:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.replace(tzinfo=UTC if dt.tzinfo is None else dt.tzinfo).timestamp()


def _parse_duration(step: str) -> float | None:
    """Parse a Prometheus duration string (e.g. '60s', '5m') to seconds. Returns None if unparseable."""
    try:
        return float(step)
    except ValueError:
        pass

    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if step and step[-1] in multipliers:
        try:
            return float(step[:-1]) * multipliers[step[-1]]
        except ValueError:
            return None
    return None


# --- Prometheus response types ---


class PrometheusSeries(TypedDict, total=False):
    metric: dict[str, str]
    value: list[float | str]
    values: list[list[float | str]]


class PrometheusData(TypedDict, total=False):
    resultType: str
    result: list[PrometheusSeries]


class PrometheusResponse(TypedDict, total=False):
    status: str
    error: str
    data: PrometheusData


def _format_result(data: PrometheusResponse) -> str:
    """Format a Prometheus API response into a readable string for the LLM."""
    status = data.get("status", "unknown")
    if status != "success":
        error_msg = data.get("error", "unknown error")
        return f"Prometheus query failed: {error_msg}"

    result_data = data.get("data", {})
    result_type = result_data.get("resultType", "unknown")
    results = list(result_data.get("result", []))

    if not results:
        return "Query returned no results. Check that the metric name and label filters are correct."

    lines: list[str] = [f"Result type: {result_type}, series count: {len(results)}"]

    if len(results) > 50:
        lines.append(f"WARNING: {len(results)} series returned. Consider adding label filters to narrow results.")
        results = results[:50]
        lines.append("(showing first 50 series)")

    for series in results:
        metric = series.get("metric", {})
        label_str = ", ".join(f'{k}="{v}"' for k, v in metric.items())

        if result_type == "vector":
            value_pair = series.get("value", [0, ""])
            val = str(value_pair[1]) if len(value_pair) > 1 else ""
            lines.append(f"  {{{label_str}}} => {val}")
        elif result_type == "matrix":
            values = series.get("values", [])
            lines.append(f"  {{{label_str}}} => {len(values)} samples")
            for point in values[:3]:
                ts_val = float(point[0])
                dt_str = datetime.fromtimestamp(ts_val, tz=UTC).strftime("%H:%M:%S")
                lines.append(f"    [{dt_str}] {point[1]}")
            if len(values) > 6:
                lines.append(f"    ... ({len(values) - 6} more samples)")
            for point in values[-3:]:
                ts_val = float(point[0])
                dt_str = datetime.fromtimestamp(ts_val, tz=UTC).strftime("%H:%M:%S")
                lines.append(f"    [{dt_str}] {point[1]}")
        elif result_type == "scalar":
            lines.append(f"  scalar => {series}")
        else:
            lines.append(f"  {series}")

    return "\n".join(lines)


async def _query_prometheus(endpoint: str, params: dict[str, str]) -> PrometheusResponse:
    """Make an HTTP request to the Prometheus API."""
    url = f"{get_settings().prometheus_url}{endpoint}"
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
        response = await client.get(url, params=params)
        _ = response.raise_for_status()
        data: PrometheusResponse = response.json()  # pyright: ignore[reportAny]
        return data


TOOL_DESCRIPTION_INSTANT = (
    "Query Prometheus for the current value of a metric (instant query). "
    "Use this for point-in-time questions like 'what is the current CPU usage?' "
    "or 'how much memory is free right now?'.\n\n"
    "This homelab's Prometheus setup:\n"
    "- The `hostname` label identifies each host "
    "(e.g. hostname='jellyfin', hostname='proxmox', hostname='immich')\n"
    "- Managed hosts (node_exporter + cadvisor): proxmox, truenas, media, infra, "
    "jellyfin, immich, prometheus, tube-archivist, paperless, open-webui\n"
    "- Network/services (dedicated exporters): mikrotik, home-assistant, unbound, "
    "key-server, traefik, cloudflared, mailcow\n"
    "- Exporters: node_exporter (node_*), cadvisor (container_*), pve_exporter (pve_*), "
    "adguard, NUT (network_ups_tools_*), smartctl, IPMI, MikroTik (mktxp_*)\n"
    "- Recording rules: share drive probes, disk spindown, network traffic (signed bytes/sec), "
    "UPS mains status\n\n"
    "IMPORTANT: Always include label filters to avoid returning too many time series. "
    "For example, use {hostname='jellyfin'} not just the bare metric name."
)

TOOL_DESCRIPTION_RANGE = (
    "Query Prometheus for metric values over a time range (range query). "
    "Use this for trend questions like 'how has CPU changed over the last hour?' "
    "or 'show memory usage for the past day'.\n\n"
    "Same homelab setup as the instant query tool. Always include label filters.\n\n"
    "Choose an appropriate step size for the time range:\n"
    "- Last 1 hour: step='15s' or '60s'\n"
    "- Last 6 hours: step='60s' or '5m'\n"
    "- Last 24 hours: step='5m'\n"
    "- Last 7 days: step='1h'\n\n"
    "IMPORTANT: Always include label filters to avoid returning too many time series."
)


@tool("prometheus_instant_query", args_schema=PrometheusInstantInput)
async def prometheus_instant_query(query: str, time: str | None = None) -> str:
    """Query Prometheus for current metric value. See TOOL_DESCRIPTION_INSTANT."""
    params: dict[str, str] = {"query": query}
    if time is not None:
        params["time"] = time

    logger.info("Prometheus instant query: %s", query)
    try:
        data = await _query_prometheus("/api/v1/query", params)
    except httpx.HTTPStatusError as e:
        raise ToolException(f"Prometheus API error: HTTP {e.response.status_code} - {e.response.text[:500]}") from e
    except httpx.ConnectError as e:
        raise ToolException(f"Cannot connect to Prometheus at {get_settings().prometheus_url}: {e}") from e
    except httpx.TimeoutException as e:
        raise ToolException(f"Prometheus query timed out after {DEFAULT_TIMEOUT_SECONDS}s: {e}") from e

    return _format_result(data)


prometheus_instant_query.description = TOOL_DESCRIPTION_INSTANT


@tool("prometheus_range_query", args_schema=PrometheusRangeInput)
async def prometheus_range_query(query: str, start: str, end: str, step: str = "60s") -> str:
    """Query Prometheus for metric values over a time range. See TOOL_DESCRIPTION_RANGE."""
    validation_errors = _validate_range_params(start, end, step)
    if validation_errors:
        raise ToolException(f"Invalid range query parameters: {'; '.join(validation_errors)}")

    params: dict[str, str] = {"query": query, "start": start, "end": end, "step": step}

    logger.info("Prometheus range query: %s (start=%s, end=%s, step=%s)", query, start, end, step)
    try:
        data = await _query_prometheus("/api/v1/query_range", params)
    except httpx.HTTPStatusError as e:
        raise ToolException(f"Prometheus API error: HTTP {e.response.status_code} - {e.response.text[:500]}") from e
    except httpx.ConnectError as e:
        raise ToolException(f"Cannot connect to Prometheus at {get_settings().prometheus_url}: {e}") from e
    except httpx.TimeoutException as e:
        raise ToolException(f"Prometheus query timed out after {DEFAULT_TIMEOUT_SECONDS}s: {e}") from e

    return _format_result(data)


prometheus_range_query.description = TOOL_DESCRIPTION_RANGE
