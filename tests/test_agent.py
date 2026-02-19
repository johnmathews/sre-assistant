"""Unit tests for agent assembly — system prompt, tool wiring, invocation."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agent.agent import (
    SYSTEM_PROMPT_TEMPLATE,
    _get_tools,
    _is_tool_call_pairing_error,
    build_agent,
    invoke_agent,
)


class TestSystemPrompt:
    def test_mentions_all_tools(self) -> None:
        assert "prometheus_instant_query" in SYSTEM_PROMPT_TEMPLATE
        assert "prometheus_range_query" in SYSTEM_PROMPT_TEMPLATE
        assert "grafana_get_alerts" in SYSTEM_PROMPT_TEMPLATE
        assert "grafana_get_alert_rules" in SYSTEM_PROMPT_TEMPLATE
        assert "runbook_search" in SYSTEM_PROMPT_TEMPLATE

    def test_mentions_proxmox_tools(self) -> None:
        assert "proxmox_list_guests" in SYSTEM_PROMPT_TEMPLATE
        assert "proxmox_get_guest_config" in SYSTEM_PROMPT_TEMPLATE
        assert "proxmox_node_status" in SYSTEM_PROMPT_TEMPLATE
        assert "proxmox_list_tasks" in SYSTEM_PROMPT_TEMPLATE

    def test_mentions_pbs_tools(self) -> None:
        assert "pbs_datastore_status" in SYSTEM_PROMPT_TEMPLATE
        assert "pbs_list_backups" in SYSTEM_PROMPT_TEMPLATE
        assert "pbs_list_tasks" in SYSTEM_PROMPT_TEMPLATE

    def test_has_proxmox_vs_prometheus_guidance(self) -> None:
        assert "Proxmox API vs Prometheus" in SYSTEM_PROMPT_TEMPLATE

    def test_has_promql_patterns(self) -> None:
        assert "Common PromQL Patterns" in SYSTEM_PROMPT_TEMPLATE
        assert "topk" in SYSTEM_PROMPT_TEMPLATE
        assert "avg_over_time" in SYSTEM_PROMPT_TEMPLATE
        assert "rate(" in SYSTEM_PROMPT_TEMPLATE

    def test_has_tool_selection_guide(self) -> None:
        assert "Tool Selection Guide" in SYSTEM_PROMPT_TEMPLATE

    def test_advises_metrics_first(self) -> None:
        assert "query metrics first" in SYSTEM_PROMPT_TEMPLATE

    def test_warns_against_fabrication(self) -> None:
        assert "Never fabricate" in SYSTEM_PROMPT_TEMPLATE


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

    def test_system_prompt_contains_current_date(self, mock_settings: object) -> None:
        """build_agent should inject today's date into the system prompt."""
        with patch("src.agent.agent.create_agent") as mock_create:
            mock_create.return_value = AsyncMock()
            build_agent()

            call_kwargs = mock_create.call_args
            prompt: str = call_kwargs.kwargs.get("system_prompt") or call_kwargs.args[2]
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            assert today in prompt
            assert "retains data" in prompt.lower()

    def test_system_prompt_has_aggregation_guidance(self, mock_settings: object) -> None:
        """The prompt template should include instant-query aggregation guidance."""
        assert "Single-value aggregation" in SYSTEM_PROMPT_TEMPLATE
        assert "prometheus_instant_query" in SYSTEM_PROMPT_TEMPLATE
        assert "*_over_time" in SYSTEM_PROMPT_TEMPLATE


