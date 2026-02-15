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
