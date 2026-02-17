"""Unit tests for the HDD power status composite tool."""

from src.agent.tools.disk_status import (
    POWER_STATE_LABELS,
    _build_disk_lookup,
    _extract_hex,
    _format_disk_name,
    _format_power_state,
)
from src.agent.tools.truenas import TruenasDiskEntry


class TestExtractHex:
    """Tests for hex extraction used in device_id ↔ identifier cross-referencing."""

    def test_extracts_from_prometheus_device_id(self) -> None:
        device_id = "/dev/disk/by-id/wwn-0x5000c500eb02b449"
        assert _extract_hex(device_id) == "5000c500eb02b449"

    def test_extracts_from_truenas_identifier(self) -> None:
        identifier = "{serial_lunid}5000c500eb02b449"
        assert _extract_hex(identifier) == "5000c500eb02b449"

    def test_extracts_longest_hex_sequence(self) -> None:
        # If multiple hex-like substrings, pick the longest
        s = "abc-12345678-9abcdef012345678"
        assert _extract_hex(s) == "9abcdef012345678"

    def test_returns_empty_for_no_hex(self) -> None:
        assert _extract_hex("no-hex-here") == ""
        assert _extract_hex("short-1234") == ""  # < 8 chars

    def test_case_insensitive(self) -> None:
        assert _extract_hex("ABCDEF0123456789") == "abcdef0123456789"

    def test_matching_prometheus_to_truenas(self) -> None:
        """Verify that the same hex is extracted from both formats for the same disk."""
        prom_id = "/dev/disk/by-id/wwn-0x5000c500f742ccbf"
        truenas_id = "{serial_lunid}5000c500f742ccbf"
        assert _extract_hex(prom_id) == _extract_hex(truenas_id)


class TestBuildDiskLookup:
    def test_builds_lookup_from_disk_entries(self) -> None:
        disks: list[TruenasDiskEntry] = [
            TruenasDiskEntry(
                identifier="{serial_lunid}5000c500eb02b449",
                name="sdc",
                model="ST8000VN004",
                serial="WWZ5TZSF",
                type="HDD",
                size=8_000_000_000_000,
            ),
            TruenasDiskEntry(
                identifier="{serial_lunid}5000c500f742ccbf",
                name="sdf",
                model="ST16000NT001",
                serial="K3S04BKQ",
                type="HDD",
                size=16_000_000_000_000,
            ),
        ]
        lookup = _build_disk_lookup(disks)
        assert "5000c500eb02b449" in lookup
        assert lookup["5000c500eb02b449"]["name"] == "sdc"
        assert "5000c500f742ccbf" in lookup
        assert lookup["5000c500f742ccbf"]["name"] == "sdf"

    def test_ignores_entries_without_hex_identifier(self) -> None:
        disks: list[TruenasDiskEntry] = [
            TruenasDiskEntry(identifier="", name="sda"),
        ]
        lookup = _build_disk_lookup(disks)
        assert len(lookup) == 0


class TestFormatDiskName:
    def test_formats_with_disk_entry(self) -> None:
        disk = TruenasDiskEntry(
            name="sdc",
            model="ST8000VN004",
            serial="WWZ5TZSF",
            size=8_000_000_000_000,
        )
        result = _format_disk_name(disk, "irrelevant")
        assert "sdc" in result
        assert "ST8000VN004" in result
        assert "WWZ5TZSF" in result

    def test_falls_back_to_short_device_id(self) -> None:
        result = _format_disk_name(None, "/dev/disk/by-id/wwn-0x5000c500eb02b449")
        assert result == "wwn-0x5000c500eb02b449"
        assert "/dev/disk/by-id/" not in result

    def test_handles_device_id_without_path(self) -> None:
        result = _format_disk_name(None, "some-device-id")
        assert result == "some-device-id"


class TestFormatPowerState:
    def test_known_states(self) -> None:
        assert "standby" in _format_power_state(0)
        assert "idle" in _format_power_state(1)
        assert "active/idle" in _format_power_state(2)
        assert "unknown" in _format_power_state(-1)

    def test_unknown_state_includes_value(self) -> None:
        result = _format_power_state(3)
        assert "unknown state" in result
        assert "3" in result

    def test_all_documented_states_have_labels(self) -> None:
        for value in (-1, 0, 1, 2):
            assert value in POWER_STATE_LABELS
