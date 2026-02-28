"""Weekly reliability report generator.

Queries Prometheus, Grafana, and Loki APIs directly (not through the LangChain
agent) to collect structured data, then makes a single LLM call for a narrative
summary.  Each collector is independent and wrapped in try/except so a partial
report is always produced.
"""

import asyncio
import json
import logging
import ssl
from datetime import UTC, datetime
from typing import NotRequired, TypedDict

import httpx
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from src.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15


# ---------------------------------------------------------------------------
# Structured data types
# ---------------------------------------------------------------------------


class AlertSummaryData(TypedDict):
    total_rules: int
    active_alerts: int
    alerts_by_severity: dict[str, int]
    active_alert_names: list[str]


class SLOStatusData(TypedDict):
    p95_latency_seconds: float | None
    tool_success_rate: float | None
    llm_error_rate: float | None
    availability: float | None
    component_availability: NotRequired[dict[str, float]]


class ToolUsageData(TypedDict):
    tool_calls: dict[str, int]
    tool_errors: dict[str, int]


class CostData(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float


class LokiErrorSummary(TypedDict):
    errors_by_service: dict[str, int]
    total_errors: int
    previous_total_errors: NotRequired[int | None]
    previous_errors_by_service: NotRequired[dict[str, int]]
    error_samples: NotRequired[dict[str, str]]


class BackupGroupHealth(TypedDict):
    backup_type: str
    backup_id: str
    last_backup_ts: int
    backup_count: int
    stale: bool


class DatastoreHealth(TypedDict):
    store: str
    total_bytes: int
    used_bytes: int
    usage_percent: float


class BackupHealthData(TypedDict):
    datastores: list[DatastoreHealth]
    backups: list[BackupGroupHealth]
    stale_count: int
    total_count: int


class ReportData(TypedDict):
    generated_at: str
    lookback_days: int
    alerts: AlertSummaryData | None
    slo_status: SLOStatusData | None
    tool_usage: ToolUsageData | None
    cost: CostData | None
    loki_errors: LokiErrorSummary | None
    backup_health: NotRequired[BackupHealthData | None]
    narrative: str


# ---------------------------------------------------------------------------
# Prometheus query helper
# ---------------------------------------------------------------------------


async def _prom_query(
    client: httpx.AsyncClient,
    prometheus_url: str,
    query: str,
) -> list[dict[str, object]]:
    """Run a Prometheus instant query, return the result list."""
    resp = await client.get(
        f"{prometheus_url}/api/v1/query",
        params={"query": query},
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    _ = resp.raise_for_status()
    body: dict[str, object] = resp.json()
    data = body.get("data")
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, list):
            return result
    return []


def _scalar_value(results: list[dict[str, object]]) -> float | None:
    """Extract a single scalar float from a Prometheus instant query result."""
    if not results:
        return None
    first = results[0]
    value = first.get("value")
    if isinstance(value, list) and len(value) >= 2:
        try:
            return float(str(value[1]))
        except (ValueError, TypeError):
            return None
    return None


def _format_plain_table(
    headers: list[str],
    rows: list[list[str]],
    right_align: set[int] | None = None,
) -> str:
    """Format a plain-text table with aligned columns (no pipe characters).

    Args:
        headers: Column header strings.
        rows: List of rows, each a list of cell strings.
        right_align: Set of column indices (0-based) to right-align.

    Returns:
        Multi-line string with padded columns separated by two spaces.
    """
    right_align = right_align or set()
    if not rows:
        return ""
    all_data = [headers, *rows]
    col_widths = [max(len(row[i]) for row in all_data) for i in range(len(headers))]

    def fmt_row(cells: list[str]) -> str:
        parts: list[str] = []
        for i, cell in enumerate(cells):
            width = col_widths[i]
            parts.append(cell.rjust(width) if i in right_align else cell.ljust(width))
        return "  ".join(parts)

    lines = [fmt_row(headers)]
    lines.append("  ".join("-" * w for w in col_widths))
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


async def _collect_alert_summary(lookback_days: int) -> AlertSummaryData:
    """Collect alert rule count and currently active alerts from Grafana."""
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.grafana_service_account_token}"}
    _ = lookback_days  # Alert summary shows current state

    async with httpx.AsyncClient() as client:
        # Get alert rules
        rules_resp = await client.get(
            f"{settings.grafana_url}/api/v1/provisioning/alert-rules",
            headers=headers,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        _ = rules_resp.raise_for_status()
        rules: list[object] = rules_resp.json()
        total_rules = len(rules)

        # Get active alerts
        alerts_resp = await client.get(
            f"{settings.grafana_url}/api/alertmanager/grafana/api/v2/alerts/groups",
            headers=headers,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        _ = alerts_resp.raise_for_status()
        groups: list[dict[str, object]] = alerts_resp.json()

        active_alerts: list[str] = []
        severity_counts: dict[str, int] = {}
        for group in groups:
            group_alerts = group.get("alerts", [])
            if not isinstance(group_alerts, list):
                continue
            for alert in group_alerts:
                if not isinstance(alert, dict):
                    continue
                status = alert.get("status", {})
                if isinstance(status, dict) and status.get("state") == "active":
                    labels = alert.get("labels", {})
                    if isinstance(labels, dict):
                        name = str(labels.get("alertname", "unknown"))
                        active_alerts.append(name)
                        severity = str(labels.get("severity", "unknown"))
                        severity_counts[severity] = severity_counts.get(severity, 0) + 1

    return AlertSummaryData(
        total_rules=total_rules,
        active_alerts=len(active_alerts),
        alerts_by_severity=severity_counts,
        active_alert_names=active_alerts,
    )


async def _collect_slo_status(lookback_days: int) -> SLOStatusData:
    """Collect SLO metrics from Prometheus over the lookback window."""
    settings = get_settings()
    window = f"{lookback_days}d"

    async with httpx.AsyncClient() as client:
        url = settings.prometheus_url

        p95_results = await _prom_query(
            client, url, f"histogram_quantile(0.95, rate(sre_assistant_request_duration_seconds_bucket[{window}]))"
        )
        p95 = _scalar_value(p95_results)

        # Tool success rate: 1 - (errors / total)
        tool_total = await _prom_query(client, url, f"sum(increase(sre_assistant_tool_calls_total[{window}]))")
        tool_errors = await _prom_query(
            client, url, f'sum(increase(sre_assistant_tool_calls_total{{status="error"}}[{window}]))'
        )
        total_val = _scalar_value(tool_total)
        error_val = _scalar_value(tool_errors)
        tool_success: float | None = None
        if total_val is not None and total_val > 0:
            tool_success = 1.0 - ((error_val or 0.0) / total_val)

        # LLM error rate
        llm_total = await _prom_query(client, url, f"sum(increase(sre_assistant_llm_calls_total[{window}]))")
        llm_errors = await _prom_query(
            client, url, f'sum(increase(sre_assistant_llm_calls_total{{status="error"}}[{window}]))'
        )
        llm_total_val = _scalar_value(llm_total)
        llm_error_val = _scalar_value(llm_errors)
        llm_error_rate: float | None = None
        if llm_total_val is not None and llm_total_val > 0:
            llm_error_rate = (llm_error_val or 0.0) / llm_total_val

        # Availability: per-component and overall average
        avail_results = await _prom_query(client, url, f"avg_over_time(sre_assistant_component_healthy[{window}])")
        availability: float | None = None
        component_availability: dict[str, float] = {}
        if avail_results:
            for r in avail_results:
                if isinstance(r, dict):
                    metric = r.get("metric", {})
                    val = _scalar_value([r])
                    if val is not None and isinstance(metric, dict):
                        component = str(metric.get("component", "unknown"))
                        component_availability[component] = val
            if component_availability:
                availability = sum(component_availability.values()) / len(component_availability)

    return SLOStatusData(
        p95_latency_seconds=p95,
        tool_success_rate=tool_success,
        llm_error_rate=llm_error_rate,
        availability=availability,
        component_availability=component_availability,
    )


async def _collect_tool_usage(lookback_days: int) -> ToolUsageData:
    """Collect per-tool call counts and error counts from Prometheus."""
    settings = get_settings()
    window = f"{lookback_days}d"

    async with httpx.AsyncClient() as client:
        url = settings.prometheus_url

        total_results = await _prom_query(
            client, url, f"sum by (tool_name) (increase(sre_assistant_tool_calls_total[{window}]))"
        )
        error_results = await _prom_query(
            client,
            url,
            f'sum by (tool_name) (increase(sre_assistant_tool_calls_total{{status="error"}}[{window}]))',
        )

    tool_calls: dict[str, int] = {}
    for r in total_results:
        if isinstance(r, dict):
            metric = r.get("metric", {})
            if isinstance(metric, dict):
                name = str(metric.get("tool_name", "unknown"))
                val = _scalar_value([r])
                if val is not None:
                    tool_calls[name] = int(val)

    tool_errors: dict[str, int] = {}
    for r in error_results:
        if isinstance(r, dict):
            metric = r.get("metric", {})
            if isinstance(metric, dict):
                name = str(metric.get("tool_name", "unknown"))
                val = _scalar_value([r])
                if val is not None and val > 0:
                    tool_errors[name] = int(val)

    return ToolUsageData(tool_calls=tool_calls, tool_errors=tool_errors)


async def _collect_cost_data(lookback_days: int) -> CostData:
    """Collect token usage and cost from Prometheus."""
    settings = get_settings()
    window = f"{lookback_days}d"

    async with httpx.AsyncClient() as client:
        url = settings.prometheus_url

        prompt_results = await _prom_query(
            client, url, f'increase(sre_assistant_llm_token_usage_total{{type="prompt"}}[{window}])'
        )
        completion_results = await _prom_query(
            client, url, f'increase(sre_assistant_llm_token_usage_total{{type="completion"}}[{window}])'
        )
        cost_results = await _prom_query(
            client, url, f"increase(sre_assistant_llm_estimated_cost_dollars_total[{window}])"
        )

    prompt_tokens = int(_scalar_value(prompt_results) or 0)
    completion_tokens = int(_scalar_value(completion_results) or 0)
    cost = _scalar_value(cost_results) or 0.0

    return CostData(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        estimated_cost_usd=round(cost, 4),
    )


def _normalize_service_name(name: str) -> str:
    """Normalize a service name so that e.g. 'node-exporter' and 'node_exporter' merge."""
    return name.replace("-", "_")


def _aggregate_by_normalized_name(raw: dict[str, int]) -> dict[str, int]:
    """Merge service counts whose names differ only by hyphens vs underscores.

    Keeps the variant with the highest count as the canonical name.
    """
    # Group by normalized key
    groups: dict[str, list[tuple[str, int]]] = {}
    for name, count in raw.items():
        key = _normalize_service_name(name)
        groups.setdefault(key, []).append((name, count))

    merged: dict[str, int] = {}
    for variants in groups.values():
        # Pick the name with the highest count as canonical
        canonical = max(variants, key=lambda x: x[1])[0]
        merged[canonical] = sum(c for _, c in variants)
    return merged


def _parse_loki_service_counts(body: dict[str, object]) -> dict[str, int]:
    """Extract per-service error counts from a Loki instant-query response."""
    raw: dict[str, int] = {}
    data = body.get("data")
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, list):
            for r in result:
                if isinstance(r, dict):
                    metric = r.get("metric", {})
                    if isinstance(metric, dict):
                        service = str(metric.get("service_name", "unknown"))
                        val = _scalar_value([r])
                        if val is not None:
                            raw[service] = int(val)
    return _aggregate_by_normalized_name(raw)


async def _collect_loki_errors(lookback_days: int) -> LokiErrorSummary | None:
    """Collect error log counts by service from Loki with previous-period comparison.

    Also fetches one representative error line per top-5 service.
    Returns None if Loki is not configured.
    """
    settings = get_settings()
    if not settings.loki_url:
        return None

    end = datetime.now(UTC)
    end_ns = int(end.timestamp() * 1e9)
    current_logql = f'sum by (service_name) (count_over_time({{detected_level=~"error|critical"}}[{lookback_days}d]))'
    previous_logql = (
        f'sum by (service_name) (count_over_time({{detected_level=~"error|critical"}}[{lookback_days}d]'
        f" offset {lookback_days}d))"
    )

    async with httpx.AsyncClient() as client:
        current_resp, previous_resp = await asyncio.gather(
            client.get(
                f"{settings.loki_url}/loki/api/v1/query",
                params={"query": current_logql, "time": str(end_ns)},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            ),
            client.get(
                f"{settings.loki_url}/loki/api/v1/query",
                params={"query": previous_logql, "time": str(end_ns)},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            ),
        )
        _ = current_resp.raise_for_status()
        current_body: dict[str, object] = current_resp.json()

        previous_by_service: dict[str, int] | None = None
        previous_total: int | None = None
        try:
            _ = previous_resp.raise_for_status()
            prev_body: dict[str, object] = previous_resp.json()
            previous_by_service = _parse_loki_service_counts(prev_body)
            previous_total = sum(previous_by_service.values())
        except Exception:
            logger.debug("Previous-period Loki query failed; omitting comparison")

    errors_by_service = _parse_loki_service_counts(current_body)
    total = sum(errors_by_service.values())

    # Fetch one representative error line per top-5 service
    error_samples = await _collect_loki_error_samples(settings.loki_url, errors_by_service, lookback_days)

    result = LokiErrorSummary(
        errors_by_service=errors_by_service,
        total_errors=total,
        error_samples=error_samples,
    )
    if previous_total is not None:
        result["previous_total_errors"] = previous_total
    if previous_by_service is not None:
        result["previous_errors_by_service"] = previous_by_service
    return result


async def _collect_loki_error_samples(
    loki_url: str,
    errors_by_service: dict[str, int],
    lookback_days: int,
) -> dict[str, str]:
    """Fetch one recent error log line per top-N service from Loki."""
    max_sample_services = 5
    top_services = sorted(errors_by_service, key=errors_by_service.get, reverse=True)[:max_sample_services]  # type: ignore[arg-type]
    if not top_services:
        return {}

    end = datetime.now(UTC)
    start_ns = str(int((end.timestamp() - lookback_days * 86400) * 1e9))
    end_ns = str(int(end.timestamp() * 1e9))

    samples: dict[str, str] = {}

    async def _fetch_sample(service: str) -> tuple[str, str]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{loki_url}/loki/api/v1/query_range",
                params={
                    "query": f'{{service_name="{service}", detected_level=~"error|critical"}}',
                    "start": start_ns,
                    "end": end_ns,
                    "limit": "1",
                    "direction": "backward",
                },
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            _ = resp.raise_for_status()
            body: dict[str, object] = resp.json()
            data = body.get("data")
            if isinstance(data, dict):
                result_list = data.get("result")
                if isinstance(result_list, list):
                    for stream in result_list:
                        if isinstance(stream, dict):
                            values = stream.get("values")
                            if isinstance(values, list) and values:
                                first_entry = values[0]
                                if isinstance(first_entry, list) and len(first_entry) >= 2:
                                    line = str(first_entry[1])[:200]
                                    return service, line
        return service, ""

    results = await asyncio.gather(*[_fetch_sample(s) for s in top_services], return_exceptions=True)
    for r in results:
        if isinstance(r, tuple):
            svc, line = r
            if line:
                samples[svc] = line
        elif isinstance(r, BaseException):
            logger.debug("Error sample fetch failed: %s", r)
    return samples


async def _collect_backup_health(lookback_days: int) -> BackupHealthData | None:
    """Collect backup health from PBS. Returns None if PBS not configured."""
    settings = get_settings()
    if not settings.pbs_url:
        return None

    _ = lookback_days  # backup health shows current state
    headers = {
        "Authorization": f"PBSAPIToken={settings.pbs_api_token}",
        "Accept": "application/json",
    }
    verify: ssl.SSLContext | bool = False
    if settings.pbs_verify_ssl:
        verify = ssl.create_default_context(cafile=settings.pbs_ca_cert) if settings.pbs_ca_cert else True

    base = f"{settings.pbs_url}/api2/json"
    stale_threshold = 86400  # 24 hours in seconds

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS, verify=verify) as client:
        ds_resp = await client.get(f"{base}/status/datastore-usage", headers=headers)
        _ = ds_resp.raise_for_status()
        ds_raw: dict[str, object] = ds_resp.json()  # pyright: ignore[reportAny]
        ds_list: list[object] = ds_raw.get("data", [])  # type: ignore[assignment]

        datastores: list[DatastoreHealth] = []
        for ds in ds_list:
            if isinstance(ds, dict):
                total = int(ds.get("total", 0))
                used = int(ds.get("used", 0))
                pct = (used / total * 100) if total > 0 else 0.0
                datastores.append(
                    DatastoreHealth(
                        store=str(ds.get("store", "unknown")),
                        total_bytes=total,
                        used_bytes=used,
                        usage_percent=round(pct, 1),
                    )
                )

        # Fetch backup groups from each datastore
        all_backups: list[BackupGroupHealth] = []
        for ds in datastores:
            try:
                groups_resp = await client.get(f"{base}/admin/datastore/{ds['store']}/groups", headers=headers)
                _ = groups_resp.raise_for_status()
                groups_raw: dict[str, object] = groups_resp.json()  # pyright: ignore[reportAny]
                groups_list: list[object] = groups_raw.get("data", [])  # type: ignore[assignment]

                now_ts = int(datetime.now(UTC).timestamp())
                for g in groups_list:
                    if isinstance(g, dict):
                        # PBS API uses hyphenated keys
                        last_ts = int(
                            g.get("last-backup", g.get("last_backup", 0))  # type: ignore[arg-type]
                        )
                        all_backups.append(
                            BackupGroupHealth(
                                backup_type=str(g.get("backup-type", g.get("backup_type", "?"))),
                                backup_id=str(g.get("backup-id", g.get("backup_id", "?"))),
                                last_backup_ts=last_ts,
                                backup_count=int(
                                    g.get("backup-count", g.get("backup_count", 0))  # type: ignore[arg-type]
                                ),
                                stale=(now_ts - last_ts) > stale_threshold if last_ts > 0 else True,
                            )
                        )
            except Exception:
                logger.debug("Failed to fetch backup groups for datastore %s", ds["store"])

    stale_count = sum(1 for b in all_backups if b["stale"])
    return BackupHealthData(
        datastores=datastores,
        backups=all_backups,
        stale_count=stale_count,
        total_count=len(all_backups),
    )


