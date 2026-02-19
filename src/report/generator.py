"""Weekly reliability report generator.

Queries Prometheus, Grafana, and Loki APIs directly (not through the LangChain
agent) to collect structured data, then makes a single LLM call for a narrative
summary.  Each collector is independent and wrapped in try/except so a partial
report is always produced.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TypedDict

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


class ReportData(TypedDict):
    generated_at: str
    lookback_days: int
    alerts: AlertSummaryData | None
    slo_status: SLOStatusData | None
    tool_usage: ToolUsageData | None
    cost: CostData | None
    loki_errors: LokiErrorSummary | None
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

        # Availability: component_healthy averaged
        avail_results = await _prom_query(client, url, f"avg_over_time(sre_assistant_component_healthy[{window}])")
        availability: float | None = None
        if avail_results:
            values = [_scalar_value([r]) for r in avail_results]
            non_none = [v for v in values if v is not None]
            if non_none:
                availability = sum(non_none) / len(non_none)

    return SLOStatusData(
        p95_latency_seconds=p95,
        tool_success_rate=tool_success,
        llm_error_rate=llm_error_rate,
        availability=availability,
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
            client, url, f'increase(sre_assistant_llm_token_usage{{type="prompt"}}[{window}])'
        )
        completion_results = await _prom_query(
            client, url, f'increase(sre_assistant_llm_token_usage{{type="completion"}}[{window}])'
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


async def _collect_loki_errors(lookback_days: int) -> LokiErrorSummary | None:
    """Collect error log counts by service from Loki. Returns None if Loki not configured."""
    settings = get_settings()
    if not settings.loki_url:
        return None

    end = datetime.now(UTC)
    end_ns = int(end.timestamp() * 1e9)
    logql = f'sum by (service_name) (count_over_time({{detected_level=~"error|critical"}}[{lookback_days}d]))'

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.loki_url}/loki/api/v1/query",
            params={
                "query": logql,
                "time": str(end_ns),
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        _ = resp.raise_for_status()
        body: dict[str, object] = resp.json()

    errors_by_service: dict[str, int] = {}
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
                            errors_by_service[service] = int(val)

    total = sum(errors_by_service.values())
    return LokiErrorSummary(errors_by_service=errors_by_service, total_errors=total)


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


async def _generate_narrative(collected_data: dict[str, object]) -> str:
    """Generate a 2-3 paragraph executive summary via a single LLM call."""
    settings = get_settings()
    try:
        llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=SecretStr(settings.openai_api_key),
            temperature=0.3,
        )
        prompt = (
            "You are an SRE assistant generating a weekly reliability report narrative. "
            "Given the following collected infrastructure data as JSON, write a concise "
            "2-3 paragraph executive summary highlighting: key findings, any active alerts "
            "or SLO violations, notable trends in tool usage or errors, and recommendations. "
            "Be factual and specific. If data is missing (null), note that the data source "
            "was unavailable.\n\n"
            f"Data:\n```json\n{json.dumps(collected_data, indent=2, default=str)}\n```"
        )
        response = await llm.ainvoke(prompt)
        return str(response.content)
    except Exception:
        logger.exception("Failed to generate narrative")
        return "Narrative unavailable — LLM call failed."


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------


def _format_slo_row(name: str, target: str, actual: float | None, higher_is_better: bool = True) -> str:
    """Format a single SLO table row with pass/fail indicator."""
    if actual is None:
        return f"| {name} | {target} | N/A | - |"
    actual_str = f"{actual:.4f}" if actual < 1 else f"{actual:.2f}"
    try:
        target_val = float(target.rstrip("%s").replace(">", "").replace("<", "").strip())
        # Normalize: if target is percentage like "99%", compare actual*100
        compare_actual = actual * 100 if "%" in target else actual
        passed = compare_actual >= target_val if higher_is_better else compare_actual <= target_val
        status = "PASS" if passed else "FAIL"
    except (ValueError, TypeError):
        status = "-"
    return f"| {name} | {target} | {actual_str} | {status} |"


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
        lines.append("| Metric | Target | Actual | Status |")
        lines.append("|--------|--------|--------|--------|")
        lines.append(_format_slo_row("P95 Latency", "< 15s", slo["p95_latency_seconds"], higher_is_better=False))
        lines.append(_format_slo_row("Tool Success Rate", "> 99%", slo["tool_success_rate"]))
        lines.append(_format_slo_row("LLM Error Rate", "< 1%", slo["llm_error_rate"], higher_is_better=False))
        lines.append(_format_slo_row("Availability", "> 99.5%", slo["availability"]))
    lines.append("")

    # 4. Tool Usage
    lines.append("## Tool Usage")
    lines.append("")
    usage = data.get("tool_usage")
    if usage is None:
        lines.append("*Tool usage data unavailable.*")
    else:
        if usage["tool_calls"]:
            lines.append("| Tool | Calls | Errors | Error Rate |")
            lines.append("|------|-------|--------|------------|")
            for tool_name, calls in sorted(usage["tool_calls"].items(), key=lambda x: x[1], reverse=True):
                errors = usage["tool_errors"].get(tool_name, 0)
                err_rate = f"{errors / calls * 100:.1f}%" if calls > 0 else "0.0%"
                lines.append(f"| {tool_name} | {calls} | {errors} | {err_rate} |")
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
            lines.append(f"**Total errors/critical logs:** {loki['total_errors']}")
            lines.append("")
            lines.append("| Service | Error Count |")
            lines.append("|---------|-------------|")
            for service, count in sorted(loki["errors_by_service"].items(), key=lambda x: x[1], reverse=True):
                lines.append(f"| {service} | {count} |")
        else:
            lines.append("*No error/critical logs recorded in this period.*")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


async def generate_report(lookback_days: int | None = None) -> str:
    """Generate a full weekly reliability report as markdown.

    Args:
        lookback_days: Number of days to look back. Defaults to settings value.

    Returns:
        Markdown-formatted report string.
    """
    settings = get_settings()
    days = lookback_days if lookback_days is not None else settings.report_lookback_days

    collected = await collect_report_data(days)
    narrative = await _generate_narrative(collected)

    report_data = ReportData(
        generated_at=datetime.now(UTC).isoformat(),
        lookback_days=days,
        alerts=collected.get("alerts"),  # type: ignore[typeddict-item]
        slo_status=collected.get("slo_status"),  # type: ignore[typeddict-item]
        tool_usage=collected.get("tool_usage"),  # type: ignore[typeddict-item]
        cost=collected.get("cost"),  # type: ignore[typeddict-item]
        loki_errors=collected.get("loki_errors"),  # type: ignore[typeddict-item]
        narrative=narrative,
    )

    return format_report_markdown(report_data)
