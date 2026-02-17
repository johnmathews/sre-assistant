# Disk Management

## Purpose

Monitor and manage disks across the homelab — Proxmox host SSDs, TrueNAS HDDs, and VM/LXC virtual disks. Includes SMART
monitoring, ZFS pool health, and disk replacement procedures.

## Key Commands

### Check disk usage (any host)

```sh
df -h
lsblk
```

### SMART health check

```sh
# Check SMART status
smartctl -a /dev/sdX

# Run short self-test
smartctl -t short /dev/sdX

# View test results
smartctl -l selftest /dev/sdX
```

### ZFS pool health (TrueNAS)

```sh
zpool status
zpool list
zfs list -o name,used,avail,refer,mountpoint
```

### Proxmox storage overview

```sh
pvesm status
```

## Prometheus Metrics

Key disk metrics to query:

```promql
# Disk space usage percentage
100 - (node_filesystem_avail_bytes / node_filesystem_size_bytes * 100)

# Disk I/O rate
rate(node_disk_read_bytes_total[5m])
rate(node_disk_written_bytes_total[5m])

# Disk I/O wait time
rate(node_disk_io_time_seconds_total[5m])
```

## HDD Spinup / Spindown Detection

TrueNAS HDDs spin down when idle and spin up on access. There are two complementary approaches
to detect spinup/spindown: the `disk_power_state` metric (direct power state) and
`node_disk_io_time_seconds_total` (indirect via IO activity).

### Primary: disk_power_state metric

The `disk-status-exporter` on TrueNAS exports `disk_power_state` with labels `device_id`,
`type` (hdd/ssd), and `pool`. Values: 0=standby, 1=idle, 2=active/idle, -1=unknown.

```promql
# Current power state of all HDDs
disk_power_state{type="hdd"}

# Check IF any HDD changed state in the last hour
changes(disk_power_state{type="hdd"}[1h])

# If changes() returns 0 for all disks, widen the window:
changes(disk_power_state{type="hdd"}[6h])
changes(disk_power_state{type="hdd"}[24h])
changes(disk_power_state{type="hdd"}[7d])
```

**Finding WHEN a disk last changed state:**

PromQL has no "last change timestamp" function. Use a progressive approach:

1. Use `changes(disk_power_state{type="hdd"}[1h])` — if all 0, widen to [6h], [24h], [7d]
2. Once you find a window where `changes() > 0`, use a range query with a small step:
   `disk_power_state{type="hdd"}` with step=15s over that window
3. Look for adjacent data points where the value differs — that's the transition timestamp
4. 0→non-zero = spin up, non-zero→0 = spin down

**Important:** A range query returning constant values means the disk has NOT changed state in
that window. That is valid data, not "no data" or "missing data". Only report "no data" if the
query returns zero series (the metric doesn't exist).

### Cross-referencing disk identity

The `device_id` labels in `disk_power_state` are opaque (e.g. `wwn-0x5000c500eb02b449`).
Always cross-reference with `truenas_list_disks` (or the `disk_info` metric) to report
human-readable disk names (model, size, serial number) instead of raw device IDs.

### Secondary: node_disk_io_time_seconds_total

As a fallback (if `disk_power_state` is unavailable), use IO time from node_exporter.
This counter only increases when a disk is doing IO — a spun-down disk shows zero increase.

```promql
# IO time increase per disk in the last 1 hour (non-zero = disk was active)
increase(node_disk_io_time_seconds_total{instance=~".*truenas.*"}[1h])

# Narrow to just HDDs by device name (cross-reference with truenas_list_disks)
increase(node_disk_io_time_seconds_total{instance=~".*truenas.*", device=~"sd[c-h]"}[1h])

# Shorter windows to find the MOST recent spinup
increase(node_disk_io_time_seconds_total{instance=~".*truenas.*", device=~"sd[c-h]"}[5m])
```

### Recommended agent strategy

When asked "which HDD spun up recently" or similar:

1. Call `truenas_list_disks` to get the disk inventory (device names, models, sizes, serial numbers)
2. Use `prometheus_instant_query` with `disk_power_state{type="hdd"}` for current state
3. Use `changes(disk_power_state{type="hdd"}[1h])` (widening as needed) to find recent transitions
4. Once a window with changes is found, use `prometheus_range_query` with step=15s to pinpoint when
5. Match the `device_id` label to TrueNAS disk names for human-readable output
6. Filter out SSDs — they don't spin up/down

### Related metrics

```promql
# Bytes read/written per disk (confirms what kind of activity)
rate(node_disk_read_bytes_total{instance=~".*truenas.*"}[5m])
rate(node_disk_written_bytes_total{instance=~".*truenas.*"}[5m])

# Disk IO operations (reads + writes)
rate(node_disk_reads_completed_total{instance=~".*truenas.*"}[5m])
rate(node_disk_writes_completed_total{instance=~".*truenas.*"}[5m])
```

## Troubleshooting

### High disk usage alert

1. Identify which file system: check the alert labels for `mountpoint` and `instance`
2. SSH to the host and run `df -h` to confirm
3. Find large files: `du -sh /* 2>/dev/null | sort -rh | head -20`
4. Common culprits: Docker images/volumes, log files, temporary files
5. Clean Docker: `docker system prune -a` (careful — removes unused images)
6. Check log rotation: `journalctl --disk-usage` and `journalctl --vacuum-size=500M`

### SMART warning on a drive

1. Run full SMART check: `smartctl -a /dev/sdX`
2. Look for reallocated sectors, pending sectors, or uncorrectable errors
3. If SMART pre-fail attributes are flagging, plan drive replacement
4. For ZFS: `zpool status` shows if pool is degraded

### Disk I/O bottleneck

1. Check I/O wait: `iostat -x 1 5`
2. Identify processes causing I/O: `iotop -o`
3. For NFS clients: I/O issues may be TrueNAS-side — check NFS server health
4. During quiet hours, HDDs should be spun down — I/O spikes wake them

## Related Services

- TrueNAS (ZFS storage pool management)
- Quiet hours (HDD spindown management)
- Share drive probe (NFS/SMB mount monitoring)
- Prometheus node_exporter (disk metrics)