# ---------------------------------------------------------------------------
# Collect all data
# ---------------------------------------------------------------------------


async def collect_report_data(lookback_days: int) -> dict[str, object]:
    """Run all collectors concurrently, returning partial data on failures."""
    collectors = {
        "alerts": _collect_alert_summary(lookback_days),
        "slo_status": _collect_slo_status(lookback_days),
        "tool_usage": _collect_tool_usage(lookback_days),
        "cost": _collect_cost_data(lookback_days),
        "loki_errors": _collect_loki_errors(lookback_days),
        "backup_health": _collect_backup_health(lookback_days),
    }

    results: dict[str, object] = {}
    gathered = await asyncio.gather(*collectors.values(), return_exceptions=True)

    for key, result in zip(collectors.keys(), gathered, strict=True):
        if isinstance(result, BaseException):
            logger.warning("Collector %s failed: %s", key, result)
            results[key] = None
        else:
            results[key] = result

    return results


# ---------------------------------------------------------------------------
# LLM narrative
# ---------------------------------------------------------------------------


async def _generate_narrative(
    collected_data: dict[str, object],
    previous_report: str | None = None,
) -> str:
    """Generate a 2-3 paragraph executive summary via a single LLM call."""
    settings = get_settings()
    try:
        llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=SecretStr(settings.openai_api_key),
            temperature=0.3,
            base_url=settings.openai_base_url or None,
        )
        prompt = (
            "You are an SRE assistant writing a weekly reliability report summary. "
            "Given the following infrastructure data as JSON, write 3-5 concise bullet "
            "points (one line each, starting with '- '). Cover: alert status, any SLO "
            "violations (mention which components if per-component data is available), "
            "notable error trends (mention if counts are up/down vs previous period), "
            "backup health (any stale backups or storage concerns), and one actionable "
            "recommendation. Be specific with numbers. Do not use markdown bold/italic "
            "formatting. If data is missing (null), note the data source was unavailable.\n\n"
            f"Data:\n```json\n{json.dumps(collected_data, indent=2, default=str)}\n```"
        )
        if previous_report:
            # Truncate to avoid blowing up the context window
            truncated = previous_report[:3000]
            prompt += f"\n\nPrevious report for context (compare and note changes/trends):\n```\n{truncated}\n```"
        response = await llm.ainvoke(prompt)
        return str(response.content)
    except Exception:
        logger.exception("Failed to generate narrative")
        return "Narrative unavailable — LLM call failed."


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------


