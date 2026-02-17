"""Integration tests for the HDD power status composite tool with mocked HTTP."""

from typing import Any

import httpx
import pytest
import respx

from src.agent.tools.disk_status import hdd_power_status


@pytest.fixture(autouse=True)
def _use_mock_settings(mock_settings: Any) -> None:
    """Automatically use mock settings for all tests in this module."""


def _mock_power_state_response(
    results: list[dict[str, Any]],
) -> httpx.Response:
    """Build a mocked Prometheus instant query response for disk_power_state."""
    return httpx.Response(
        200,
        json={
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": results,
            },
        },
    )


def _mock_truenas_disks() -> httpx.Response:
    """Build a mocked TrueNAS /disk response with 2 HDDs."""
    return httpx.Response(
        200,
        json=[
            {
                "identifier": "{serial_lunid}5000c500eb02b449",
                "name": "sdc",
                "serial": "WWZ5TZSF",
                "model": "ST8000VN004-3CP101",
                "type": "HDD",
                "size": 8001563222016,
                "pool": "tank",
                "togglesmart": True,
                "hddstandby": "30",
            },
            {
                "identifier": "{serial_lunid}5000c500f742ccbf",
                "name": "sdf",
                "serial": "K3S04BKQ",
                "model": "ST16000NT001-3LV101",
                "type": "HDD",
                "size": 16000900661248,
                "pool": "tank",
                "togglesmart": True,
                "hddstandby": "60",
            },
        ],
    )


POWER_STATE_RESULTS = [
    {
        "metric": {
            "__name__": "disk_power_state",
            "device_id": "/dev/disk/by-id/wwn-0x5000c500eb02b449",
            "type": "hdd",
            "pool": "tank",
        },
        "value": [1700000000, "2"],
    },
    {
        "metric": {
            "__name__": "disk_power_state",
            "device_id": "/dev/disk/by-id/wwn-0x5000c500f742ccbf",
            "type": "hdd",
            "pool": "tank",
        },
        "value": [1700000000, "0"],
    },
]


