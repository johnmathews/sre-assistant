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

TrueNAS HDDs spin down when idle and spin up on access. To determine which disk spun up most
recently, use Prometheus disk IO metrics from node_exporter on the TrueNAS host.

### Which HDD had activity most recently?

Use `node_disk_io_time_seconds_total` — this counter only increases when a disk is actively
doing IO. A disk that was spun down will show zero increase; a disk that just spun up will
show a non-zero increase over a short window.

```promql
# IO time increase per disk in the last 1 hour (non-zero = disk was active)
increase(node_disk_io_time_seconds_total{instance=~".*truenas.*"}[1h])

# Narrow to just HDDs by filtering to known HDD devices (sd[c-h] typically)
# Cross-reference device names with truenas_list_disks output
increase(node_disk_io_time_seconds_total{instance=~".*truenas.*", device=~"sd[c-h]"}[1h])

# Check shorter windows to find the MOST recent spinup
# Try 5m, 15m, 30m, 1h — the shortest window with non-zero results
# identifies the most recently active disk
increase(node_disk_io_time_seconds_total{instance=~".*truenas.*", device=~"sd[c-h]"}[5m])
```

### Recommended agent strategy

When asked "which HDD spun up recently" or similar:

1. Call `truenas_list_disks` to get the disk inventory (device names, models, sizes, serial numbers)
2. Use `prometheus_query` with increasingly short time windows (1h → 30m → 15m → 5m) on
   `increase(node_disk_io_time_seconds_total{instance=~".*truenas.*"}[<window>])` to find
   which disk(s) had IO most recently
3. Match the `device` label from Prometheus to the device name from TrueNAS to report the
   disk model, size, and serial number
4. Filter out SSDs (sdb, sdd, sdg — or check the `type` field from `truenas_list_disks`) since
   SSDs don't spin up/down

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
