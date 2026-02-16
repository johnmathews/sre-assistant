"""Unit tests for PBS tool formatting and helpers."""

import ssl

from src.agent.tools.pbs import (
    PbsBackupGroup,
    PbsDatastoreStatus,
    PbsTaskEntry,
    _format_backup_groups,
    _format_bytes,
    _format_datastore_status,
    _format_pbs_tasks,
    _pbs_ssl_verify,
)


class TestFormatBytes:
    def test_bytes(self) -> None:
        assert _format_bytes(512) == "512.0 B"

    def test_gibibytes(self) -> None:
        result = _format_bytes(4 * 1024 * 1024 * 1024)
        assert "GiB" in result

    def test_tebibytes(self) -> None:
        result = _format_bytes(2 * 1024**4)
        assert "TiB" in result


class TestFormatDatastoreStatus:
    def test_empty_list(self) -> None:
        assert "No datastores" in _format_datastore_status([])

    def test_single_datastore(self) -> None:
        store: PbsDatastoreStatus = {
            "store": "backups",
            "total": 2 * 1024**4,
            "used": 1 * 1024**4,
            "avail": 1 * 1024**4,
        }
        result = _format_datastore_status([store])
        assert "1 datastore(s)" in result
        assert "backups:" in result
        assert "50.0%" in result
        assert "TiB" in result

    def test_gc_status_shown(self) -> None:
        store: PbsDatastoreStatus = {
            "store": "backups",
            "total": 100,
            "used": 50,
            "avail": 50,
            "gc_status": {"last-run-state": "ok"},
        }
        result = _format_datastore_status([store])
        assert "Last GC: ok" in result

    def test_gc_status_hyphenated_key(self) -> None:
        """PBS API returns 'gc-status' with a hyphen."""
        store: dict[str, object] = {
            "store": "backups",
            "total": 100,
            "used": 50,
            "avail": 50,
            "gc-status": {"last-run-state": "ok"},
        }
        result = _format_datastore_status([store])  # type: ignore[list-item]
        assert "Last GC: ok" in result


class TestFormatBackupGroups:
    def test_empty_list(self) -> None:
        result = _format_backup_groups([], "backups")
        assert "No backup groups" in result
        assert "backups" in result

    def test_single_vm_backup(self) -> None:
        group: PbsBackupGroup = {
            "backup_type": "vm",
            "backup_id": "100",
            "backup_count": 5,
            "last_backup": 1700000000,
            "owner": "root@pam",
        }
        result = _format_backup_groups([group], "backups")
        assert "1 backup group(s)" in result
        assert "VM/100" in result
        assert "5 backup(s)" in result
        assert "root@pam" in result

    def test_multiple_types(self) -> None:
        groups: list[PbsBackupGroup] = [
            {"backup_type": "vm", "backup_id": "100", "backup_count": 3, "last_backup": 1700000000},
            {"backup_type": "ct", "backup_id": "101", "backup_count": 2, "last_backup": 1700000000},
            {"backup_type": "host", "backup_id": "proxmox", "backup_count": 1, "last_backup": 1700000000},
        ]
        result = _format_backup_groups(groups, "backups")
        assert "3 backup group(s)" in result
        assert "VM/100" in result
        assert "CT/101" in result
        assert "Host/proxmox" in result

    def test_sorted_by_id(self) -> None:
        groups: list[PbsBackupGroup] = [
            {"backup_type": "vm", "backup_id": "200", "backup_count": 1, "last_backup": 1700000000},
            {"backup_type": "vm", "backup_id": "100", "backup_count": 1, "last_backup": 1700000000},
        ]
        result = _format_backup_groups(groups, "backups")
        lines = result.split("\n")
        group_lines = [line for line in lines if "VM/" in line]
        assert "100" in group_lines[0]
        assert "200" in group_lines[1]

    def test_hyphenated_keys_from_real_api(self) -> None:
        """PBS API returns hyphenated keys — verify they are handled correctly."""
        group: dict[str, object] = {
            "backup-type": "vm",
            "backup-id": "100",
            "backup-count": 5,
            "last-backup": 1700000000,
            "owner": "root@pam",
        }
        result = _format_backup_groups([group], "backups")  # type: ignore[list-item]
        assert "VM/100" in result
        assert "5 backup(s)" in result
        assert "root@pam" in result

    def test_comment_shown(self) -> None:
        group: PbsBackupGroup = {
            "backup_type": "vm",
            "backup_id": "100",
            "backup_count": 1,
            "last_backup": 1700000000,
            "comment": "daily backup",
        }
        result = _format_backup_groups([group], "backups")
        assert "daily backup" in result


class TestFormatPbsTasks:
    def test_empty_list(self) -> None:
        assert "No recent PBS tasks" in _format_pbs_tasks([])

    def test_successful_task(self) -> None:
        task: PbsTaskEntry = {
            "worker_type": "backup",
            "worker_id": "vm/100",
            "status": "OK",
            "user": "root@pam",
            "starttime": 1700000000,
            "endtime": 1700003600,
        }
        result = _format_pbs_tasks([task])
        assert "1 recent PBS task(s)" in result
        assert "[OK]" in result
        assert "backup" in result
        assert "(vm/100)" in result

    def test_failed_task(self) -> None:
        task: PbsTaskEntry = {
            "worker_type": "backup",
            "worker_id": "vm/200",
            "status": "ERROR: backup failed",
            "user": "root@pam",
            "starttime": 1700000000,
        }
        result = _format_pbs_tasks([task])
        assert "ERROR" in result


class TestPbsSslVerify:
    def test_default_no_verify(self, mock_settings: object) -> None:
        result = _pbs_ssl_verify()
        assert result is False

    def test_verify_with_system_ca(self, mock_settings: object) -> None:
        mock_settings.pbs_verify_ssl = True  # type: ignore[attr-defined]
        result = _pbs_ssl_verify()
        assert result is True

    def test_verify_with_custom_ca(self, mock_settings: object) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            f.write(b"fake cert")
            cert_path = f.name

        mock_settings.pbs_verify_ssl = True  # type: ignore[attr-defined]
        mock_settings.pbs_ca_cert = cert_path  # type: ignore[attr-defined]
        try:
            result = _pbs_ssl_verify()
            assert isinstance(result, ssl.SSLContext)
        except ssl.SSLError:
            pass
