"""Unit tests for TrueNAS tool formatting and helpers."""

import ssl

from src.agent.tools.truenas import (
    TruenasAlertEntry,
    TruenasAppEntry,
    TruenasDatasetEntry,
    TruenasDiskEntry,
    TruenasJobEntry,
    TruenasNfsShareEntry,
    TruenasPoolEntry,
    TruenasReplicationEntry,
    TruenasSmbShareEntry,
    TruenasSnapshotEntry,
    TruenasSnapshotTaskEntry,
    TruenasSystemInfo,
    _extract_topology_disks,
    _format_apps,
    _format_bytes,
    _format_cron_schedule,
    _format_pools,
    _format_shares,
    _format_snapshots,
    _format_system_status,
    _truenas_ssl_verify,
)


class TestFormatBytes:
    def test_bytes(self) -> None:
        assert _format_bytes(512) == "512.0 B"

    def test_gibibytes(self) -> None:
        result = _format_bytes(4 * 1024 * 1024 * 1024)
        assert "GiB" in result

    def test_tebibytes(self) -> None:
        result = _format_bytes(16 * 1024**4)
        assert "TiB" in result


class TestExtractTopologyDisks:
    def test_mirror_vdev(self) -> None:
        topology: dict[str, object] = {
            "data": [
                {
                    "type": "MIRROR",
                    "children": [
                        {"disk": "sdf", "status": "ONLINE"},
                        {"disk": "sdh", "status": "ONLINE"},
                    ],
                }
            ],
        }
        result = _extract_topology_disks(topology)
        assert ("data", "MIRROR", "sdf") in result
        assert ("data", "MIRROR", "sdh") in result

    def test_special_vdev(self) -> None:
        topology: dict[str, object] = {
            "special": [
                {
                    "type": "MIRROR",
                    "children": [
                        {"disk": "sdb", "status": "ONLINE"},
                        {"disk": "sdd", "status": "ONLINE"},
                    ],
                }
            ],
        }
        result = _extract_topology_disks(topology)
        assert ("special", "MIRROR", "sdb") in result
        assert ("special", "MIRROR", "sdd") in result

    def test_single_disk_vdev(self) -> None:
        topology: dict[str, object] = {
            "data": [{"type": "DISK", "disk": "sdg", "children": []}],
        }
        result = _extract_topology_disks(topology)
        assert ("data", "DISK", "sdg") in result

    def test_empty_topology(self) -> None:
        assert _extract_topology_disks({}) == []

    def test_multiple_categories(self) -> None:
        topology: dict[str, object] = {
            "data": [
                {"type": "MIRROR", "children": [{"disk": "sdc"}, {"disk": "sde"}]},
            ],
            "special": [
                {"type": "MIRROR", "children": [{"disk": "sdb"}, {"disk": "sdd"}]},
            ],
            "cache": [],
            "log": [],
        }
        result = _extract_topology_disks(topology)
        assert len(result) == 4
        data_disks = [r[2] for r in result if r[0] == "data"]
        special_disks = [r[2] for r in result if r[0] == "special"]
        assert set(data_disks) == {"sdc", "sde"}
        assert set(special_disks) == {"sdb", "sdd"}


