"""Integration tests for Prometheus tools with mocked HTTP responses."""

from typing import Any

import httpx
import pytest
import respx

from src.agent.tools.prometheus import (
    prometheus_instant_query,
    prometheus_range_query,
    prometheus_search_metrics,
)


@pytest.fixture(autouse=True)
def _use_mock_settings(mock_settings: Any) -> None:
    """Automatically use mock settings for all tests in this module."""


@pytest.mark.integration
class TestPrometheusInstantQuery:
    @respx.mock
    async def test_successful_query(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/query").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [
                            {
                                "metric": {"__name__": "up", "hostname": "jellyfin", "job": "node_exporter"},
                                "value": [1700000000, "1"],
                            }
                        ],
                    },
                },
            )
        )

        result = await prometheus_instant_query.ainvoke({"query": 'up{hostname="jellyfin"}'})
        assert "jellyfin" in result
        assert "1" in result

    @respx.mock
    async def test_empty_result(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/query").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "data": {"resultType": "vector", "result": []}},
            )
        )

        result = await prometheus_instant_query.ainvoke({"query": 'up{hostname="nonexistent"}'})
        assert "no results" in result.lower()

    @respx.mock
    async def test_prometheus_unreachable(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/query").mock(side_effect=httpx.ConnectError("Connection refused"))

        result = await prometheus_instant_query.ainvoke({"query": "up"})
        assert "Cannot connect" in result

    @respx.mock
    async def test_prometheus_timeout(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/query").mock(side_effect=httpx.ReadTimeout("Read timed out"))

        result = await prometheus_instant_query.ainvoke({"query": "up"})
        assert "timed out" in result

    @respx.mock
    async def test_prometheus_http_error(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/query").mock(
            return_value=httpx.Response(422, text="invalid expression")
        )

        result = await prometheus_instant_query.ainvoke({"query": "invalid{{"})
        assert "422" in result

    @respx.mock
    async def test_query_with_optional_time(self) -> None:
        route = respx.get("http://prometheus.test:9090/api/v1/query").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "data": {"resultType": "vector", "result": []}},
            )
        )

        await prometheus_instant_query.ainvoke({"query": "up", "time": "1700000000"})
        assert route.called
        assert route.calls.last.request.url.params["time"] == "1700000000"


@pytest.mark.integration
class TestPrometheusRangeQuery:
    @respx.mock
    async def test_successful_range_query(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/query_range").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [
                            {
                                "metric": {"__name__": "node_cpu_seconds_total", "hostname": "jellyfin"},
                                "values": [
                                    [1700000000, "100"],
                                    [1700000060, "105"],
                                    [1700000120, "110"],
                                ],
                            }
                        ],
                    },
                },
            )
        )

        result = await prometheus_range_query.ainvoke(
            {
                "query": 'node_cpu_seconds_total{hostname="jellyfin"}',
                "start": "1700000000",
                "end": "1700003600",
                "step": "60s",
            }
        )
        assert "jellyfin" in result
        assert "3 samples" in result

    @respx.mock
    async def test_range_query_sends_correct_params(self) -> None:
        route = respx.get("http://prometheus.test:9090/api/v1/query_range").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "data": {"resultType": "matrix", "result": []}},
            )
        )

        await prometheus_range_query.ainvoke(
            {
                "query": "up",
                "start": "1700000000",
                "end": "1700003600",
                "step": "5m",
            }
        )
        params = route.calls.last.request.url.params
        assert params["query"] == "up"
        assert params["start"] == "1700000000"
        assert params["end"] == "1700003600"
        assert params["step"] == "5m"

    async def test_invalid_range_end_before_start(self) -> None:
        result = await prometheus_range_query.ainvoke(
            {
                "query": "up",
                "start": "1700003600",
                "end": "1700000000",
                "step": "60s",
            }
        )
        assert "end must be after start" in result

    async def test_invalid_range_too_many_points(self) -> None:
        result = await prometheus_range_query.ainvoke(
            {
                "query": "up",
                "start": "1700000000",
                "end": "1700604800",
                "step": "1",
            }
        )
        assert "oo many data points" in result