@pytest.mark.integration
class TestHddPowerStatus:
    @respx.mock
    async def test_shows_current_state_with_disk_names(self) -> None:
        """Tool cross-references Prometheus device_ids with TrueNAS disk inventory."""
        # Prometheus: disk_power_state instant query
        respx.get("http://prometheus.test:9090/api/v1/query").mock(
            side_effect=[
                _mock_power_state_response(POWER_STATE_RESULTS),
                # changes() query — no changes
                _mock_power_state_response(
                    [
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500eb02b449"},
                            "value": [1700000000, "0"],
                        },
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500f742ccbf"},
                            "value": [1700000000, "0"],
                        },
                    ]
                ),
                # Widen to 6h — still no changes
                _mock_power_state_response(
                    [
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500eb02b449"},
                            "value": [1700000000, "0"],
                        },
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500f742ccbf"},
                            "value": [1700000000, "0"],
                        },
                    ]
                ),
                # Widen to 24h — no changes
                _mock_power_state_response(
                    [
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500eb02b449"},
                            "value": [1700000000, "0"],
                        },
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500f742ccbf"},
                            "value": [1700000000, "0"],
                        },
                    ]
                ),
                # Widen to 7d — no changes
                _mock_power_state_response(
                    [
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500eb02b449"},
                            "value": [1700000000, "0"],
                        },
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500f742ccbf"},
                            "value": [1700000000, "0"],
                        },
                    ]
                ),
            ]
        )
        # TrueNAS: disk inventory
        respx.get("https://truenas.test/api/v2.0/disk").mock(return_value=_mock_truenas_disks())

        result = await hdd_power_status.ainvoke({})

        # Should show human-readable disk names, not wwn IDs
        assert "sdc" in result
        assert "ST8000VN004" in result
        assert "sdf" in result
        assert "ST16000NT001" in result
        # Should show power state labels
        assert "active/idle" in result
        assert "standby" in result
        # Should NOT show raw wwn paths
        assert "/dev/disk/by-id/" not in result

    @respx.mock
    async def test_no_changes_reports_stable(self) -> None:
        """When no transitions in 7d, clearly states disks are stable."""
        respx.get("http://prometheus.test:9090/api/v1/query").mock(
            side_effect=[
                _mock_power_state_response(POWER_STATE_RESULTS),
                # All changes() queries return 0
                *[
                    _mock_power_state_response(
                        [
                            {"metric": {"device_id": "d1"}, "value": [1700000000, "0"]},
                            {"metric": {"device_id": "d2"}, "value": [1700000000, "0"]},
                        ]
                    )
                    for _ in range(4)
                ],
            ]
        )
        respx.get("https://truenas.test/api/v2.0/disk").mock(return_value=_mock_truenas_disks())

        result = await hdd_power_status.ainvoke({})
        assert "no power state changes" in result.lower() or "no change" in result.lower()

    @respx.mock
    async def test_finds_transitions_with_range_query(self) -> None:
        """When changes() finds transitions, pinpoints time with range query."""
        respx.get("http://prometheus.test:9090/api/v1/query").mock(
            side_effect=[
                # Current state
                _mock_power_state_response(POWER_STATE_RESULTS),
                # changes() for 1h — finds a change on first disk
                _mock_power_state_response(
                    [
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500eb02b449"},
                            "value": [1700000000, "1"],  # 1 change detected
                        },
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500f742ccbf"},
                            "value": [1700000000, "0"],
                        },
                    ]
                ),
            ]
        )
        # Range query to pinpoint transition
        respx.get("http://prometheus.test:9090/api/v1/query_range").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [
                            {
                                "metric": {
                                    "device_id": "/dev/disk/by-id/wwn-0x5000c500eb02b449",
                                },
                                "values": [
                                    [1699999800, "0"],  # standby
                                    [1699999815, "0"],
                                    [1699999830, "2"],  # ← transition!
                                    [1699999845, "2"],
                                ],
                            },
                            {
                                "metric": {
                                    "device_id": "/dev/disk/by-id/wwn-0x5000c500f742ccbf",
                                },
                                "values": [
                                    [1699999800, "0"],
                                    [1699999815, "0"],
                                    [1699999830, "0"],
                                    [1699999845, "0"],
                                ],
                            },
                        ],
                    },
                },
            )
        )
        respx.get("https://truenas.test/api/v2.0/disk").mock(return_value=_mock_truenas_disks())

        result = await hdd_power_status.ainvoke({})

        # Should show transition with disk name and timestamp
        assert "sdc" in result
        assert "standby" in result
        assert "active/idle" in result
        assert "→" in result
        # sdf should show no change
        assert "no change" in result.lower()

    @respx.mock
    async def test_prometheus_unreachable(self) -> None:
        """When Prometheus is down, returns a clear error."""
        respx.get("http://prometheus.test:9090/api/v1/query").mock(side_effect=httpx.ConnectError("Connection refused"))

        result = await hdd_power_status.ainvoke({})
        assert "Cannot connect" in result

    @respx.mock
    async def test_no_metrics_found(self) -> None:
        """When disk_power_state returns no series, explains the issue."""
        respx.get("http://prometheus.test:9090/api/v1/query").mock(return_value=_mock_power_state_response([]))

        result = await hdd_power_status.ainvoke({})
        assert "disk-status-exporter" in result

    @respx.mock
    async def test_works_without_truenas(self) -> None:
        """When TrueNAS is unreachable, still shows power states with device IDs."""
        respx.get("http://prometheus.test:9090/api/v1/query").mock(
            side_effect=[
                _mock_power_state_response(POWER_STATE_RESULTS),
                *[
                    _mock_power_state_response(
                        [
                            {"metric": {"device_id": "d1"}, "value": [1700000000, "0"]},
                            {"metric": {"device_id": "d2"}, "value": [1700000000, "0"]},
                        ]
                    )
                    for _ in range(4)
                ],
            ]
        )
        respx.get("https://truenas.test/api/v2.0/disk").mock(side_effect=httpx.ConnectError("TrueNAS down"))

        result = await hdd_power_status.ainvoke({})
        # Should still show power states, just with shortened device IDs
        assert "active/idle" in result
        assert "standby" in result
        assert "wwn-0x5000c500eb02b449" in result