def _format_slo_row(name: str, target: str, actual: float | None, higher_is_better: bool = True) -> list[str]:
    """Build a list of cell values for one SLO row: [name, target, actual, status]."""
    if actual is None:
        return [name, target, "N/A", "-"]
    # Format actual to match target's unit for readability
    if "%" in target:
        actual_str = f"{actual * 100:.2f}%"
    elif "s" in target:
        actual_str = f"{actual:.2f}s"
    else:
        actual_str = f"{actual:.4f}" if actual < 1 else f"{actual:.2f}"
    try:
        target_val = float(target.rstrip("%s").replace(">", "").replace("<", "").strip())
        # Normalize: if target is percentage like "99%", compare actual*100
        compare_actual = actual * 100 if "%" in target else actual
        passed = compare_actual >= target_val if higher_is_better else compare_actual <= target_val
        status = "PASS" if passed else "FAIL"
    except (ValueError, TypeError):
        status = "-"
    return [name, target, actual_str, status]


def format_report_markdown(data: ReportData) -> str:
    """Convert structured ReportData into a readable markdown report."""
    lines: list[str] = []
    lines.append("# Weekly Reliability Report")
    lines.append("")
    lines.append(f"**Generated:** {data['generated_at']}")
    lines.append(f"**Lookback:** {data['lookback_days']} days")
    lines.append("")

    # 1. Executive Summary
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(data["narrative"])
    lines.append("")

    # 2. Alert Summary
    lines.append("## Alert Summary")
    lines.append("")
    alerts = data.get("alerts")
    if alerts is None:
        lines.append("*Alert data unavailable.*")
    else:
        lines.append(f"- **Total alert rules:** {alerts['total_rules']}")
        lines.append(f"- **Currently active:** {alerts['active_alerts']}")
        if alerts["alerts_by_severity"]:
            severity_parts = [f"{sev}: {count}" for sev, count in sorted(alerts["alerts_by_severity"].items())]
            lines.append(f"- **By severity:** {', '.join(severity_parts)}")
        if alerts["active_alert_names"]:
            lines.append(f"- **Active alerts:** {', '.join(alerts['active_alert_names'])}")
    lines.append("")

    # 3. SLO Status
    lines.append("## SLO Status")
    lines.append("")
    slo = data.get("slo_status")
    if slo is None:
        lines.append("*SLO data unavailable.*")
    else:
        slo_rows = [
            _format_slo_row("P95 Latency", "< 15s", slo["p95_latency_seconds"], higher_is_better=False),
            _format_slo_row("Tool Success Rate", "> 99%", slo["tool_success_rate"]),
            _format_slo_row("LLM Error Rate", "< 1%", slo["llm_error_rate"], higher_is_better=False),
            _format_slo_row("Availability", "> 99.5%", slo["availability"]),
        ]
        lines.append(
            _format_plain_table(
                ["Metric", "Target", "Actual", "Status"],
                slo_rows,
                right_align={2},
            )
        )
        # Per-component availability breakdown
        comp_avail = slo.get("component_availability", {})
        if comp_avail:
            lines.append("")
            degraded = {k: v for k, v in comp_avail.items() if v < 1.0}
            if degraded:
                lines.append("Components with degraded availability:")
                for comp, val in sorted(degraded.items(), key=lambda x: x[1]):
                    lines.append(f"  - {comp}: {val * 100:.2f}%")
            else:
                lines.append("All components at 100% availability.")
    lines.append("")

    # 4. Tool Usage
    lines.append("## Tool Usage")
    lines.append("")
    usage = data.get("tool_usage")
    if usage is None:
        lines.append("*Tool usage data unavailable.*")
    else:
        if usage["tool_calls"]:
            active = {k: v for k, v in usage["tool_calls"].items() if v > 0}
            inactive_count = len(usage["tool_calls"]) - len(active)
            if active:
                tool_rows: list[list[str]] = []
                for tool_name, calls in sorted(active.items(), key=lambda x: x[1], reverse=True):
                    errors = usage["tool_errors"].get(tool_name, 0)
                    err_rate = f"{errors / calls * 100:.1f}%" if calls > 0 else "0.0%"
                    tool_rows.append([tool_name, str(calls), str(errors), err_rate])
                lines.append(
                    _format_plain_table(
                        ["Tool", "Calls", "Errors", "Error Rate"],
                        tool_rows,
                        right_align={1, 2, 3},
                    )
                )
                if inactive_count > 0:
                    lines.append("")
                    lines.append(f"{inactive_count} registered tools had no calls this period.")
            else:
                lines.append("*No tool calls recorded in this period.*")
        else:
            lines.append("*No tool calls recorded in this period.*")
    lines.append("")

    # 5. Cost & Token Usage
    lines.append("## Cost & Token Usage")
    lines.append("")
    cost = data.get("cost")
    if cost is None:
        lines.append("*Cost data unavailable.*")
    else:
        lines.append(f"- **Prompt tokens:** {cost['prompt_tokens']:,}")
        lines.append(f"- **Completion tokens:** {cost['completion_tokens']:,}")
        lines.append(f"- **Total tokens:** {cost['total_tokens']:,}")
        lines.append(f"- **Estimated cost:** ${cost['estimated_cost_usd']:.4f}")
    lines.append("")

    # 6. Log Error Summary (if Loki configured)
    loki = data.get("loki_errors")
    if loki is not None:
        lines.append("## Log Error Summary")
        lines.append("")
        if loki["errors_by_service"]:
            # Total with week-over-week delta
            total_str = f"**Total errors/critical logs:** {loki['total_errors']}"
            prev_total = loki.get("previous_total_errors")
            if prev_total is not None:
                delta = loki["total_errors"] - prev_total
                if delta > 0:
                    pct = (delta / prev_total * 100) if prev_total > 0 else 0
                    total_str += f" (up {delta:,} / {pct:.0f}% from previous period)"
                elif delta < 0:
                    pct = (abs(delta) / prev_total * 100) if prev_total > 0 else 0
                    total_str += f" (down {abs(delta):,} / {pct:.0f}% from previous period)"
                else:
                    total_str += " (unchanged from previous period)"
            lines.append(total_str)
            lines.append("")

            # Per-service table with delta column if previous data available
            max_loki_rows = 10
            sorted_services = sorted(loki["errors_by_service"].items(), key=lambda x: x[1], reverse=True)
            shown = sorted_services[:max_loki_rows]
            remaining = sorted_services[max_loki_rows:]

            prev_by_service = loki.get("previous_errors_by_service")
            if prev_by_service is not None:
                loki_rows = []
                for service, count in shown:
                    prev_count = prev_by_service.get(service, 0)
                    delta = count - prev_count
                    delta_str = f"+{delta}" if delta > 0 else str(delta)
                    if prev_count == 0 and count > 0:
                        delta_str = "new"
                    loki_rows.append([service, str(count), delta_str])
                lines.append(
                    _format_plain_table(
                        ["Service", "Errors", "vs Prev"],
                        loki_rows,
                        right_align={1, 2},
                    )
                )
            else:
                loki_rows_simple = [[service, str(count)] for service, count in shown]
                lines.append(
                    _format_plain_table(
                        ["Service", "Errors"],
                        loki_rows_simple,
                        right_align={1},
                    )
                )
            if remaining:
                remaining_total = sum(c for _, c in remaining)
                lines.append(f"+ {len(remaining)} more services ({remaining_total} errors)")

            # Error samples — one representative line per top service
            samples = loki.get("error_samples", {})
            if samples:
                lines.append("")
                lines.append("Top error samples:")
                for service, sample in samples.items():
                    lines.append(f"  {service}: {sample}")
        else:
            lines.append("*No error/critical logs recorded in this period.*")
        lines.append("")

    # 7. Backup Health (if PBS configured)
    backup = data.get("backup_health")
    if backup is not None:
        lines.append("## Backup Health")
        lines.append("")
        # Datastore usage
        if backup["datastores"]:
            for ds in backup["datastores"]:
                total_tib = ds["total_bytes"] / (1024**4)
                used_tib = ds["used_bytes"] / (1024**4)
                lines.append(
                    f"- **{ds['store']}:** {used_tib:.1f} / {total_tib:.1f} TiB ({ds['usage_percent']:.1f}% used)"
                )
        # Backup freshness
        if backup["backups"]:
            lines.append(f"- **Backup groups:** {backup['total_count']} total, {backup['stale_count']} stale (>24h)")
            stale = [b for b in backup["backups"] if b["stale"]]
            if stale:
                lines.append("")
                lines.append("Stale backups (last backup >24h ago):")
                type_labels = {"vm": "VM", "ct": "CT", "host": "Host"}
                for b in sorted(stale, key=lambda x: x["last_backup_ts"]):
                    label = type_labels.get(b["backup_type"], b["backup_type"])
                    age_h = (int(datetime.now(UTC).timestamp()) - b["last_backup_ts"]) / 3600
                    lines.append(f"  - {label}/{b['backup_id']}: {age_h:.0f}h ago ({b['backup_count']} snapshots)")
            else:
                lines.append("- All backups are fresh (<24h).")
        else:
            lines.append("*No backup groups found.*")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


