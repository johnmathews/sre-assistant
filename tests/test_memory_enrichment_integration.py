"""Integration tests for memory enrichment in tools and agent post-response actions."""

import sqlite3
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from src.agent.agent import _extract_tool_names, _get_memory_context, _post_response_actions
from src.memory.models import BaselineRecord
from src.memory.store import (
    get_connection,
    get_recent_query_patterns,
    init_schema,
    save_baselines,
    save_incident,
)

pytestmark = pytest.mark.integration


def _make_conn() -> sqlite3.Connection:
    conn = get_connection(":memory:")
    init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Tool name extraction
# ---------------------------------------------------------------------------


class TestExtractToolNames:
    def test_extracts_from_ai_messages(self) -> None:
        messages = [
            AIMessage(
                content="Let me check that.",
                tool_calls=[
                    {"name": "grafana_get_alerts", "args": {}, "id": "1"},
                    {"name": "prometheus_instant_query", "args": {"query": "up"}, "id": "2"},
                ],
            ),
            AIMessage(content="Here are the results."),
        ]
        names = _extract_tool_names(messages)
        assert names == ["grafana_get_alerts", "prometheus_instant_query"]

    def test_empty_messages(self) -> None:
        assert _extract_tool_names([]) == []

    def test_no_tool_calls(self) -> None:
        messages = [AIMessage(content="Hello")]
        assert _extract_tool_names(messages) == []


# ---------------------------------------------------------------------------
# Memory context for system prompt
# ---------------------------------------------------------------------------


class TestGetMemoryContext:
    def test_returns_empty_when_not_configured(self, mock_settings: Any) -> None:
        mock_settings.memory_db_path = ""
        result = _get_memory_context()
        assert result == ""

    def test_returns_context_with_incidents(self, mock_settings: Any, tmp_path: Any) -> None:
        db_path = str(tmp_path / "test.db")
        mock_settings.memory_db_path = db_path
        conn = get_connection(db_path)
        init_schema(conn)
        save_incident(conn, title="HighCPU on media", description="...", severity="warning")
        conn.close()

        result = _get_memory_context()
        assert "Active Incidents" in result
        assert "HighCPU on media" in result


# ---------------------------------------------------------------------------
# Post-response actions
# ---------------------------------------------------------------------------


class TestPostResponseActions:
    def test_returns_empty_when_not_configured(self, mock_settings: Any) -> None:
        mock_settings.memory_db_path = ""
        messages = [
            AIMessage(
                content="The root cause was X.",
                tool_calls=[{"name": "grafana_get_alerts", "args": {}, "id": "1"}],
            ),
        ]
        result = _post_response_actions(messages, "What alerts?", "The root cause was X.")
        assert result == ""

    def test_saves_query_pattern(self, mock_settings: Any, tmp_path: Any) -> None:
        db_path = str(tmp_path / "test.db")
        mock_settings.memory_db_path = db_path
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        messages = [
            AIMessage(
                content="CPU is 45%.",
                tool_calls=[{"name": "prometheus_instant_query", "args": {"query": "up"}, "id": "1"}],
            ),
        ]
        _post_response_actions(messages, "What is the CPU?", "CPU is 45%.")

        verify_conn = get_connection(db_path)
        init_schema(verify_conn)
        patterns = get_recent_query_patterns(verify_conn, limit=10)
        verify_conn.close()

        assert len(patterns) == 1
        assert patterns[0]["question"] == "What is the CPU?"
        assert "prometheus_instant_query" in patterns[0]["tool_names"]

    def test_suggests_incident_when_warranted(self, mock_settings: Any, tmp_path: Any) -> None:
        db_path = str(tmp_path / "test.db")
        mock_settings.memory_db_path = db_path
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        messages = [
            AIMessage(
                content="The root cause was a DNS misconfiguration.",
                tool_calls=[{"name": "grafana_get_alerts", "args": {}, "id": "1"}],
            ),
        ]
        result = _post_response_actions(
            messages,
            "Why is DNS failing?",
            "The root cause was a DNS misconfiguration.",
        )

        assert "memory_record_incident" in result

    def test_no_suggestion_for_normal_queries(self, mock_settings: Any, tmp_path: Any) -> None:
        db_path = str(tmp_path / "test.db")
        mock_settings.memory_db_path = db_path
        conn = get_connection(db_path)
        init_schema(conn)
        conn.close()

        messages = [
            AIMessage(
                content="CPU is at 45%.",
                tool_calls=[{"name": "prometheus_instant_query", "args": {"query": "up"}, "id": "1"}],
            ),
        ]
        result = _post_response_actions(messages, "What is CPU?", "CPU is at 45%.")

        # No investigation tools + no outcome keywords = no suggestion
        assert result == ""


