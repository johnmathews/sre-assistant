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

    def test_mentions_proxmox_tools(self) -> None:
        assert "proxmox_list_guests" in SYSTEM_PROMPT
        assert "proxmox_get_guest_config" in SYSTEM_PROMPT
        assert "proxmox_node_status" in SYSTEM_PROMPT
        assert "proxmox_list_tasks" in SYSTEM_PROMPT

    def test_mentions_pbs_tools(self) -> None:
        assert "pbs_datastore_status" in SYSTEM_PROMPT
        assert "pbs_list_backups" in SYSTEM_PROMPT
        assert "pbs_list_tasks" in SYSTEM_PROMPT

    def test_has_proxmox_vs_prometheus_guidance(self) -> None:
        assert "Proxmox API vs Prometheus" in SYSTEM_PROMPT

    def test_has_promql_patterns(self) -> None:
        assert "Common PromQL Patterns" in SYSTEM_PROMPT
        assert "topk" in SYSTEM_PROMPT
        assert "avg_over_time" in SYSTEM_PROMPT
        assert "rate(" in SYSTEM_PROMPT

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

    def test_includes_proxmox_tools_when_configured(self, mock_settings: object) -> None:
        tools = _get_tools()
        tool_names = [t.name for t in tools]
        assert "proxmox_list_guests" in tool_names
        assert "proxmox_get_guest_config" in tool_names
        assert "proxmox_node_status" in tool_names
        assert "proxmox_list_tasks" in tool_names

    def test_excludes_proxmox_tools_when_not_configured(self, mock_settings: object) -> None:
        mock_settings.proxmox_url = ""  # type: ignore[attr-defined]
        tools = _get_tools()
        tool_names = [t.name for t in tools]
        assert "proxmox_list_guests" not in tool_names
        assert "proxmox_get_guest_config" not in tool_names

    def test_includes_pbs_tools_when_configured(self, mock_settings: object) -> None:
        tools = _get_tools()
        tool_names = [t.name for t in tools]
        assert "pbs_datastore_status" in tool_names
        assert "pbs_list_backups" in tool_names
        assert "pbs_list_tasks" in tool_names

    def test_excludes_pbs_tools_when_not_configured(self, mock_settings: object) -> None:
        mock_settings.pbs_url = ""  # type: ignore[attr-defined]
        tools = _get_tools()
        tool_names = [t.name for t in tools]
        assert "pbs_datastore_status" not in tool_names
        assert "pbs_list_backups" not in tool_names

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
