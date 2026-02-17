"""Integration tests for TrueNAS SCALE tools with mocked HTTP responses."""

from typing import Any

import httpx
import pytest
import respx

from src.agent.tools.truenas import (
    truenas_apps,
    truenas_list_shares,
    truenas_pool_status,
    truenas_snapshots,
    truenas_system_status,
)


@pytest.fixture(autouse=True)
def _use_mock_settings(mock_settings: Any) -> None:
    """Automatically use mock settings for all tests in this module."""


BASE = "https://truenas.test/api/v2.0"


@pytest.mark.integration
class TestTruenasPoolStatus:
    @respx.mock
    async def test_healthy_pool_with_topology(self) -> None:
        respx.get(f"{BASE}/pool").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "tank",
                        "status": "ONLINE",
                        "healthy": True,
                        "size": 16 * 1024**4,
                        "allocated": 8 * 1024**4,
                        "free": 8 * 1024**4,
                        "topology": {
                            "data": [
                                {
                                    "type": "MIRROR",
                                    "children": [
                                        {"disk": "sdf", "status": "ONLINE"},
                                        {"disk": "sdh", "status": "ONLINE"},
                                    ],
                                }
                            ],
                            "special": [
                                {
                                    "type": "MIRROR",
                                    "children": [
                                        {"disk": "sdb", "status": "ONLINE"},
                                        {"disk": "sdd", "status": "ONLINE"},
                                    ],
                                }
                            ],
                        },
                    }
                ],
            )
        )
        respx.get(f"{BASE}/pool/dataset").mock(return_value=httpx.Response(200, json=[]))

        result = await truenas_pool_status.ainvoke({})
        assert "1 pool(s)" in result
        assert "tank" in result
        assert "ONLINE" in result
        assert "HEALTHY" in result
        assert "data (MIRROR): sdf, sdh" in result
        assert "special (MIRROR): sdb, sdd" in result

    @respx.mock
    async def test_connect_error(self) -> None:
        respx.get(f"{BASE}/pool").mock(side_effect=httpx.ConnectError("Connection refused"))

        result = await truenas_pool_status.ainvoke({})
        assert "Cannot connect" in result

    @respx.mock
    async def test_timeout(self) -> None:
        respx.get(f"{BASE}/pool").mock(side_effect=httpx.ReadTimeout("Read timed out"))

        result = await truenas_pool_status.ainvoke({})
        assert "timed out" in result

    @respx.mock
    async def test_auth_error(self) -> None:
        respx.get(f"{BASE}/pool").mock(return_value=httpx.Response(401, text="Not authenticated"))

        result = await truenas_pool_status.ainvoke({})
        assert "401" in result

    @respx.mock
    async def test_sends_bearer_auth(self) -> None:
        route = respx.get(f"{BASE}/pool").mock(return_value=httpx.Response(200, json=[]))
        respx.get(f"{BASE}/pool/dataset").mock(return_value=httpx.Response(200, json=[]))

        await truenas_pool_status.ainvoke({})
        assert route.called
        auth_header = route.calls.last.request.headers["authorization"]
        assert auth_header.startswith("Bearer ")


@pytest.mark.integration
class TestTruenasListShares:
    @respx.mock
    async def test_lists_nfs_and_smb(self) -> None:
        respx.get(f"{BASE}/sharing/nfs").mock(
            return_value=httpx.Response(
                200,
                json=[{"path": "/mnt/tank/media", "enabled": True, "ro": False}],
            )
        )
        respx.get(f"{BASE}/sharing/smb").mock(
            return_value=httpx.Response(
                200,
                json=[{"name": "TimeMachine", "path": "/mnt/tank/tm", "enabled": True}],
            )
        )

        result = await truenas_list_shares.ainvoke({})
        assert "NFS shares (1)" in result
        assert "/mnt/tank/media" in result
        assert "SMB shares (1)" in result
        assert "TimeMachine" in result

    @respx.mock
    async def test_filter_nfs_only(self) -> None:
        respx.get(f"{BASE}/sharing/nfs").mock(return_value=httpx.Response(200, json=[]))

        result = await truenas_list_shares.ainvoke({"share_type": "nfs"})
        assert "NFS shares" in result
        assert "SMB shares" not in result

    @respx.mock
    async def test_filter_smb_only(self) -> None:
        respx.get(f"{BASE}/sharing/smb").mock(return_value=httpx.Response(200, json=[]))

        result = await truenas_list_shares.ainvoke({"share_type": "smb"})
        assert "SMB shares" in result
        assert "NFS shares" not in result

    @respx.mock
    async def test_connect_error(self) -> None:
        respx.get(f"{BASE}/sharing/nfs").mock(side_effect=httpx.ConnectError("Connection refused"))

        result = await truenas_list_shares.ainvoke({})
        assert "Cannot connect" in result

    @respx.mock
    async def test_timeout(self) -> None:
        respx.get(f"{BASE}/sharing/nfs").mock(side_effect=httpx.ReadTimeout("Read timed out"))

        result = await truenas_list_shares.ainvoke({})
        assert "timed out" in result

    @respx.mock
    async def test_sends_bearer_auth(self) -> None:
        route = respx.get(f"{BASE}/sharing/nfs").mock(return_value=httpx.Response(200, json=[]))
        respx.get(f"{BASE}/sharing/smb").mock(return_value=httpx.Response(200, json=[]))

        await truenas_list_shares.ainvoke({})
        assert route.called
        auth_header = route.calls.last.request.headers["authorization"]
        assert auth_header.startswith("Bearer ")