class TestFormatPools:
    def test_empty_pools(self) -> None:
        assert "No ZFS pools found" in _format_pools([], [])

    def test_single_healthy_pool(self) -> None:
        pool: TruenasPoolEntry = {
            "name": "tank",
            "status": "ONLINE",
            "healthy": True,
            "size": 16 * 1024**4,
            "allocated": 8 * 1024**4,
            "free": 8 * 1024**4,
        }
        result = _format_pools([pool], [])
        assert "1 pool(s)" in result
        assert "tank" in result
        assert "ONLINE" in result
        assert "HEALTHY" in result
        assert "50.0%" in result

    def test_degraded_pool(self) -> None:
        pool: TruenasPoolEntry = {
            "name": "backup",
            "status": "DEGRADED",
            "healthy": False,
            "size": 1024**4,
            "allocated": 0,
            "free": 1024**4,
        }
        result = _format_pools([pool], [])
        assert "DEGRADED" in result
        assert "UNHEALTHY" in result

    def test_pool_with_topology(self) -> None:
        pool: TruenasPoolEntry = {
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
        result = _format_pools([pool], [])
        assert "Disk topology:" in result
        assert "data (MIRROR): sdf, sdh" in result
        assert "special (MIRROR): sdb, sdd" in result

    def test_pool_single_disk_vdev(self) -> None:
        """Single-disk vdev (stripe) where the vdev itself is the disk."""
        pool: TruenasPoolEntry = {
            "name": "swift",
            "status": "ONLINE",
            "healthy": True,
            "size": 1024**4,
            "allocated": 0,
            "free": 1024**4,
            "topology": {
                "data": [{"type": "DISK", "disk": "sdg", "children": []}],
            },
        }
        result = _format_pools([pool], [])
        assert "data (DISK): sdg" in result

    def test_pool_with_datasets(self) -> None:
        pool: TruenasPoolEntry = {
            "name": "tank",
            "status": "ONLINE",
            "healthy": True,
            "size": 16 * 1024**4,
            "allocated": 8 * 1024**4,
            "free": 8 * 1024**4,
        }
        dataset: TruenasDatasetEntry = {
            "id": "tank/media",
            "name": "media",
            "pool": "tank",
            "used": {"rawvalue": 4 * 1024**4},
            "available": {"rawvalue": 8 * 1024**4},
        }
        result = _format_pools([pool], [dataset])
        assert "tank/media" in result
        assert "Top-level datasets:" in result


class TestFormatShares:
    def test_empty_shares(self) -> None:
        result = _format_shares([], [], None)
        assert "NFS shares (0)" in result
        assert "SMB shares (0)" in result
        assert "(none)" in result

    def test_nfs_share(self) -> None:
        nfs: TruenasNfsShareEntry = {
            "path": "/mnt/tank/media",
            "enabled": True,
            "ro": False,
            "networks": ["192.168.2.0/24"],
            "comment": "Media share",
        }
        result = _format_shares([nfs], [], "nfs")
        assert "NFS shares (1)" in result
        assert "/mnt/tank/media" in result
        assert "enabled" in result
        assert "192.168.2.0/24" in result
        assert "Media share" in result

    def test_smb_share(self) -> None:
        smb: TruenasSmbShareEntry = {
            "name": "TimeMachine",
            "path": "/mnt/tank/timemachine",
            "enabled": True,
            "ro": False,
            "comment": "Time Machine backup",
        }
        result = _format_shares([], [smb], "smb")
        assert "SMB shares (1)" in result
        assert "TimeMachine" in result
        assert "/mnt/tank/timemachine" in result

    def test_disabled_readonly_share(self) -> None:
        nfs: TruenasNfsShareEntry = {
            "path": "/mnt/tank/archive",
            "enabled": False,
            "ro": True,
        }
        result = _format_shares([nfs], [], "nfs")
        assert "DISABLED" in result
        assert "read-only" in result

    def test_filter_nfs_only(self) -> None:
        nfs: TruenasNfsShareEntry = {"path": "/mnt/tank/data", "enabled": True}
        smb: TruenasSmbShareEntry = {"name": "test", "path": "/mnt/tank/test", "enabled": True}
        result = _format_shares([nfs], [smb], "nfs")
        assert "NFS shares" in result
        assert "SMB shares" not in result

    def test_filter_smb_only(self) -> None:
        nfs: TruenasNfsShareEntry = {"path": "/mnt/tank/data", "enabled": True}
        smb: TruenasSmbShareEntry = {"name": "test", "path": "/mnt/tank/test", "enabled": True}
        result = _format_shares([nfs], [smb], "smb")
        assert "SMB shares" in result
        assert "NFS shares" not in result


class TestFormatSnapshots:
    def test_empty_all(self) -> None:
        result = _format_snapshots([], [], [])
        assert "Recent snapshots (0)" in result
        assert "Snapshot schedules (0)" in result
        assert "Replication tasks (0)" in result

    def test_snapshot_listing(self) -> None:
        snap: TruenasSnapshotEntry = {
            "id": "tank/media@auto-2024-01-15_00-00",
        }
        result = _format_snapshots([snap], [], [])
        assert "Recent snapshots (1)" in result
        assert "tank/media@auto-2024-01-15_00-00" in result

    def test_snapshot_schedule(self) -> None:
        task: TruenasSnapshotTaskEntry = {
            "dataset": "tank/media",
            "enabled": True,
            "lifetime_value": 14,
            "lifetime_unit": "DAY",
            "recursive": True,
            "schedule": {"minute": "0", "hour": "0", "dom": "*", "month": "*", "dow": "*"},
        }
        result = _format_snapshots([], [task], [])
        assert "tank/media" in result
        assert "enabled" in result
        assert "14DAY" in result
        assert "recursive" in result
        assert "0 0 * * *" in result

    def test_replication_task(self) -> None:
        repl: TruenasReplicationEntry = {
            "name": "tank-to-backup",
            "enabled": True,
            "direction": "PUSH",
            "transport": "LOCAL",
            "source_datasets": ["tank/media"],
            "target_dataset": "backup/media",
            "state": {"state": "FINISHED"},
        }
        result = _format_snapshots([], [], [repl])
        assert "tank-to-backup" in result
        assert "PUSH" in result
        assert "LOCAL" in result
        assert "tank/media" in result
        assert "backup/media" in result
        assert "FINISHED" in result


class TestFormatCronSchedule:
    def test_full_schedule(self) -> None:
        sched = {"minute": "0", "hour": "*/6", "dom": "*", "month": "*", "dow": "*"}
        assert _format_cron_schedule(sched) == "0 */6 * * *"

    def test_empty_schedule(self) -> None:
        assert _format_cron_schedule({}) == "no schedule"


class TestFormatSystemStatus:
    def test_full_system_status(self) -> None:
        info: TruenasSystemInfo = {
            "version": "TrueNAS-SCALE-24.04.2",
            "hostname": "truenas",
            "uptime_seconds": 864000.0,
            "system_product": "Supermicro X11SCL-IF",
            "physmem": 64 * 1024**3,
            "cores": 8,
            "loadavg": [1.23, 0.98, 0.76],
            "ecc_memory": True,
        }
        alerts: list[TruenasAlertEntry] = [
            {"level": "WARNING", "formatted": "Pool tank is 80% full", "dismissed": False},
            {"level": "INFO", "formatted": "Update available", "dismissed": True},
        ]
        jobs: list[TruenasJobEntry] = [
            {"method": "pool.scrub", "state": "RUNNING", "progress": {"percent": 45, "description": "Scrubbing"}},
        ]
        disks: list[TruenasDiskEntry] = [
            {
                "name": "sda",
                "model": "WDC WD80EFPX",
                "serial": "ABC123",
                "type": "HDD",
                "size": 8 * 1024**4,
                "pool": "tank",
                "hddstandby": "10",
            },
        ]
        result = _format_system_status(info, alerts, jobs, disks)
        assert "TrueNAS-SCALE-24.04.2" in result
        assert "truenas" in result
        assert "10d 0h" in result
        assert "Supermicro" in result
        assert "ECC" in result
        assert "CPU cores: 8" in result
        assert "Load avg: 1.23" in result
        assert "1 active" in result
        assert "WARNING" in result
        assert "80% full" in result
        assert "pool.scrub" in result
        assert "45%" in result
        assert "sda" in result
        assert "WDC WD80EFPX" in result
        assert "ABC123" in result
        assert "pool=tank" in result
        assert "standby=10" in result

    def test_no_alerts_no_jobs(self) -> None:
        info: TruenasSystemInfo = {"version": "test", "hostname": "test"}
        result = _format_system_status(info, [], [], [])
        assert "(none active)" in result
        assert "Running jobs (0)" in result

    def test_dismissed_alerts_excluded(self) -> None:
        info: TruenasSystemInfo = {"version": "test"}
        alerts: list[TruenasAlertEntry] = [
            {"level": "WARNING", "formatted": "Test alert", "dismissed": True},
        ]
        result = _format_system_status(info, alerts, [], [])
        assert "0 active" in result
        assert "(none active)" in result


class TestFormatApps:
    def test_empty_apps(self) -> None:
        assert "No apps found" in _format_apps([])

    def test_running_and_stopped(self) -> None:
        apps: list[TruenasAppEntry] = [
            {"name": "alloy", "state": "RUNNING", "human_version": "1.0.0"},
            {"name": "disk-status-exporter", "state": "STOPPED", "version": "0.5.0"},
        ]
        result = _format_apps(apps)
        assert "2 app(s)" in result
        assert "1 running" in result
        assert "1 stopped" in result
        assert "+ alloy" in result
        assert "- disk-status-exporter" in result

    def test_upgrade_available(self) -> None:
        app: TruenasAppEntry = {
            "name": "netdata",
            "state": "RUNNING",
            "human_version": "1.0.0",
            "upgrade_available": True,
        }
        result = _format_apps([app])
        assert "[upgrade available]" in result

    def test_sorted_by_name(self) -> None:
        apps: list[TruenasAppEntry] = [
            {"name": "zzz", "state": "RUNNING", "version": "1.0"},
            {"name": "aaa", "state": "RUNNING", "version": "1.0"},
        ]
        result = _format_apps(apps)
        lines = result.split("\n")
        app_lines = [line for line in lines if line.strip().startswith("+") or line.strip().startswith("-")]
        assert "aaa" in app_lines[0]
        assert "zzz" in app_lines[1]


class TestTruenasSslVerify:
    def test_default_no_verify(self, mock_settings: object) -> None:
        result = _truenas_ssl_verify()
        assert result is False

    def test_verify_with_system_ca(self, mock_settings: object) -> None:
        mock_settings.truenas_verify_ssl = True  # type: ignore[attr-defined]
        result = _truenas_ssl_verify()
        assert result is True

    def test_verify_with_custom_ca(self, mock_settings: object) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            f.write(b"fake cert")
            cert_path = f.name

        mock_settings.truenas_verify_ssl = True  # type: ignore[attr-defined]
        mock_settings.truenas_ca_cert = cert_path  # type: ignore[attr-defined]
        try:
            result = _truenas_ssl_verify()
            assert isinstance(result, ssl.SSLContext)
        except ssl.SSLError:
            # Expected — fake cert is not valid PEM
            pass
