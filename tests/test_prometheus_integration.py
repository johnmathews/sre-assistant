"""Integration tests for Prometheus tools with mocked HTTP responses."""

from typing import Any

import httpx
import pytest
import respx

from src.agent.tools.prometheus import prometheus_instant_query, prometheus_range_query


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

        with pytest.raises(Exception, match="Cannot connect"):
            await prometheus_instant_query.ainvoke({"query": "up"})

    @respx.mock
    async def test_prometheus_timeout(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/query").mock(
            side_effect=httpx.ReadTimeout("Read timed out")
        )

        with pytest.raises(Exception, match="timed out"):
            await prometheus_instant_query.ainvoke({"query": "up"})

    @respx.mock
    async def test_prometheus_http_error(self) -> None:
        respx.get("http://prometheus.test:9090/api/v1/query").mock(
            return_value=httpx.Response(422, text="invalid expression")
        )

        with pytest.raises(Exception, match="422"):
            await prometheus_instant_query.ainvoke({"query": "invalid{{"})

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

        result = await prometheus_range_query.ainvoke({
            "query": 'node_cpu_seconds_total{hostname="jellyfin"}',
            "start": "1700000000",
            "end": "1700003600",
            "step": "60s",
        })
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

        await prometheus_range_query.ainvoke({
            "query": "up",
            "start": "1700000000",
            "end": "1700003600",
            "step": "5m",
        })
        params = route.calls.last.request.url.params
        assert params["query"] == "up"
        assert params["start"] == "1700000000"
        assert params["end"] == "1700003600"
        assert params["step"] == "5m"

    async def test_invalid_range_end_before_start(self) -> None:
        with pytest.raises(Exception, match="end must be after start"):
            await prometheus_range_query.ainvoke({
                "query": "up",
                "start": "1700003600",
                "end": "1700000000",
                "step": "60s",
            })

    async def test_invalid_range_too_many_points(self) -> None:
        with pytest.raises(Exception, match="[Tt]oo many data points"):
            await prometheus_range_query.ainvoke({
                "query": "up",
                "start": "1700000000",
                "end": "1700604800",
                "step": "1",
            })