# ---------------------------------------------------------------------------
# Prometheus baseline enrichment
# ---------------------------------------------------------------------------


class TestPrometheusBaselineEnrichment:
    def test_enriches_with_baseline(self, mock_settings: Any) -> None:
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

        from src.agent.tools.prometheus import _get_baseline_enrichment

        data = {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {"__name__": "node_cpu_usage_ratio", "hostname": "media"}, "value": [1, "0.72"]}],
            },
        }

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            result = _get_baseline_enrichment(data)  # type: ignore[arg-type]

        assert "Baseline context" in result
        assert "avg=0.45" in result

    def test_returns_empty_when_not_configured(self, mock_settings: Any) -> None:
        from src.agent.tools.prometheus import _get_baseline_enrichment

        data = {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {"__name__": "node_cpu"}, "value": [1, "0.5"]}],
            },
        }

        # Default mock_settings has memory_db_path="" -> not configured
        result = _get_baseline_enrichment(data)  # type: ignore[arg-type]
        assert result == ""

    def test_returns_empty_when_no_metric_names(self, mock_settings: Any) -> None:
        from src.agent.tools.prometheus import _get_baseline_enrichment

        # PromQL aggregations like count() don't have __name__ in results
        data = {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [1, "5"]}],
            },
        }
        result = _get_baseline_enrichment(data)  # type: ignore[arg-type]
        assert result == ""


# ---------------------------------------------------------------------------
# Grafana alerts incident history enrichment
# ---------------------------------------------------------------------------


class TestGrafanaAlertIncidentHistoryEnrichment:
    def test_enriches_with_incident_history(self, mock_settings: Any) -> None:
        conn = _make_conn()
        save_incident(
            conn,
            title="Previous CPU spike",
            description="CPU hit 95%",
            alert_name="HighCPU",
            root_cause="Transcoding overload",
        )

        from src.agent.tools.grafana_alerts import _get_incident_history_enrichment

        groups = [
            {
                "labels": {"grafana_folder": "test"},
                "alerts": [
                    {
                        "labels": {"alertname": "HighCPU", "severity": "warning"},
                        "annotations": {},
                        "status": {"state": "active"},
                        "startsAt": "2026-02-25T10:00:00Z",
                    }
                ],
            }
        ]

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            result = _get_incident_history_enrichment("Found 1 alert(s)", groups, None)  # type: ignore[arg-type]

        assert "Incident History from Memory" in result
        assert "Previous CPU spike" in result
        assert "Transcoding overload" in result

    def test_returns_original_when_not_configured(self, mock_settings: Any) -> None:
        from src.agent.tools.grafana_alerts import _get_incident_history_enrichment

        groups = [
            {
                "labels": {},
                "alerts": [
                    {
                        "labels": {"alertname": "HighCPU"},
                        "status": {"state": "active"},
                    }
                ],
            }
        ]

        # Default mock_settings -> memory not configured
        result = _get_incident_history_enrichment("Alerts", groups, None)  # type: ignore[arg-type]
        assert result == "Alerts"

    def test_respects_state_filter(self, mock_settings: Any) -> None:
        conn = _make_conn()
        save_incident(conn, title="Past incident", description="...", alert_name="HighCPU")

        from src.agent.tools.grafana_alerts import _get_incident_history_enrichment

        groups = [
            {
                "labels": {},
                "alerts": [
                    {
                        "labels": {"alertname": "HighCPU"},
                        "status": {"state": "suppressed"},
                    }
                ],
            }
        ]

        with (
            patch("src.memory.context.is_memory_configured", return_value=True),
            patch("src.memory.context.get_initialized_connection", return_value=conn),
        ):
            # Filter for "active" only — the suppressed alert should be skipped
            result = _get_incident_history_enrichment("Alerts", groups, "active")  # type: ignore[arg-type]

        # No alert names extracted (suppressed doesn't match "active" filter)
        assert result == "Alerts"
