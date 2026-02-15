"""Tests for the Prometheus tool input validation and result formatting."""

from src.agent.tools.prometheus import (
    PrometheusData,
    PrometheusResponse,
    PrometheusSeries,
    _format_result,
    _parse_duration,
    _parse_timestamp,
    _validate_range_params,
)


class TestParseTimestamp:
    def test_unix_timestamp(self) -> None:
        assert _parse_timestamp("1700000000") == 1700000000.0

    def test_rfc3339(self) -> None:
        ts = _parse_timestamp("2024-01-15T10:00:00Z")
        assert isinstance(ts, float)
        assert ts > 0

    def test_rfc3339_with_offset(self) -> None:
        ts = _parse_timestamp("2024-01-15T10:00:00+01:00")
        assert isinstance(ts, float)


class TestParseDuration:
    def test_seconds(self) -> None:
        assert _parse_duration("60s") == 60.0

    def test_minutes(self) -> None:
        assert _parse_duration("5m") == 300.0

    def test_hours(self) -> None:
        assert _parse_duration("1h") == 3600.0

    def test_days(self) -> None:
        assert _parse_duration("1d") == 86400.0

    def test_numeric_string(self) -> None:
        assert _parse_duration("60") == 60.0

    def test_unparseable(self) -> None:
        assert _parse_duration("abc") is None


class TestValidateRangeParams:
    def test_valid_params(self) -> None:
        errors = _validate_range_params("1700000000", "1700003600", "60s")
        assert errors == []

    def test_end_before_start(self) -> None:
        errors = _validate_range_params("1700003600", "1700000000", "60s")
        assert any("end must be after start" in e for e in errors)

    def test_range_too_large(self) -> None:
        errors = _validate_range_params("1700000000", "1703000000", "60s")
        assert any("too large" in e.lower() for e in errors)

    def test_too_many_data_points(self) -> None:
        # 7 days with 1s step = too many points
        errors = _validate_range_params("1700000000", "1700604800", "1")
        assert any("too many data points" in e.lower() for e in errors)


class TestFormatResult:
    def test_empty_result(self) -> None:
        result_data: PrometheusData = {"resultType": "vector", "result": []}
        data: PrometheusResponse = {"status": "success", "data": result_data}
        output = _format_result(data)
        assert "no results" in output.lower()

    def test_error_status(self) -> None:
        data: PrometheusResponse = {"status": "error", "error": "bad query"}
        output = _format_result(data)
        assert "bad query" in output

    def test_vector_result(self) -> None:
        series: PrometheusSeries = {"metric": {"hostname": "jellyfin", "__name__": "up"}, "value": [1700000000, "1"]}
        result_data: PrometheusData = {"resultType": "vector", "result": [series]}
        data: PrometheusResponse = {"status": "success", "data": result_data}
        output = _format_result(data)
        assert "jellyfin" in output
        assert "1" in output

    def test_large_result_truncated(self) -> None:
        series_list: list[PrometheusSeries] = [
            {"metric": {"hostname": f"host-{i}"}, "value": [1700000000, str(i)]} for i in range(100)
        ]
        result_data: PrometheusData = {"resultType": "vector", "result": series_list}
        data: PrometheusResponse = {"status": "success", "data": result_data}
        output = _format_result(data)
        assert "WARNING" in output
        assert "first 50" in output
