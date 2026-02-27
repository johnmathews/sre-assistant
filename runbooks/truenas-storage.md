# TrueNAS Storage Management

## Purpose

TrueNAS SCALE is the central NAS providing ZFS-backed storage pools and NFS/SMB shares for the homelab. All persistent
data (media, documents, photos, backups) lives on TrueNAS.

## Architecture

- TrueNAS SCALE running on dedicated hardware
- IP: 192.168.2.104
- ZFS pools:
  - `tank` — main storage pool with mirrored 16TB HDDs and mirrored special VDEVs (SSDs for metadata)
  - `swift` — SSD pool
  - `backup` — used only for TrueNAS snapshots and backups
- Proxmox Backup Server (PBS) backs up to a separate disk that TrueNAS cannot access
- Shares served primarily via NFS (all Linux VMs/LXCs). SMB only for Time Machine backups and macOS file browsing.
- UPS-protected with automatic shutdown on low battery

## Key Commands

### Access TrueNAS

```sh
# Web UI
open https://192.168.2.104

# SSH
ssh nas  # truenas_admin@192.168.2.104
```

### Check pool health

```sh
sudo zpool status tank
sudo zpool list
```

### Check dataset usage

```sh
sudo zfs list -o name,used,avail,refer,mountpoint
```

**Note:** On TrueNAS, `zfs` and `zpool` commands require `sudo`.

### Manage NFS shares

```sh
# List NFS shares via API
curl -s -k https://192.168.2.104/api/v2.0/sharing/nfs \
  -H "Authorization: Bearer $TRUENAS_API_KEY" | jq '.[] | {id, path, enabled}'
```

## Prometheus Metrics

```promql
# TrueNAS host health
up{instance=~".*truenas.*"}

# Pool capacity — ZFS datasets are exported as filesystem metrics by node_exporter
node_filesystem_size_bytes{instance=~".*truenas.*", mountpoint=~"/mnt/tank.*|/mnt/swift.*"}
node_filesystem_avail_bytes{instance=~".*truenas.*", mountpoint=~"/mnt/tank.*|/mnt/swift.*"}

# Pool usage percentage
100 - (node_filesystem_avail_bytes{instance=~".*truenas.*", mountpoint=~"/mnt/tank.*"} /
       node_filesystem_size_bytes{instance=~".*truenas.*", mountpoint=~"/mnt/tank.*"} * 100)

# Disk IO — see disk-management runbook for spinup/spindown detection
rate(node_disk_io_time_seconds_total{instance=~".*truenas.*"}[5m])

# Memory (ZFS ARC uses most of it — high memory usage is normal)
node_memory_MemTotal_bytes{instance=~".*truenas.*"}
node_memory_MemAvailable_bytes{instance=~".*truenas.*"}
```

### ZFS capacity guidance

- ZFS performance degrades significantly above **80% capacity** — plan expansion before then
- `tank` pool: mirrored 16TB HDDs = ~14TB usable (after mirror + ZFS overhead)
- `swift` pool: SSD — capacity depends on SSD size
- Use `truenas_dataset_usage` tool for per-dataset breakdown

## Common Operations

### Toggle an NFS share (enable/disable)

This is the most common fix for NFS issues. TrueNAS uses PUT (not PATCH) for share updates. The `/id/` prefix is required
in the path.

```sh
# Disable NFS share (replace 5 with actual share ID)
curl -X PUT "https://192.168.2.104/api/v2.0/sharing/nfs/id/5" \
  -H "Authorization: Bearer $TRUENAS_API_KEY" \
  -H "Content-Type: application/json" \
  -k -d '{"enabled": false}'

# Enable NFS share
curl -X PUT "https://192.168.2.104/api/v2.0/sharing/nfs/id/5" \
  -H "Authorization: Bearer $TRUENAS_API_KEY" \
  -H "Content-Type: application/json" \
  -k -d '{"enabled": true}'
```

### Check API connectivity

```sh
curl -s -k "https://192.168.2.104/api/v2.0/system/info" \
  -H "Authorization: Bearer $TRUENAS_API_KEY" | jq -r '.version'
```

## Troubleshooting

### NFS mount stale or disconnected on client

Most NFS issues can be fixed by toggling the NFS share via the TrueNAS API, then (if necessary) restarting the Docker
service that bindmounts the NFS share into the container.

1. Toggle the NFS share off then on via API (see "Toggle an NFS share" above)
2. On the client, check mount status: `mount | grep nfs`, `findmnt -t nfs4`
3. If mount is stale, force unmount: `sudo umount -l /mnt/nfs/<dataset>`
4. Remount: `sudo mount -t nfs 192.168.2.104:/mnt/tank/<dataset> /mnt/nfs/<dataset>`
5. If a Docker service depends on this mount, restart it: `cd /srv/apps && docker compose restart <service>`
6. Check TrueNAS is reachable: `ping 192.168.2.104`

### SMB share not accessible (rare — macOS only)

SMB is only used for Time Machine backups and browsing files from macOS. For all Linux services, use NFS.

1. Toggle the share off and on in TrueNAS UI to restart the service
2. Test manually: `smbclient -L //192.168.2.104 -U <username>`

### ZFS pool degraded

1. Check pool status: `sudo zpool status tank`
2. Identify failed/faulted drives
3. See disks runbook for disk replacement procedures

## Related Services

- NFS/SMB shares runbook (client-side mount management and monitoring)
- Quiet hours system (currently disabled — was used to toggle shares for HDD spindown)
- Share drive probe (monitors NFS/SMB mount health via Prometheus metrics)
- UPS (triggers TrueNAS shutdown on low battery)
- Proxmox Backup Server (backs up to separate disk, independent of TrueNAS)
