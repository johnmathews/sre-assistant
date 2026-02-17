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


def _zero_changes_response() -> httpx.Response:
    """Build a changes() response where all disks have 0 changes."""
    return _mock_power_state_response(
        [
            {"metric": {"device_id": "d1"}, "value": [1700000000, "0"]},
            {"metric": {"device_id": "d2"}, "value": [1700000000, "0"]},
        ]
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
        # Query order: current_state, 24h_counts, changes_1h, changes_6h, changes_24h, changes_7d
        respx.get("http://prometheus.test:9090/api/v1/query").mock(
            side_effect=[
                _mock_power_state_response(POWER_STATE_RESULTS),
                _zero_changes_response(),  # 24h change counts
                _zero_changes_response(),  # changes() 1h
                _zero_changes_response(),  # changes() 6h
                _zero_changes_response(),  # changes() 24h
                _zero_changes_response(),  # changes() 7d
            ]
        )
        respx.get("https://truenas.test/api/v2.0/disk").mock(return_value=_mock_truenas_disks())

        result = await hdd_power_status.ainvoke({})

        # Should show human-readable disk names, not wwn IDs
        assert "sdc" in result
        assert "ST8000VN004" in result
        assert "sdf" in result
        assert "ST16000NT001" in result
        # Should show power state labels
        assert "active_or_idle" in result
        assert "standby" in result
        # Should NOT show raw wwn paths
        assert "/dev/disk/by-id/" not in result

    @respx.mock
    async def test_no_changes_reports_stable(self) -> None:
        """When no transitions in 7d, clearly states disks are stable."""
        respx.get("http://prometheus.test:9090/api/v1/query").mock(
            side_effect=[
                _mock_power_state_response(POWER_STATE_RESULTS),
                _zero_changes_response(),  # 24h change counts
                *[_zero_changes_response() for _ in range(4)],  # changes() widening
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
                # 24h change counts — 1 change on first disk
                _mock_power_state_response(
                    [
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500eb02b449"},
                            "value": [1700000000, "1"],
                        },
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500f742ccbf"},
                            "value": [1700000000, "0"],
                        },
                    ]
                ),
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
        assert "active_or_idle" in result
        assert "→" in result
        # sdf should show no change
        assert "no change" in result.lower()
        # Should show 24h change counts
        assert "1 total" in result or "1 change" in result

    @respx.mock
    async def test_shows_24h_change_counts(self) -> None:
        """Tool includes per-disk change counts in last 24 hours."""
        respx.get("http://prometheus.test:9090/api/v1/query").mock(
            side_effect=[
                _mock_power_state_response(POWER_STATE_RESULTS),
                # 24h change counts — 3 changes on disk 1, 2 on disk 2
                _mock_power_state_response(
                    [
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500eb02b449"},
                            "value": [1700000000, "3"],
                        },
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500f742ccbf"},
                            "value": [1700000000, "2"],
                        },
                    ]
                ),
                # changes() for 1h — has changes
                _mock_power_state_response(
                    [
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500eb02b449"},
                            "value": [1700000000, "1"],
                        },
                        {
                            "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500f742ccbf"},
                            "value": [1700000000, "1"],
                        },
                    ]
                ),
            ]
        )
        # Range query
        respx.get("http://prometheus.test:9090/api/v1/query_range").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [
                            {
                                "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500eb02b449"},
                                "values": [[1699999800, "0"], [1699999830, "2"]],
                            },
                            {
                                "metric": {"device_id": "/dev/disk/by-id/wwn-0x5000c500f742ccbf"},
                                "values": [[1699999800, "2"], [1699999830, "0"]],
                            },
                        ],
                    },
                },
            )
        )
        respx.get("https://truenas.test/api/v2.0/disk").mock(return_value=_mock_truenas_disks())

        result = await hdd_power_status.ainvoke({})

        # Should show total and per-disk counts
        assert "5 total" in result
        assert "3 change" in result
        assert "2 change" in result

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
                _zero_changes_response(),  # 24h change counts
                *[_zero_changes_response() for _ in range(4)],  # changes() widening
            ]
        )
        respx.get("https://truenas.test/api/v2.0/disk").mock(side_effect=httpx.ConnectError("TrueNAS down"))

        result = await hdd_power_status.ainvoke({})
        # Should still show power states, just with shortened device IDs
        assert "active_or_idle" in result
        assert "standby" in result
        assert "wwn-0x5000c500eb02b449" in result
