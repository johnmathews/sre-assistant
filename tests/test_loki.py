"""Unit tests for Loki tool helpers: time parsing, formatting, validation, timeline building."""

from datetime import UTC, datetime, timedelta

import pytest

from src.agent.tools.loki import (
    LokiQueryResponse,
    LokiStreamValues,
    _build_timeline,
    _datetime_to_nanoseconds,
    _extract_events_from_response,
    _format_label_values,
    _format_log_lines,
    _parse_relative_time,
    _TimelineEvent,
)


class TestParseRelativeTime:
    def test_now(self) -> None:
        before = datetime.now(tz=UTC)
        result = _parse_relative_time("now")
        after = datetime.now(tz=UTC)
        assert before <= result <= after

    def test_seconds(self) -> None:
        expected = datetime.now(tz=UTC) - timedelta(seconds=30)
        result = _parse_relative_time("30s")
        assert abs((result - expected).total_seconds()) < 1

    def test_minutes(self) -> None:
        result = _parse_relative_time("5m")
        expected = datetime.now(tz=UTC) - timedelta(minutes=5)
        assert abs((result - expected).total_seconds()) < 1

    def test_hours(self) -> None:
        result = _parse_relative_time("1h")
        expected = datetime.now(tz=UTC) - timedelta(hours=1)
        assert abs((result - expected).total_seconds()) < 1

    def test_days(self) -> None:
        result = _parse_relative_time("2d")
        expected = datetime.now(tz=UTC) - timedelta(days=2)
        assert abs((result - expected).total_seconds()) < 1

    def test_weeks(self) -> None:
        result = _parse_relative_time("1w")
        expected = datetime.now(tz=UTC) - timedelta(weeks=1)
        assert abs((result - expected).total_seconds()) < 1

    def test_iso_timestamp(self) -> None:
        result = _parse_relative_time("2024-06-15T14:00:00Z")
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 14

    def test_iso_timestamp_with_offset(self) -> None:
        result = _parse_relative_time("2024-06-15T14:00:00+02:00")
        assert result.year == 2024
        assert result.tzinfo is not None

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse time"):
            _parse_relative_time("invalid")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse time"):
            _parse_relative_time("")


class TestDatetimeToNanoseconds:
    def test_converts_correctly(self) -> None:
        dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        result = _datetime_to_nanoseconds(dt)
        expected_seconds = int(dt.timestamp())
        assert result == str(expected_seconds * 1_000_000_000)

    def test_returns_string(self) -> None:
        dt = datetime.now(tz=UTC)
        result = _datetime_to_nanoseconds(dt)
        assert isinstance(result, str)
        assert result.isdigit()


class TestFormatLogLines:
    def test_empty_result(self) -> None:
        data: LokiQueryResponse = {
            "status": "success",
            "data": {"resultType": "streams", "result": []},
        }
        output = _format_log_lines(data, 100)
        assert "No log lines found" in output

    def test_error_status(self) -> None:
        data: LokiQueryResponse = {"status": "error"}
        output = _format_log_lines(data, 100)
        assert "failed" in output.lower()

    def test_formats_log_lines(self) -> None:
        stream: LokiStreamValues = {
            "stream": {
                "hostname": "media",
                "service_name": "traefik",
                "detected_level": "error",
            },
            "values": [
                ["1700000000000000000", "Connection refused to backend"],
                ["1700000001000000000", "Retrying connection"],
            ],
        }
        data: LokiQueryResponse = {
            "status": "success",
            "data": {"resultType": "streams", "result": [stream]},
        }
        output = _format_log_lines(data, 100)
        assert "Found 2 log lines" in output
        assert "media" in output
        assert "traefik" in output
        assert "Connection refused" in output

    def test_respects_limit(self) -> None:
        values = [[f"{1700000000000000000 + i * 1000000000}", f"Line {i}"] for i in range(50)]
        stream: LokiStreamValues = {
            "stream": {"hostname": "test"},
            "values": values,
        }
        data: LokiQueryResponse = {
            "status": "success",
            "data": {"resultType": "streams", "result": [stream]},
        }
        output = _format_log_lines(data, 10)
        assert "limited to 10" in output
        # Count actual log lines (lines starting with [)
        log_lines = [line for line in output.split("\n") if line.startswith("[")]
        assert len(log_lines) == 10

    def test_truncates_long_lines(self) -> None:
        long_message = "x" * 1000
        stream: LokiStreamValues = {
            "stream": {"hostname": "test"},
            "values": [["1700000000000000000", long_message]],
        }
        data: LokiQueryResponse = {
            "status": "success",
            "data": {"resultType": "streams", "result": [stream]},
        }
        output = _format_log_lines(data, 100)
        assert "..." in output
        # Ensure the full 1000 char line isn't present
        assert long_message not in output