@pytest.mark.integration
class TestPrometheusSearchMetrics:
    @respx.mock
    async def test_successful_search(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/label/__name__/values").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": ["mktxp_dhcp_lease_count", "mktxp_dhcp_lease_info", "mktxp_interface_rx_bytes_total"],
                },
            )
        )
        respx.get("http://prometheus.test:9090/api/v1/metadata").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "mktxp_dhcp_lease_count": [
                            {"type": "gauge", "help": "Number of active DHCP leases", "unit": ""},
                        ],
                        "mktxp_dhcp_lease_info": [
                            {"type": "gauge", "help": "DHCP lease information", "unit": ""},
                        ],
                        "mktxp_interface_rx_bytes_total": [
                            {"type": "counter", "help": "Total received bytes", "unit": ""},
                        ],
                    },
                },
            )
        )

        result = await prometheus_search_metrics.ainvoke({"search_term": "mktxp"})
        assert "Found 3 metrics" in result
        assert "mktxp_dhcp_lease_count (gauge)" in result
        assert "mktxp_dhcp_lease_info (gauge)" in result
        assert "mktxp_interface_rx_bytes_total (counter)" in result

    @respx.mock
    async def test_correct_match_regex_parameter(self) -> None:
        route = respx.get("http://prometheus.test:9090/api/v1/label/__name__/values").mock(
            return_value=httpx.Response(200, json={"status": "success", "data": []})
        )
        respx.get("http://prometheus.test:9090/api/v1/metadata").mock(
            return_value=httpx.Response(200, json={"status": "success", "data": {}})
        )

        await prometheus_search_metrics.ainvoke({"search_term": "node_cpu"})
        assert route.called
        match_param = route.calls.last.request.url.params["match[]"]
        assert "node_cpu" in match_param
        assert '=~".*node_cpu.*"' in match_param

    @respx.mock
    async def test_no_matching_metrics(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/label/__name__/values").mock(
            return_value=httpx.Response(200, json={"status": "success", "data": []})
        )
        respx.get("http://prometheus.test:9090/api/v1/metadata").mock(
            return_value=httpx.Response(200, json={"status": "success", "data": {}})
        )

        result = await prometheus_search_metrics.ainvoke({"search_term": "nonexistent_metric"})
        assert "No metrics found" in result

    @respx.mock
    async def test_special_characters_escaped(self) -> None:
        route = respx.get("http://prometheus.test:9090/api/v1/label/__name__/values").mock(
            return_value=httpx.Response(200, json={"status": "success", "data": []})
        )
        respx.get("http://prometheus.test:9090/api/v1/metadata").mock(
            return_value=httpx.Response(200, json={"status": "success", "data": {}})
        )

        await prometheus_search_metrics.ainvoke({"search_term": "foo.bar+baz"})
        match_param = route.calls.last.request.url.params["match[]"]
        # Dots and plus should be escaped for regex safety
        assert r"foo\.bar\+baz" in match_param

    @respx.mock
    async def test_connect_error_raises_tool_exception(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/label/__name__/values").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await prometheus_search_metrics.ainvoke({"search_term": "up"})
        assert "Cannot connect" in result

    @respx.mock
    async def test_timeout_raises_tool_exception(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/label/__name__/values").mock(
            side_effect=httpx.ReadTimeout("Read timed out")
        )

        result = await prometheus_search_metrics.ainvoke({"search_term": "up"})
        assert "timed out" in result

    @respx.mock
    async def test_metadata_failure_still_returns_names(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/label/__name__/values").mock(
            return_value=httpx.Response(
                200,
                json={"status": "success", "data": ["up", "node_cpu_seconds_total"]},
            )
        )
        respx.get("http://prometheus.test:9090/api/v1/metadata").mock(
            side_effect=httpx.ConnectError("metadata endpoint down")
        )

        result = await prometheus_search_metrics.ainvoke({"search_term": "node"})
        assert "Found 2 metrics" in result
        assert "node_cpu_seconds_total" in result
        assert "up" in result