@pytest.mark.integration
class TestTruenasSnapshots:
    @respx.mock
    async def test_snapshots_and_tasks(self) -> None:
        respx.get(f"{BASE}/zfs/snapshot").mock(
            return_value=httpx.Response(
                200,
                json=[{"id": "tank/media@auto-2024-01-15"}],
            )
        )
        respx.get(f"{BASE}/pool/snapshottask").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "dataset": "tank/media",
                        "enabled": True,
                        "lifetime_value": 14,
                        "lifetime_unit": "DAY",
                        "recursive": True,
                        "schedule": {"minute": "0", "hour": "0", "dom": "*", "month": "*", "dow": "*"},
                    }
                ],
            )
        )
        respx.get(f"{BASE}/replication").mock(return_value=httpx.Response(200, json=[]))

        result = await truenas_snapshots.ainvoke({})
        assert "tank/media@auto-2024-01-15" in result
        assert "tank/media" in result
        assert "14DAY" in result

    @respx.mock
    async def test_dataset_filter(self) -> None:
        route = respx.get(f"{BASE}/zfs/snapshot").mock(return_value=httpx.Response(200, json=[]))
        respx.get(f"{BASE}/pool/snapshottask").mock(return_value=httpx.Response(200, json=[]))
        respx.get(f"{BASE}/replication").mock(return_value=httpx.Response(200, json=[]))

        await truenas_snapshots.ainvoke({"dataset": "tank/media"})
        assert route.called
        assert route.calls.last.request.url.params["dataset"] == "tank/media"

    @respx.mock
    async def test_connect_error(self) -> None:
        respx.get(f"{BASE}/zfs/snapshot").mock(side_effect=httpx.ConnectError("Connection refused"))

        result = await truenas_snapshots.ainvoke({})
        assert "Cannot connect" in result

    @respx.mock
    async def test_timeout(self) -> None:
        respx.get(f"{BASE}/zfs/snapshot").mock(side_effect=httpx.ReadTimeout("Read timed out"))

        result = await truenas_snapshots.ainvoke({})
        assert "timed out" in result

    @respx.mock
    async def test_auth_error(self) -> None:
        respx.get(f"{BASE}/zfs/snapshot").mock(return_value=httpx.Response(401, text="Not authenticated"))

        result = await truenas_snapshots.ainvoke({})
        assert "401" in result


