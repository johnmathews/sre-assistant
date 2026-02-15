"""Unit tests for agent assembly — system prompt, tool wiring, invocation."""

from unittest.mock import patch

from src.agent.agent import SYSTEM_PROMPT, _get_tools, build_agent


class TestSystemPrompt:
    def test_mentions_all_tools(self) -> None:
        assert "prometheus_instant_query" in SYSTEM_PROMPT
        assert "prometheus_range_query" in SYSTEM_PROMPT
        assert "grafana_get_alerts" in SYSTEM_PROMPT
        assert "grafana_get_alert_rules" in SYSTEM_PROMPT
        assert "runbook_search" in SYSTEM_PROMPT

    def test_has_tool_selection_guide(self) -> None:
        assert "Tool Selection Guide" in SYSTEM_PROMPT

    def test_advises_metrics_first(self) -> None:
        assert "query metrics first" in SYSTEM_PROMPT

    def test_warns_against_fabrication(self) -> None:
        assert "Never fabricate" in SYSTEM_PROMPT


class TestGetTools:
    def test_includes_prometheus_tools(self, mock_settings: object) -> None:
        tools = _get_tools()
        tool_names = [t.name for t in tools]
        assert "prometheus_instant_query" in tool_names
        assert "prometheus_range_query" in tool_names

    def test_includes_grafana_tools(self, mock_settings: object) -> None:
        tools = _get_tools()
        tool_names = [t.name for t in tools]
        assert "grafana_get_alerts" in tool_names
        assert "grafana_get_alert_rules" in tool_names

    def test_includes_runbook_search(self, mock_settings: object) -> None:
        tools = _get_tools()
        tool_names = [t.name for t in tools]
        assert "runbook_search" in tool_names

    def test_gracefully_handles_missing_runbook_tool(self, mock_settings: object) -> None:
        with patch(
            "src.agent.retrieval.runbooks.load_vector_store",
            side_effect=Exception("no vector store"),
        ):
            # Import still works but tool would fail at runtime;
            # _get_tools should still include it since import succeeds
            tools = _get_tools()
            assert len(tools) >= 4


class TestBuildAgent:
    def test_builds_without_error(self, mock_settings: object) -> None:
        agent = build_agent()
        assert agent is not None
        assert hasattr(agent, "invoke")

    def test_custom_model_name(self, mock_settings: object) -> None:
        agent = build_agent(model_name="gpt-4o")
        assert agent is not None
