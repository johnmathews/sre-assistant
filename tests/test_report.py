"""Unit tests for the report module — pure function tests, no I/O."""

from typing import Any

from src.report.email import is_email_configured
from src.report.generator import (
    AlertSummaryData,
    CostData,
    LokiErrorSummary,
    ReportData,
    SLOStatusData,
    ToolUsageData,
    _format_slo_row,
    format_report_markdown,
)


def _complete_report_data() -> ReportData:
    """Return a complete ReportData fixture with all sections populated."""
    return ReportData(
        generated_at="2026-02-19T08:00:00+00:00",
        lookback_days=7,
        narrative="Everything looks healthy this week. No SLO violations detected.",
        alerts=AlertSummaryData(
            total_rules=12,
            active_alerts=2,
            alerts_by_severity={"critical": 1, "warning": 1},
            active_alert_names=["HighCPU", "DiskSpaceLow"],
        ),
        slo_status=SLOStatusData(
            p95_latency_seconds=3.2,
            tool_success_rate=0.997,
            llm_error_rate=0.005,
            availability=0.998,
        ),
        tool_usage=ToolUsageData(
            tool_calls={"prometheus_query": 150, "grafana_alerts": 45},
            tool_errors={"prometheus_query": 2},
        ),
        cost=CostData(
            prompt_tokens=50000,
            completion_tokens=15000,
            total_tokens=65000,
            estimated_cost_usd=0.1234,
        ),
        loki_errors=LokiErrorSummary(
            errors_by_service={"traefik": 120, "jellyfin": 5},
            total_errors=125,
        ),
    )


class TestFormatReportMarkdown:
    def test_complete_report_has_all_sections(self) -> None:
        data = _complete_report_data()
        md = format_report_markdown(data)

        assert "# Weekly Reliability Report" in md
        assert "## Executive Summary" in md
        assert "## Alert Summary" in md
        assert "## SLO Status" in md
        assert "## Tool Usage" in md
        assert "## Cost & Token Usage" in md
        assert "## Log Error Summary" in md

    def test_complete_report_includes_alert_details(self) -> None:
        data = _complete_report_data()
        md = format_report_markdown(data)

        assert "Total alert rules:** 12" in md
        assert "Currently active:** 2" in md
        assert "HighCPU" in md
        assert "critical: 1" in md

    def test_complete_report_includes_slo_table(self) -> None:
        data = _complete_report_data()
        md = format_report_markdown(data)

        assert "P95 Latency" in md
        assert "Tool Success Rate" in md
        assert "PASS" in md

    def test_complete_report_includes_tool_usage(self) -> None:
        data = _complete_report_data()
        md = format_report_markdown(data)

        assert "prometheus_query" in md
        assert "150" in md

    def test_complete_report_includes_cost(self) -> None:
        data = _complete_report_data()
        md = format_report_markdown(data)

        assert "50,000" in md
        assert "$0.1234" in md

    def test_complete_report_includes_loki_errors(self) -> None:
        data = _complete_report_data()
        md = format_report_markdown(data)

        assert "traefik" in md
        assert "120" in md
        assert "Total errors/critical logs:** 125" in md

    def test_partial_data_none_sections(self) -> None:
        data = ReportData(
            generated_at="2026-02-19T08:00:00+00:00",
            lookback_days=7,
            narrative="Partial data only.",
            alerts=None,
            slo_status=None,
            tool_usage=None,
            cost=None,
            loki_errors=None,
        )
        md = format_report_markdown(data)

        assert "Alert data unavailable" in md
        assert "SLO data unavailable" in md
        assert "Tool usage data unavailable" in md
        assert "Cost data unavailable" in md
        # Loki section should be omitted entirely when None
        assert "Log Error Summary" not in md

    def test_empty_tool_calls(self) -> None:
        data = _complete_report_data()
        data["tool_usage"] = ToolUsageData(tool_calls={}, tool_errors={})
        md = format_report_markdown(data)

        assert "No tool calls recorded" in md

    def test_no_active_alerts(self) -> None:
        data = _complete_report_data()
        data["alerts"] = AlertSummaryData(
            total_rules=10,
            active_alerts=0,
            alerts_by_severity={},
            active_alert_names=[],
        )
        md = format_report_markdown(data)

        assert "Currently active:** 0" in md
        assert "Active alerts:" not in md


class TestFormatSloRow:
    def test_pass_higher_is_better(self) -> None:
        row = _format_slo_row("Tool Success Rate", "> 99%", 0.997)
        assert "PASS" in row

    def test_fail_higher_is_better(self) -> None:
        row = _format_slo_row("Tool Success Rate", "> 99%", 0.98)
        assert "FAIL" in row

    def test_pass_lower_is_better(self) -> None:
        row = _format_slo_row("P95 Latency", "< 15s", 3.2, higher_is_better=False)
        assert "PASS" in row

    def test_fail_lower_is_better(self) -> None:
        row = _format_slo_row("P95 Latency", "< 15s", 20.0, higher_is_better=False)
        assert "FAIL" in row

    def test_none_value(self) -> None:
        row = _format_slo_row("Availability", "> 99.5%", None)
        assert "N/A" in row


class TestIsEmailConfigured:
    def test_all_fields_set(self, mock_settings: Any) -> None:
        assert is_email_configured() is True

    def test_missing_host(self, mock_settings: Any) -> None:
        mock_settings.smtp_host = ""
        assert is_email_configured() is False

    def test_missing_username(self, mock_settings: Any) -> None:
        mock_settings.smtp_username = ""
        assert is_email_configured() is False

    def test_missing_password(self, mock_settings: Any) -> None:
        mock_settings.smtp_password = ""
        assert is_email_configured() is False

    def test_missing_recipient(self, mock_settings: Any) -> None:
        mock_settings.report_recipient_email = ""
        assert is_email_configured() is False
