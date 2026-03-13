"""Unit tests for Grafana dashboard tool helpers (pure functions, no IO)."""

from src.agent.tools.grafana_dashboards import (
    _find_panel,
    _flatten_panels,
    _format_dashboard_summary,
    _format_field_config,
    _format_panel_detail,
    _format_search_results,
    _is_likely_uid,
)


class TestIsLikelyUid:
    def test_typical_grafana_uid(self) -> None:
        assert _is_likely_uid("dekkfibh9454wb") is True

    def test_uid_with_hyphens_underscores(self) -> None:
        assert _is_likely_uid("abc-123_def") is True

    def test_name_with_spaces(self) -> None:
        assert _is_likely_uid("Home Server") is False

    def test_long_string(self) -> None:
        assert _is_likely_uid("a" * 41) is False

    def test_empty_string(self) -> None:
        assert _is_likely_uid("") is False

    def test_short_uid(self) -> None:
        assert _is_likely_uid("abc") is True


class TestFlattenPanels:
    def test_flat_list(self) -> None:
        panels = [
            {"id": 1, "title": "A", "type": "timeseries"},
            {"id": 2, "title": "B", "type": "stat"},
        ]
        result = _flatten_panels(panels)
        assert len(result) == 2
        assert result[0]["title"] == "A"

    def test_row_with_nested_panels(self) -> None:
        panels = [
            {
                "id": 1,
                "title": "Row",
                "type": "row",
                "panels": [
                    {"id": 2, "title": "Nested A", "type": "timeseries"},
                    {"id": 3, "title": "Nested B", "type": "stat"},
                ],
            },
        ]
        result = _flatten_panels(panels)
        assert len(result) == 2
        assert result[0]["title"] == "Nested A"
        assert result[1]["title"] == "Nested B"

    def test_row_without_nested_panels(self) -> None:
        panels = [{"id": 1, "title": "Empty Row", "type": "row", "panels": []}]
        result = _flatten_panels(panels)
        assert len(result) == 1
        assert result[0]["title"] == "Empty Row"

    def test_mixed_rows_and_panels(self) -> None:
        panels = [
            {"id": 1, "title": "Standalone", "type": "gauge"},
            {
                "id": 2,
                "title": "Row",
                "type": "row",
                "panels": [{"id": 3, "title": "In Row", "type": "stat"}],
            },
        ]
        result = _flatten_panels(panels)
        assert len(result) == 2
        assert result[0]["title"] == "Standalone"
        assert result[1]["title"] == "In Row"

    def test_empty_list(self) -> None:
        assert _flatten_panels([]) == []


class TestFindPanel:
    def test_exact_match(self) -> None:
        panels = [
            {"title": "CPU Usage", "type": "timeseries"},
            {"title": "Memory Usage", "type": "timeseries"},
        ]
        found, others = _find_panel(panels, "CPU Usage")
        assert found is not None
        assert found["title"] == "CPU Usage"
        assert others == []

    def test_case_insensitive_match(self) -> None:
        panels = [{"title": "CPU Usage", "type": "timeseries"}]
        found, _ = _find_panel(panels, "cpu usage")
        assert found is not None
        assert found["title"] == "CPU Usage"

    def test_partial_match(self) -> None:
        panels = [{"title": "CPU per VM/LXC", "type": "timeseries"}]
        found, _ = _find_panel(panels, "CPU")
        assert found is not None
        assert found["title"] == "CPU per VM/LXC"

    def test_multiple_partial_matches(self) -> None:
        panels = [
            {"title": "CPU per VM", "type": "timeseries"},
            {"title": "CPU per Container", "type": "timeseries"},
        ]
        found, others = _find_panel(panels, "CPU")
        assert found is not None
        assert found["title"] == "CPU per VM"
        assert "CPU per Container" in others

    def test_no_match(self) -> None:
        panels = [{"title": "CPU Usage", "type": "timeseries"}]
        found, others = _find_panel(panels, "Disk IO")
        assert found is None
        assert others == []

    def test_match_inside_row(self) -> None:
        panels = [
            {
                "type": "row",
                "title": "Compute",
                "panels": [{"title": "CPU Usage", "type": "timeseries"}],
            }
        ]
        found, _ = _find_panel(panels, "CPU Usage")
        assert found is not None
        assert found["title"] == "CPU Usage"


