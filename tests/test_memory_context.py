"""Unit tests for memory context functions — build-time context, enrichment, suggestions."""

import sqlite3

from src.memory.context import (
    detect_incident_suggestion,
    enrich_alerts_with_incident_history,
    enrich_with_baseline_context,
    get_open_incidents_context,
    get_recent_patterns_context,
)
from src.memory.models import BaselineRecord
from src.memory.store import (
    get_connection,
    init_schema,
    save_baselines,
    save_incident,
    save_query_pattern,
)


def _make_conn() -> sqlite3.Connection:
    """Create an in-memory SQLite connection with schema initialized."""
    conn = get_connection(":memory:")
    init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Open incidents context
# ---------------------------------------------------------------------------


class TestOpenIncidentsContext:
    def test_returns_empty_when_not_configured(self) -> None:
        """When memory is not configured, returns empty string."""
        # Default mock_settings has memory_db_path="" so is_memory_configured=False
        # But we're calling without mock_settings, so we need to patch
        from unittest.mock import patch

        with patch("src.memory.context.is_memory_configured", return_value=False):
            result = get_open_incidents_context()
        assert result == ""

    def test_returns_empty_when_no_incidents(self) -> None:
        conn = _make_conn()
        from unittest.mock import patch

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            result = get_open_incidents_context()
        assert result == ""

    def test_returns_formatted_incidents(self) -> None:
        conn = _make_conn()
        save_incident(conn, title="HighCPU on media", description="...", severity="warning", services="media,jellyfin")
        save_incident(conn, title="DiskFull on infra", description="...", severity="critical", services="infra")

        from unittest.mock import patch

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            result = get_open_incidents_context()

        assert "Active Incidents" in result
        assert "HighCPU on media" in result
        assert "DiskFull on infra" in result
        assert "warning" in result
        assert "critical" in result
        assert "[media,jellyfin]" in result

    def test_limits_to_max_incidents(self) -> None:
        conn = _make_conn()
        for i in range(10):
            save_incident(conn, title=f"Incident {i}", description="...", severity="info")

        from unittest.mock import patch

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            result = get_open_incidents_context()

        assert "5 more open incidents" in result

    def test_graceful_on_error(self) -> None:
        from unittest.mock import patch

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", side_effect=RuntimeError("db error")),
        ):
            result = get_open_incidents_context()
        assert result == ""


# ---------------------------------------------------------------------------
# Recent patterns context
# ---------------------------------------------------------------------------


class TestRecentPatternsContext:
    def test_returns_empty_when_not_configured(self) -> None:
        from unittest.mock import patch

        with patch("src.memory.context.is_memory_configured", return_value=False):
            result = get_recent_patterns_context()
        assert result == ""

    def test_returns_empty_when_no_patterns(self) -> None:
        conn = _make_conn()
        from unittest.mock import patch

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            result = get_recent_patterns_context()
        assert result == ""

    def test_returns_formatted_patterns(self) -> None:
        conn = _make_conn()
        save_query_pattern(conn, question="What CPU is media using?", tool_names="prometheus_instant_query")
        save_query_pattern(conn, question="Are there any alerts?", tool_names="grafana_get_alerts")

        from unittest.mock import patch

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            result = get_recent_patterns_context()

        assert "Recent User Questions" in result
        assert "What CPU is media using?" in result
        assert "prometheus_instant_query" in result


# ---------------------------------------------------------------------------
# Alert incident history enrichment
# ---------------------------------------------------------------------------


class TestEnrichAlertsWithIncidentHistory:
    def test_returns_original_when_not_configured(self) -> None:
        from unittest.mock import patch

        with patch("src.memory.context.is_memory_configured", return_value=False):
            result = enrich_alerts_with_incident_history("Alert output", ["HighCPU"])
        assert result == "Alert output"

    def test_returns_original_when_no_alert_names(self) -> None:
        result = enrich_alerts_with_incident_history("Alert output", [])
        assert result == "Alert output"

    def test_enriches_with_history(self) -> None:
        conn = _make_conn()
        save_incident(
            conn,
            title="Past CPU spike",
            description="CPU hit 95%",
            alert_name="HighCPU",
            root_cause="Transcoding overload",
        )

        from unittest.mock import patch

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            result = enrich_alerts_with_incident_history("Found 1 alert(s)", ["HighCPU"])

        assert "Found 1 alert(s)" in result
        assert "Incident History from Memory" in result
        assert "Past CPU spike" in result
        assert "Transcoding overload" in result

    def test_no_history_found(self) -> None:
        conn = _make_conn()

        from unittest.mock import patch

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            result = enrich_alerts_with_incident_history("Found 1 alert(s)", ["UnknownAlert"])

        # Should return original without history section
        assert result == "Found 1 alert(s)"

    def test_deduplicates_alert_names(self) -> None:
        conn = _make_conn()
        save_incident(conn, title="CPU incident", description="...", alert_name="HighCPU")

        from unittest.mock import patch

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            result = enrich_alerts_with_incident_history("Alerts", ["HighCPU", "HighCPU", "HighCPU"])

        # Should only show one "Past incidents for 'HighCPU'" section
        assert result.count("Past incidents for 'HighCPU'") == 1