class TestIsToolCallPairingError:
    """Tests for the tool_call pairing error detection helper."""

    def test_detects_openai_tool_call_error(self) -> None:
        exc = Exception(
            "Error code: 400 - {'error': {'message': \"An assistant message "
            "with 'tool_calls' must be followed by tool messages responding "
            "to each 'tool_call_id'.\"}}"
        )
        assert _is_tool_call_pairing_error(exc) is True

    def test_ignores_unrelated_errors(self) -> None:
        assert _is_tool_call_pairing_error(Exception("Connection refused")) is False
        assert _is_tool_call_pairing_error(Exception("rate limit exceeded")) is False
        assert _is_tool_call_pairing_error(TimeoutError("timed out")) is False

    def test_ignores_partial_match(self) -> None:
        # Must have BOTH "tool_calls" AND "tool messages" to match
        assert _is_tool_call_pairing_error(Exception("tool_calls not found")) is False
        assert _is_tool_call_pairing_error(Exception("tool messages missing")) is False


class TestInvokeAgent:
    """Tests for invoke_agent error handling and session recovery."""

    @pytest.mark.integration
    async def test_returns_ai_message_content(self, mock_settings: object) -> None:
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"messages": [AIMessage(content="CPU is at 42%.")]}

        result = await invoke_agent(mock_agent, "What is CPU?", session_id="s1")
        assert result == "CPU is at 42%."

    @pytest.mark.integration
    async def test_returns_fallback_when_no_ai_message(self, mock_settings: object) -> None:
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"messages": []}

        result = await invoke_agent(mock_agent, "hello", session_id="s1")
        assert result == "No response generated."

    @pytest.mark.integration
    async def test_recovers_from_corrupted_tool_call_history(self, mock_settings: object) -> None:
        """When session history has orphaned tool_calls, invoke_agent retries
        with a fresh session instead of permanently failing."""
        tool_call_error = Exception(
            "Error code: 400 - {'error': {'message': \"An assistant message "
            "with 'tool_calls' must be followed by tool messages responding "
            "to each 'tool_call_id'. The following tool_call_ids did not have "
            'response messages: call_abc123"}}'
        )

        mock_agent = AsyncMock()
        # First call with original session: corrupted history → error
        # Second call with fresh session: succeeds
        mock_agent.ainvoke.side_effect = [
            tool_call_error,
            {"messages": [AIMessage(content="Recovered response.")]},
        ]

        result = await invoke_agent(mock_agent, "hello?", session_id="broken-sess")

        assert result == "Recovered response."
        assert mock_agent.ainvoke.call_count == 2

        # Verify the retry used a different thread_id (config passed as kwarg)
        first_thread = mock_agent.ainvoke.call_args_list[0].kwargs["config"]["configurable"]["thread_id"]
        second_thread = mock_agent.ainvoke.call_args_list[1].kwargs["config"]["configurable"]["thread_id"]
        assert first_thread != second_thread
        assert second_thread.startswith("broken-sess-")

    @pytest.mark.integration
    async def test_raises_non_tool_call_errors(self, mock_settings: object) -> None:
        """Errors unrelated to tool_call pairing still propagate."""
        mock_agent = AsyncMock()
        mock_agent.ainvoke.side_effect = RuntimeError("LLM exploded")

        with pytest.raises(RuntimeError, match="LLM exploded"):
            await invoke_agent(mock_agent, "boom", session_id="s1")

    @pytest.mark.integration
    async def test_timeout_error_propagates(self, mock_settings: object) -> None:
        """A generic timeout from ainvoke propagates (not a tool_call pairing issue)."""
        mock_agent = AsyncMock()
        mock_agent.ainvoke.side_effect = TimeoutError("timed out")

        with pytest.raises(TimeoutError, match="timed out"):
            await invoke_agent(mock_agent, "slow query", session_id="s1")

    @pytest.mark.integration
    async def test_recovery_failure_propagates(self, mock_settings: object) -> None:
        """If the fresh-session retry also fails, that error propagates."""
        tool_call_error = Exception(
            "An assistant message with 'tool_calls' must be followed by "
            "tool messages responding to each 'tool_call_id'."
        )

        mock_agent = AsyncMock()
        mock_agent.ainvoke.side_effect = [
            tool_call_error,
            RuntimeError("LLM still broken"),
        ]

        with pytest.raises(RuntimeError, match="LLM still broken"):
            await invoke_agent(mock_agent, "hello", session_id="s1")