async def generate_report(lookback_days: int | None = None) -> str:
    """Generate a full weekly reliability report as markdown.

    After generation, archives the report to the memory store (if configured)
    and triggers baseline computation. Loads the previous report to provide
    context for the LLM narrative.

    Args:
        lookback_days: Number of days to look back. Defaults to settings value.

    Returns:
        Markdown-formatted report string.
    """
    settings = get_settings()
    days = lookback_days if lookback_days is not None else settings.report_lookback_days

    collected = await collect_report_data(days)

    # Load previous report for narrative context (if memory configured)
    previous_report = _load_previous_report()

    narrative = await _generate_narrative(collected, previous_report=previous_report)

    report_data = ReportData(
        generated_at=datetime.now(UTC).isoformat(),
        lookback_days=days,
        alerts=collected.get("alerts"),  # type: ignore[typeddict-item]
        slo_status=collected.get("slo_status"),  # type: ignore[typeddict-item]
        tool_usage=collected.get("tool_usage"),  # type: ignore[typeddict-item]
        cost=collected.get("cost"),  # type: ignore[typeddict-item]
        loki_errors=collected.get("loki_errors"),  # type: ignore[typeddict-item]
        backup_health=collected.get("backup_health"),  # type: ignore[typeddict-item]
        narrative=narrative,
    )

    markdown = format_report_markdown(report_data)

    # Archive report and compute baselines (non-blocking, best-effort)
    _archive_report(report_data, markdown)
    await _compute_post_report_baselines(days)

    return markdown