class TestFormatSearchResults:
    def test_empty_results(self) -> None:
        result = _format_search_results([])
        assert "No dashboards found" in result

    def test_single_result(self) -> None:
        results = [
            {
                "title": "Home Server",
                "uid": "abc123",
                "folderTitle": "General",
                "url": "/d/abc123/home-server",
                "tags": ["infra"],
            }
        ]
        result = _format_search_results(results)
        assert "Home Server" in result
        assert "abc123" in result
        assert "General" in result
        assert "1 dashboard" in result

    def test_multiple_results(self) -> None:
        results = [
            {"title": "Dashboard A", "uid": "aaa", "url": ""},
            {"title": "Dashboard B", "uid": "bbb", "url": ""},
        ]
        result = _format_search_results(results)
        assert "2 dashboard" in result
        assert "Dashboard A" in result
        assert "Dashboard B" in result


class TestFormatFieldConfig:
    def test_thresholds(self) -> None:
        fc = {
            "defaults": {
                "unit": "percent",
                "thresholds": {
                    "steps": [
                        {"value": None, "color": "green"},
                        {"value": 80, "color": "yellow"},
                        {"value": 90, "color": "red"},
                    ]
                },
            }
        }
        lines = _format_field_config(fc)
        text = "\n".join(lines)
        assert "percent" in text
        assert "80" in text
        assert "90" in text

    def test_overrides(self) -> None:
        fc = {
            "defaults": {},
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "CPU"},
                    "properties": [{"id": "color", "value": "red"}],
                }
            ],
        }
        lines = _format_field_config(fc)
        text = "\n".join(lines)
        assert "byName=CPU" in text
        assert "color" in text

    def test_empty_config(self) -> None:
        lines = _format_field_config({"defaults": {}})
        assert lines == []


class TestFormatPanelDetail:
    def test_panel_with_targets(self) -> None:
        panel = {
            "id": 1,
            "title": "CPU per VM",
            "type": "timeseries",
            "datasource": {"type": "prometheus", "uid": "abc"},
            "targets": [
                {
                    "refId": "A",
                    "expr": 'pve_cpu_usage_ratio{name=~"$hostname"} * 100',
                    "legendFormat": "{{name}}",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "unit": "percent",
                    "thresholds": {"steps": [{"value": None, "color": "green"}]},
                }
            },
        }
        templating = {"list": [{"name": "hostname", "type": "query", "query": "label_values(pve_guest_info, name)"}]}
        result = _format_panel_detail(panel, templating)
        assert "CPU per VM" in result
        assert "pve_cpu_usage_ratio" in result
        assert "percent" in result
        assert "hostname" in result

    def test_panel_without_targets(self) -> None:
        panel = {"id": 1, "title": "Text Panel", "type": "text", "targets": []}
        result = _format_panel_detail(panel)
        assert "Text Panel" in result
        assert "Queries" not in result


class TestFormatDashboardSummary:
    def test_full_dashboard(self) -> None:
        data = {
            "dashboard": {
                "title": "Home Server",
                "uid": "dekkfibh9454wb",
                "tags": ["homelab"],
                "refresh": "10s",
                "panels": [
                    {
                        "id": 1,
                        "title": "CPU",
                        "type": "timeseries",
                        "datasource": {"type": "prometheus", "uid": "prom1"},
                        "targets": [{"expr": "pve_cpu_usage_ratio", "refId": "A"}],
                    },
                    {
                        "id": 2,
                        "title": "Memory",
                        "type": "stat",
                        "datasource": {"type": "prometheus", "uid": "prom1"},
                        "targets": [{"expr": "node_memory_MemTotal_bytes", "refId": "A"}],
                    },
                ],
                "templating": {
                    "list": [
                        {
                            "name": "hostname",
                            "type": "query",
                            "query": "label_values(pve_guest_info, name)",
                            "current": {"text": "All"},
                        }
                    ]
                },
                "annotations": {"list": []},
                "links": [],
            },
            "meta": {"folderTitle": "General"},
        }
        result = _format_dashboard_summary(data)
        assert "Home Server" in result
        assert "dekkfibh9454wb" in result
        assert "homelab" in result
        assert "2" in result  # panel count
        assert "$hostname" in result
        assert "CPU" in result
        assert "Memory" in result

    def test_empty_dashboard(self) -> None:
        data = {
            "dashboard": {"title": "Empty", "uid": "empty", "panels": [], "templating": {}},
            "meta": {},
        }
        result = _format_dashboard_summary(data)
        assert "Empty" in result
        assert "Panels (0)" in result
