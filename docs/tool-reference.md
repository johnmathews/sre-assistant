# Tool Reference

The agent has up to 22 tools across 8 categories. Tools are conditionally registered based on configuration.

## Prometheus (always enabled)

### prometheus_search_metrics

Search for available Prometheus metric names matching a substring.

- **Input:** `search_term` (string)
- **Example questions:** "What MikroTik metrics are available?", "Find CPU-related metrics"
- **Returns:** Matching metric names with type and description from metadata

### prometheus_instant_query

Query Prometheus for the current value of a metric (instant query).

- **Input:** `query` (PromQL string), `time` (optional RFC3339/Unix timestamp)
- **Example questions:** "What is the current CPU usage on jellyfin?", "How many VMs are running?"
- **Returns:** Formatted metric values with labels

### prometheus_range_query

Query Prometheus for metric values over a time range.

- **Input:** `query` (PromQL), `start`, `end` (timestamps), `step` (duration)
- **Example questions:** "How has CPU changed over the last hour?", "Show memory usage for the past day"
- **Returns:** Time series data with sample counts

## Grafana Alerting (always enabled)

### grafana_get_alerts

Fetch active alerts from Grafana's alerting system.

- **Input:** `state` (optional: "active", "suppressed", "unprocessed")
- **Example questions:** "What alerts are firing?", "Are there any active alerts?"
- **Returns:** Alert name, severity, state, labels, annotations, start time

### grafana_get_alert_rules

Fetch alert rule definitions from Grafana.

- **Input:** none
- **Example questions:** "What alerts are configured?", "What conditions trigger the high CPU alert?"
- **Returns:** Rule name, UID, folder, group, severity, summary

## Loki (enabled when `LOKI_URL` is set)

### loki_query_logs

Query Loki for log lines using LogQL.

- **Input:** `query` (LogQL string), `start` (relative like "1h" or ISO timestamp, default "1h"), `end` (relative or ISO, default "now"), `limit` (int, default 100, max 1000), `direction` ("forward" or "backward", default "backward")
- **Example questions:** "Show me recent logs from traefik", "What errors occurred on the media VM in the last 6 hours?"
- **Returns:** Formatted log lines with timestamps, stream labels, and log text

### loki_list_label_values

List available values for a Loki label.

- **Input:** `label` (string, e.g. "hostname", "service_name"), `query` (optional LogQL stream selector to scope results)
- **Example questions:** "What services are running on the infra VM?", "What hostnames send logs?"
- **Returns:** Sorted list of values for the given label

### loki_correlate_changes

Search for significant log events around a reference time for change correlation.

- **Input:** `reference_time` (ISO timestamp or "now"), `window_minutes` (int, default 30), `hostname` (optional filter), `service_name` (optional filter)
- **Example questions:** "What changed before this alert fired?", "Show me what happened around 2pm on the infra VM"
- **Returns:** Chronological timeline of error/warn/fatal events and container lifecycle events, grouped by service

## Proxmox VE (enabled when `PROXMOX_URL` is set)

### proxmox_list_guests

List all VMs and containers on the Proxmox node.

- **Input:** `guest_type` (optional: "qemu" or "lxc")
- **Example questions:** "What VMs are running?", "List all containers", "How many guests are there?"
- **Returns:** VMID, name, type (VM/CT), status, vCPUs, RAM, CPU usage

### proxmox_get_guest_config

Get the full configuration of a specific VM or container by VMID.

- **Input:** `vmid` (integer), `guest_type` (string, default "qemu")
- **Example questions:** "What disks does VM 100 have?", "Show the config for jellyfin"
- **Returns:** Grouped config: compute (CPU/RAM), disks, network, boot/OS settings

### proxmox_node_status

Get overall status of the Proxmox host node.

- **Input:** none
- **Example questions:** "How is the Proxmox server doing?", "What PVE version is running?"
- **Returns:** CPU%, memory, root FS, load average, uptime, PVE version, kernel

### proxmox_list_tasks

List recent Proxmox tasks (migrations, backups, snapshots, etc).