# ---------------------------------------------------------------------------
# Baseline enrichment
# ---------------------------------------------------------------------------


class TestEnrichWithBaselineContext:
    def test_returns_original_when_not_configured(self) -> None:
        from unittest.mock import patch

        with patch("src.memory.context.is_memory_configured", return_value=False):
            result = enrich_with_baseline_context("Result data", ["node_cpu"])
        assert result == "Result data"

    def test_returns_original_when_no_metric_names(self) -> None:
        result = enrich_with_baseline_context("Result data", [])
        assert result == "Result data"

    def test_enriches_with_baseline(self) -> None:
        conn = _make_conn()
        save_baselines(
            conn,
            [
                BaselineRecord(
                    id=0,
                    metric_name="node_cpu_usage_ratio",
                    labels="{}",
                    avg_value=0.45,
                    p95_value=0.85,
                    min_value=0.10,
                    max_value=0.95,
                    sample_count=100,
                    window_days=7,
                    computed_at="2026-02-19T08:00:00+00:00",
                )
            ],
        )

        from unittest.mock import patch

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            result = enrich_with_baseline_context("", ["node_cpu_usage_ratio"])

        assert "Baseline context" in result
        assert "avg=0.45" in result
        assert "p95=0.85" in result

    def test_no_baseline_found(self) -> None:
        conn = _make_conn()

        from unittest.mock import patch

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            result = enrich_with_baseline_context("Result data", ["unknown_metric"])

        assert result == "Result data"


# ---------------------------------------------------------------------------
# Incident suggestion detection
# ---------------------------------------------------------------------------


class TestDetectIncidentSuggestion:
    def test_returns_empty_when_not_configured(self) -> None:
        from unittest.mock import patch

        with patch("src.memory.context.is_memory_configured", return_value=False):
            result = detect_incident_suggestion(["grafana_get_alerts"], "The root cause was a config error.")
        assert result == ""

    def test_returns_empty_when_no_investigation_tools(self) -> None:
        from unittest.mock import patch

        with patch("src.memory.context.is_memory_configured", return_value=True):
            result = detect_incident_suggestion(
                ["prometheus_instant_query"],
                "The root cause was a config error.",
            )
        assert result == ""

    def test_returns_empty_when_no_outcome_keywords(self) -> None:
        from unittest.mock import patch

        with patch("src.memory.context.is_memory_configured", return_value=True):
            result = detect_incident_suggestion(
                ["grafana_get_alerts"],
                "There are 2 alerts firing.",
            )
        assert result == ""

    def test_returns_suggestion_when_both_conditions_met(self) -> None:
        from unittest.mock import patch

        with patch("src.memory.context.is_memory_configured", return_value=True):
            result = detect_incident_suggestion(
                ["grafana_get_alerts", "loki_query_logs"],
                "The root cause was a misconfigured DNS entry. Fixed by updating CoreDNS config.",
            )

        assert "memory_record_incident" in result
        assert "investigation" in result.lower()

    def test_detects_loki_correlate_changes(self) -> None:
        from unittest.mock import patch

        with patch("src.memory.context.is_memory_configured", return_value=True):
            result = detect_incident_suggestion(
                ["loki_correlate_changes"],
                "The issue was caused by a failed container restart.",
            )

        assert "memory_record_incident" in result

    def test_detects_memory_search_incidents(self) -> None:
        from unittest.mock import patch

        with patch("src.memory.context.is_memory_configured", return_value=True):
            result = detect_incident_suggestion(
                ["memory_search_incidents"],
                "The problem was resolved by restarting the service.",
            )

        assert "memory_record_incident" in result