def _load_previous_report() -> str | None:
    """Load the most recent archived report for narrative context.

    Returns None if memory is not configured or no previous report exists.
    """
    try:
        from src.memory.store import get_initialized_connection, get_latest_report, is_memory_configured

        if not is_memory_configured():
            return None
        conn = get_initialized_connection()
        try:
            report = get_latest_report(conn)
            if report is None:
                return None
            return report["report_markdown"]
        finally:
            conn.close()
    except Exception:
        logger.debug("Could not load previous report from memory store")
        return None


def _archive_report(report_data: ReportData, markdown: str) -> None:
    """Save the report to the memory store (best-effort, never raises)."""
    try:
        from src.memory.store import (
            _extract_report_metrics,
            get_initialized_connection,
            is_memory_configured,
            save_report,
        )

        if not is_memory_configured():
            return
        data_json = json.dumps(dict(report_data), default=str)
        metrics = _extract_report_metrics(data_json)
        conn = get_initialized_connection()
        try:
            save_report(
                conn,
                generated_at=report_data["generated_at"],
                lookback_days=report_data["lookback_days"],
                report_markdown=markdown,
                report_data=data_json,
                active_alerts=int(metrics.get("active_alerts", 0)),
                slo_failures=int(metrics.get("slo_failures", 0)),
                total_log_errors=int(metrics.get("total_log_errors", 0)),
                estimated_cost=float(metrics.get("estimated_cost", 0.0)),
            )
            logger.info("Report archived to memory store")
        finally:
            conn.close()
    except Exception:
        logger.debug("Failed to archive report to memory store")


async def _compute_post_report_baselines(lookback_days: int) -> None:
    """Compute and store metric baselines after report generation (best-effort)."""
    try:
        from src.memory.baselines import compute_and_store_baselines

        count = await compute_and_store_baselines(lookback_days)
        if count > 0:
            logger.info("Stored %d metric baselines", count)
    except Exception:
        logger.debug("Failed to compute/store baselines")
