"""Tests for the Prometheus tool input validation and result formatting."""

from src.agent.tools.prometheus import (
    MAX_SEARCH_RESULTS,
    PrometheusData,
    PrometheusMetadataEntry,
    PrometheusResponse,
    PrometheusSeries,
    _format_result,
    _format_search_results,
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

    def test_weeks(self) -> None:
        assert _parse_duration("1w") == 604800.0

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

    def test_matrix_result_shows_summary_stats(self) -> None:
        """Matrix results include min/max/avg so the agent sees the full range."""
        series: PrometheusSeries = {
            "metric": {"name": "ether1", "__name__": "mktxp_interface_download_bytes_per_second"},
            "values": [
                [1700000000, "-5000"],
                [1700003600, "-100000"],
                [1700007200, "-2000"],
                [1700010800, "-350000"],
                [1700014400, "-8000"],
                [1700018000, "-1500"],
                [1700021600, "-12000"],
            ],
        }
        result_data: PrometheusData = {"resultType": "matrix", "result": [series]}
        data: PrometheusResponse = {"status": "success", "data": result_data}
        output = _format_result(data)
        assert "7 samples" in output
        assert "min: -3.5e+05" in output
        assert "max: -1500" in output
        assert "avg:" in output

    def test_matrix_summary_stats_with_positive_values(self) -> None:
        """Summary stats work correctly with positive values."""
        series: PrometheusSeries = {
            "metric": {"hostname": "media"},
            "values": [
                [1700000000, "10"],
                [1700003600, "50"],
                [1700007200, "30"],
            ],
        }
        result_data: PrometheusData = {"resultType": "matrix", "result": [series]}
        data: PrometheusResponse = {"status": "success", "data": result_data}
        output = _format_result(data)
        assert "min: 10" in output
        assert "max: 50" in output
        assert "avg: 30" in output


class TestFormatSearchResults:
    def test_formats_metrics_with_metadata(self) -> None:
        names = ["mktxp_dhcp_lease_count", "mktxp_dhcp_lease_info"]
        metadata: dict[str, list[PrometheusMetadataEntry]] = {
            "mktxp_dhcp_lease_count": [{"type": "gauge", "help": "Number of active DHCP leases", "unit": ""}],
            "mktxp_dhcp_lease_info": [{"type": "gauge", "help": "DHCP lease information", "unit": ""}],
        }
        output = _format_search_results(names, metadata, "mktxp_dhcp")
        assert "Found 2 metrics" in output
        assert "mktxp_dhcp_lease_count (gauge): Number of active DHCP leases" in output
        assert "mktxp_dhcp_lease_info (gauge): DHCP lease information" in output
        assert "prometheus_instant_query" in output

    def test_empty_results_shows_suggestions(self) -> None:
        output = _format_search_results([], {}, "nonexistent")
        assert "No metrics found" in output
        assert "nonexistent" in output
        assert "node_" in output  # suggests common prefixes

    def test_truncates_at_max_search_results(self) -> None:
        names = [f"metric_{i:04d}" for i in range(80)]
        output = _format_search_results(names, {}, "metric")
        assert f"first {MAX_SEARCH_RESULTS} of 80" in output
        # Should only show MAX_SEARCH_RESULTS metric lines
        metric_lines = [line for line in output.split("\n") if line.startswith("  metric_")]
        assert len(metric_lines) == MAX_SEARCH_RESULTS

    def test_handles_missing_metadata(self) -> None:
        names = ["up", "node_cpu_seconds_total"]
        output = _format_search_results(names, {}, "node")
        assert "  up" in output
        assert "  node_cpu_seconds_total" in output
        # No parenthesized type info since metadata is empty
        assert "(gauge)" not in output

    def test_results_sorted_alphabetically(self) -> None:
        names = ["z_metric", "a_metric", "m_metric"]
        output = _format_search_results(names, {}, "metric")
        lines = [line.strip() for line in output.split("\n") if line.startswith("  ")]
        assert lines == ["a_metric", "m_metric", "z_metric"]

    def test_metadata_with_type_only(self) -> None:
        names = ["up"]
        metadata: dict[str, list[PrometheusMetadataEntry]] = {
            "up": [{"type": "gauge", "help": "", "unit": ""}],
        }
        output = _format_search_results(names, metadata, "up")
        assert "up (gauge)" in output
        # No trailing colon when help is empty
        assert "up (gauge):" not in output