@pytest.mark.integration
class TestTruenasSystemStatus:
    @respx.mock
    async def test_full_system_status(self) -> None:
        respx.get(f"{BASE}/system/info").mock(
            return_value=httpx.Response(
                200,
                json={
                    "version": "TrueNAS-SCALE-24.04.2",
                    "hostname": "truenas",
                    "uptime_seconds": 864000,
                    "system_product": "Supermicro",
                    "physical_mem": 64 * 1024**3,
                },
            )
        )
        respx.get(f"{BASE}/alert/list").mock(
            return_value=httpx.Response(
                200,
                json=[{"level": "WARNING", "formatted": "Pool 80% full", "dismissed": False}],
            )
        )
        respx.get(f"{BASE}/core/get_jobs").mock(return_value=httpx.Response(200, json=[]))
        respx.get(f"{BASE}/disk").mock(
            return_value=httpx.Response(
                200,
                json=[{"name": "sda", "model": "WDC", "serial": "ABC", "type": "HDD", "size": 8 * 1024**4}],
            )
        )

        result = await truenas_system_status.ainvoke({})
        assert "TrueNAS-SCALE-24.04.2" in result
        assert "10 days" in result
        assert "WARNING" in result
        assert "sda" in result

    @respx.mock
    async def test_connect_error(self) -> None:
        respx.get(f"{BASE}/system/info").mock(side_effect=httpx.ConnectError("Connection refused"))

        result = await truenas_system_status.ainvoke({})
        assert "Cannot connect" in result

    @respx.mock
    async def test_timeout(self) -> None:
        respx.get(f"{BASE}/system/info").mock(side_effect=httpx.ReadTimeout("Read timed out"))

        result = await truenas_system_status.ainvoke({})
        assert "timed out" in result

    @respx.mock
    async def test_auth_error(self) -> None:
        respx.get(f"{BASE}/system/info").mock(return_value=httpx.Response(401, text="Not authenticated"))

        result = await truenas_system_status.ainvoke({})
        assert "401" in result

    @respx.mock
    async def test_sends_bearer_auth(self) -> None:
        route = respx.get(f"{BASE}/system/info").mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{BASE}/alert/list").mock(return_value=httpx.Response(200, json=[]))
        respx.get(f"{BASE}/core/get_jobs").mock(return_value=httpx.Response(200, json=[]))
        respx.get(f"{BASE}/disk").mock(return_value=httpx.Response(200, json=[]))

        await truenas_system_status.ainvoke({})
        assert route.called
        auth_header = route.calls.last.request.headers["authorization"]
        assert auth_header.startswith("Bearer ")


@pytest.mark.integration
class TestTruenasApps:
    @respx.mock
    async def test_lists_apps(self) -> None:
        respx.get(f"{BASE}/app").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"name": "alloy", "state": "RUNNING", "human_version": "1.5.0"},
                    {"name": "disk-status-exporter", "state": "STOPPED", "version": "0.3.0"},
                ],
            )
        )

        result = await truenas_apps.ainvoke({})
        assert "2 app(s)" in result
        assert "alloy" in result
        assert "disk-status-exporter" in result
        assert "RUNNING" in result
        assert "STOPPED" in result

    @respx.mock
    async def test_empty_apps(self) -> None:
        respx.get(f"{BASE}/app").mock(return_value=httpx.Response(200, json=[]))

        result = await truenas_apps.ainvoke({})
        assert "No apps found" in result

    @respx.mock
    async def test_connect_error(self) -> None:
        respx.get(f"{BASE}/app").mock(side_effect=httpx.ConnectError("Connection refused"))

        result = await truenas_apps.ainvoke({})
        assert "Cannot connect" in result

    @respx.mock
    async def test_timeout(self) -> None:
        respx.get(f"{BASE}/app").mock(side_effect=httpx.ReadTimeout("Read timed out"))

        result = await truenas_apps.ainvoke({})
        assert "timed out" in result

    @respx.mock
    async def test_auth_error(self) -> None:
        respx.get(f"{BASE}/app").mock(return_value=httpx.Response(401, text="Not authenticated"))

        result = await truenas_apps.ainvoke({})
        assert "401" in result


@pytest.mark.integration
class TestTruenasNotConfigured:
    async def test_pool_status_not_configured(self, mock_settings: Any) -> None:
        mock_settings.truenas_url = ""
        result = await truenas_pool_status.ainvoke({})
        assert "not configured" in result.lower()

    async def test_list_shares_not_configured(self, mock_settings: Any) -> None:
        mock_settings.truenas_url = ""
        result = await truenas_list_shares.ainvoke({})
        assert "not configured" in result.lower()

    async def test_snapshots_not_configured(self, mock_settings: Any) -> None:
        mock_settings.truenas_url = ""
        result = await truenas_snapshots.ainvoke({})
        assert "not configured" in result.lower()

    async def test_system_status_not_configured(self, mock_settings: Any) -> None:
        mock_settings.truenas_url = ""
        result = await truenas_system_status.ainvoke({})
        assert "not configured" in result.lower()

    async def test_apps_not_configured(self, mock_settings: Any) -> None:
        mock_settings.truenas_url = ""
        result = await truenas_apps.ainvoke({})
        assert "not configured" in result.lower()
