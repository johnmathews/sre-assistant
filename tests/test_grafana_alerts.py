"""Unit tests for Grafana alerting tool formatting functions."""

from src.agent.tools.grafana_alerts import (
    GrafanaAlert,
    GrafanaAlertGroup,
    GrafanaAlertRule,
    _format_alert_rules,
    _format_alerts,
)


class TestFormatAlerts:
    def test_no_alerts(self) -> None:
        output = _format_alerts([], None)
        assert "No alerts found" in output

    def test_no_alerts_with_filter(self) -> None:
        output = _format_alerts([], "active")
        assert "No alerts with state 'active'" in output

    def test_single_firing_alert(self) -> None:
        alert: GrafanaAlert = {
            "labels": {"alertname": "HighCPU", "severity": "warning", "hostname": "jellyfin"},
            "annotations": {"summary": "CPU usage above 90%"},
            "startsAt": "2024-01-15T10:00:00Z",
            "status": {"state": "active"},
        }
        group: GrafanaAlertGroup = {
            "labels": {"grafana_folder": "Infrastructure"},
            "alerts": [alert],
        }
        output = _format_alerts([group], None)
        assert "HighCPU" in output
        assert "warning" in output
        assert "active" in output
        assert "CPU usage above 90%" in output
        assert "jellyfin" in output

    def test_state_filter_excludes_non_matching(self) -> None:
        active_alert: GrafanaAlert = {
            "labels": {"alertname": "HighCPU"},
            "annotations": {},
            "startsAt": "2024-01-15T10:00:00Z",
            "status": {"state": "active"},
        }
        suppressed_alert: GrafanaAlert = {
            "labels": {"alertname": "DiskFull"},
            "annotations": {},
            "startsAt": "2024-01-15T09:00:00Z",
            "status": {"state": "suppressed"},
        }
        group: GrafanaAlertGroup = {
            "labels": {},
            "alerts": [active_alert, suppressed_alert],
        }
        output = _format_alerts([group], "active")
        assert "HighCPU" in output
        assert "DiskFull" not in output

    def test_multiple_groups(self) -> None:
        alert1: GrafanaAlert = {
            "labels": {"alertname": "HighCPU"},
            "annotations": {},
            "startsAt": "2024-01-15T10:00:00Z",
            "status": {"state": "active"},
        }
        alert2: GrafanaAlert = {
            "labels": {"alertname": "LowDisk"},
            "annotations": {},
            "startsAt": "2024-01-15T11:00:00Z",
            "status": {"state": "active"},
        }
        groups: list[GrafanaAlertGroup] = [
            {"labels": {"grafana_folder": "Infra"}, "alerts": [alert1]},
            {"labels": {"grafana_folder": "Storage"}, "alerts": [alert2]},
        ]
        output = _format_alerts(groups, None)
        assert "2 alert(s)" in output
        assert "HighCPU" in output
        assert "LowDisk" in output


class TestFormatAlertRules:
    def test_no_rules(self) -> None:
        output = _format_alert_rules([])
        assert "No alert rules found" in output

    def test_single_rule(self) -> None:
        rule: GrafanaAlertRule = {
            "uid": "abc123",
            "title": "High CPU Usage",
            "folderUID": "infra",
            "ruleGroup": "node-alerts",
            "labels": {"severity": "warning"},
            "annotations": {"summary": "CPU > 90% for 5 minutes"},
        }
        output = _format_alert_rules([rule])
        assert "High CPU Usage" in output
        assert "abc123" in output
        assert "warning" in output
        assert "CPU > 90%" in output

    def test_multiple_rules(self) -> None:
        rules: list[GrafanaAlertRule] = [
            {"uid": "r1", "title": "High CPU", "labels": {"severity": "warning"}, "annotations": {}},
            {"uid": "r2", "title": "Low Disk", "labels": {"severity": "critical"}, "annotations": {}},
        ]
        output = _format_alert_rules(rules)
        assert "2 alert rule(s)" in output
        assert "High CPU" in output
        assert "Low Disk" in output