- **Input:** `limit` (int, default 20), `errors_only` (bool, default false)
- **Example questions:** "Any recent failed tasks?", "Is a migration running?"
- **Returns:** Task type, status, user, start/end time, guest ID

## TrueNAS SCALE (enabled when `TRUENAS_URL` is set)

### truenas_pool_status

Get ZFS pool health and dataset space usage from TrueNAS.

- **Input:** none
- **Example questions:** "Is the tank pool healthy?", "How much space is left on the NAS?", "Any degraded pools?", "How big is my photos dataset?", "How much space does tank/media use?"
- **Returns:** Pool status (ONLINE/DEGRADED/FAULTED), health flag, size/used/free space, disk topology, per-dataset used/available for each top-level dataset

### truenas_list_shares

List NFS and SMB shares configured on TrueNAS.

- **Input:** `share_type` (optional: "nfs" or "smb")
- **Example questions:** "What NFS shares exist?", "Is the paperless share enabled?", "Which SMB shares are configured?"
- **Returns:** Share path, enabled/disabled status, read-only flag, allowed networks/hosts, comments

### truenas_snapshots

List ZFS snapshots, snapshot schedules, and replication tasks on TrueNAS.

- **Input:** `dataset` (optional filter, e.g. "tank/media"), `limit` (int, default 50)
- **Example questions:** "When was the last snapshot of tank/media?", "Is replication running?", "What snapshot schedules exist?"
- **Returns:** Recent snapshots, periodic snapshot task schedules with retention, replication task status

### truenas_system_status

Get TrueNAS system information, alerts, running jobs, and disk inventory.

- **Input:** none
- **Example questions:** "Any TrueNAS alerts?", "What version is TrueNAS running?", "What disks does TrueNAS have?"
- **Returns:** Version, hostname, uptime, hardware, active alerts, running jobs with progress, disk inventory

### truenas_apps

List apps installed on TrueNAS SCALE with their running state.

- **Input:** none
- **Example questions:** "What apps are running on TrueNAS?", "Is Alloy running?", "Is the disk-status-exporter deployed?"
- **Returns:** App name, state (RUNNING/STOPPED/DEPLOYING), version, upgrade availability

## HDD Power Status (enabled when `TRUENAS_URL` is set)

### hdd_power_status

Complete HDD power status summary: current state, human-readable disk names, and transition history.

- **Input:** none
- **Example questions:** "Which HDDs are spun up?", "What was the last HDD to spin up?", "Are the drives spun down?", "When did the HDDs last change power state?"
- **Returns:** Per-disk power state (active/idle, standby) with model, size, serial, pool. Last power state change timestamp with from/to transition. Automatically cross-references Prometheus `disk_power_state` with TrueNAS disk inventory and uses progressive `changes()` widening to find transitions.

## Proxmox Backup Server (enabled when `PBS_URL` is set)

### pbs_datastore_status

Get storage usage for all PBS datastores.

- **Input:** none
- **Example questions:** "How much backup space is left?", "Is the backup store full?"
- **Returns:** Datastore name, total/used/available space, usage%, last GC status

### pbs_list_backups

List backup groups in a PBS datastore.

- **Input:** `datastore` (optional, defaults to `PBS_DEFAULT_DATASTORE`)
- **Example questions:** "When was VM 100 last backed up?", "Which VMs are being backed up?"
- **Returns:** Backup type (VM/CT/Host), ID, snapshot count, last backup time, owner

### pbs_list_tasks

List recent PBS tasks (backup jobs, GC, verification).

- **Input:** `limit` (int, default 20), `errors_only` (bool, default false)
- **Example questions:** "Did last night's backup succeed?", "Any failed backup tasks?"
- **Returns:** Task type, status, user, start/end time, worker ID

## RAG (enabled when vector store exists)

### runbook_search

Search operational runbooks for procedures, troubleshooting steps, and architecture docs.

- **Input:** `query` (string)
- **Example questions:** "How do I restart the DNS stack?", "What's the procedure for NFS issues?"
- **Returns:** Relevant runbook chunks with source attribution