class TestFormatLabelValues:
    def test_empty_values(self) -> None:
        output = _format_label_values([], "hostname")
        assert "No values found" in output

    def test_formats_values(self) -> None:
        values = ["media", "infra", "jellyfin"]
        output = _format_label_values(values, "hostname")
        assert "Found 3 values" in output
        assert "infra" in output
        assert "jellyfin" in output
        assert "media" in output

    def test_values_sorted(self) -> None:
        values = ["zebra", "apple", "mango"]
        output = _format_label_values(values, "service_name")
        lines = [line.strip() for line in output.split("\n") if line.startswith("  ")]
        assert lines == ["apple", "mango", "zebra"]


class TestExtractEventsFromResponse:
    def test_extracts_events(self) -> None:
        stream: LokiStreamValues = {
            "stream": {
                "hostname": "infra",
                "service_name": "traefik",
                "detected_level": "error",
            },
            "values": [
                ["1700000000000000000", "502 Bad Gateway"],
                ["1700000001000000000", "Connection reset"],
            ],
        }
        data: LokiQueryResponse = {
            "status": "success",
            "data": {"resultType": "streams", "result": [stream]},
        }
        events = _extract_events_from_response(data)
        assert len(events) == 2
        assert events[0].service == "traefik"
        assert events[0].hostname == "infra"
        assert events[0].level == "error"
        assert "502" in events[0].message

    def test_respects_max_events(self) -> None:
        values = [[f"{1700000000000000000 + i * 1000000000}", f"Event {i}"] for i in range(50)]
        stream: LokiStreamValues = {
            "stream": {"hostname": "test", "service_name": "svc"},
            "values": values,
        }
        data: LokiQueryResponse = {
            "status": "success",
            "data": {"resultType": "streams", "result": [stream]},
        }
        events = _extract_events_from_response(data, max_events=5)
        assert len(events) == 5

    def test_empty_response(self) -> None:
        data: LokiQueryResponse = {
            "status": "success",
            "data": {"resultType": "streams", "result": []},
        }
        events = _extract_events_from_response(data)
        assert events == []

    def test_uses_container_label_as_fallback(self) -> None:
        stream: LokiStreamValues = {
            "stream": {
                "hostname": "media",
                "container": "jellyfin-app",
                "detected_level": "warn",
            },
            "values": [["1700000000000000000", "Slow transcode"]],
        }
        data: LokiQueryResponse = {
            "status": "success",
            "data": {"resultType": "streams", "result": [stream]},
        }
        events = _extract_events_from_response(data)
        assert events[0].service == "jellyfin-app"


class TestBuildTimeline:
    def test_empty_events(self) -> None:
        output = _build_timeline([])
        assert "No significant events" in output

    def test_builds_chronological_timeline(self) -> None:
        events = [
            _TimelineEvent(
                timestamp=datetime(2024, 6, 15, 14, 0, 0, tzinfo=UTC),
                service="traefik",
                hostname="infra",
                level="error",
                message="502 Bad Gateway",
            ),
            _TimelineEvent(
                timestamp=datetime(2024, 6, 15, 13, 55, 0, tzinfo=UTC),
                service="jellyfin",
                hostname="media",
                level="warn",
                message="Slow response",
            ),
        ]
        output = _build_timeline(events)
        assert "Found 2 significant events across 2 services" in output
        assert "Chronological Timeline" in output
        assert "Summary by Service" in output
        # Earlier event should come first
        lines = output.split("\n")
        timeline_lines = [line for line in lines if "13:55:00" in line or "14:00:00" in line]
        assert len(timeline_lines) == 2
        # 13:55 should appear before 14:00
        first_idx = next(i for i, line in enumerate(lines) if "13:55:00" in line)
        second_idx = next(i for i, line in enumerate(lines) if "14:00:00" in line)
        assert first_idx < second_idx

    def test_groups_by_service(self) -> None:
        events = [
            _TimelineEvent(
                timestamp=datetime(2024, 6, 15, 14, 0, 0, tzinfo=UTC),
                service="traefik",
                hostname="infra",
                level="error",
                message="Error 1",
            ),
            _TimelineEvent(
                timestamp=datetime(2024, 6, 15, 14, 1, 0, tzinfo=UTC),
                service="traefik",
                hostname="infra",
                level="error",
                message="Error 2",
            ),
            _TimelineEvent(
                timestamp=datetime(2024, 6, 15, 14, 2, 0, tzinfo=UTC),
                service="jellyfin",
                hostname="media",
                level="warn",
                message="Warning 1",
            ),
        ]
        output = _build_timeline(events)
        assert "infra/traefik: 2 events" in output
        assert "media/jellyfin: 1 events" in output
