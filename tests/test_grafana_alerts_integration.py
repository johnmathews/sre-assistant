"""Integration tests for Grafana alerting tools with mocked HTTP responses."""

from typing import Any

import httpx
import pytest
import respx

from src.agent.tools.grafana_alerts import grafana_get_alert_rules, grafana_get_alerts


@pytest.fixture(autouse=True)
def _use_mock_settings(mock_settings: Any) -> None:
    """Automatically use mock settings for all tests in this module."""


@pytest.mark.integration
class TestGrafanaGetAlerts:
    @respx.mock
    async def test_successful_fetch(self) -> None:
        respx.get("http://grafana.test:3000/api/alertmanager/grafana/api/v2/alerts/groups").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "labels": {"grafana_folder": "Infrastructure"},
                        "receiver": {"name": "pushover"},
                        "alerts": [
                            {
                                "labels": {"alertname": "HighCPU", "severity": "warning", "hostname": "jellyfin"},
                                "annotations": {"summary": "CPU above 90%"},
                                "startsAt": "2024-01-15T10:00:00Z",
                                "status": {"state": "active"},
                            }
                        ],
                    }
                ],
            )
        )

        result = await grafana_get_alerts.ainvoke({})
        assert "HighCPU" in result
        assert "jellyfin" in result

    @respx.mock
    async def test_with_state_filter(self) -> None:
        route = respx.get("http://grafana.test:3000/api/alertmanager/grafana/api/v2/alerts/groups").mock(
            return_value=httpx.Response(200, json=[]),
        )

        await grafana_get_alerts.ainvoke({"state": "active"})
        assert route.called
        assert route.calls.last.request.url.params.get("filter") == "active"

    @respx.mock
    async def test_empty_response(self) -> None:
        respx.get("http://grafana.test:3000/api/alertmanager/grafana/api/v2/alerts/groups").mock(
            return_value=httpx.Response(200, json=[]),
        )

        result = await grafana_get_alerts.ainvoke({})
        assert "No alerts found" in result

    @respx.mock
    async def test_grafana_unreachable(self) -> None:
        respx.get("http://grafana.test:3000/api/alertmanager/grafana/api/v2/alerts/groups").mock(
            side_effect=httpx.ConnectError("Connection refused"),
        )

        result = await grafana_get_alerts.ainvoke({})
        assert "Cannot connect" in result

    @respx.mock
    async def test_auth_failure(self) -> None:
        respx.get("http://grafana.test:3000/api/alertmanager/grafana/api/v2/alerts/groups").mock(
            return_value=httpx.Response(401, text="Unauthorized"),
        )

        result = await grafana_get_alerts.ainvoke({})
        assert "401" in result

    @respx.mock
    async def test_sends_auth_header(self) -> None:
        route = respx.get("http://grafana.test:3000/api/alertmanager/grafana/api/v2/alerts/groups").mock(
            return_value=httpx.Response(200, json=[]),
        )

        await grafana_get_alerts.ainvoke({})
        auth_header = route.calls.last.request.headers.get("authorization")
        assert auth_header == "Bearer glsa_test_fake"


@pytest.mark.integration
class TestGrafanaGetAlertRules:
    @respx.mock
    async def test_successful_fetch(self) -> None:
        respx.get("http://grafana.test:3000/api/v1/provisioning/alert-rules").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "uid": "abc123",
                        "title": "High CPU Usage",
                        "folderUID": "infra",
                        "ruleGroup": "node-alerts",
                        "labels": {"severity": "warning"},
                        "annotations": {"summary": "CPU > 90% for 5 minutes"},
                    }
                ],
            )
        )

        result = await grafana_get_alert_rules.ainvoke({})
        assert "High CPU Usage" in result
        assert "abc123" in result

    @respx.mock
    async def test_empty_rules(self) -> None:
        respx.get("http://grafana.test:3000/api/v1/provisioning/alert-rules").mock(
            return_value=httpx.Response(200, json=[]),
        )

        result = await grafana_get_alert_rules.ainvoke({})
        assert "No alert rules found" in result

    @respx.mock
    async def test_grafana_timeout(self) -> None:
        respx.get("http://grafana.test:3000/api/v1/provisioning/alert-rules").mock(
            side_effect=httpx.ReadTimeout("Read timed out"),
        )

        result = await grafana_get_alert_rules.ainvoke({})
        assert "timed out" in result
