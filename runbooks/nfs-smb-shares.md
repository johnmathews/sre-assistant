# NFS and SMB Share Management

## Purpose

TrueNAS serves NFS shares to all Linux VMs and LXCs across the homelab. NFS is the primary share protocol — it is more
reliable than SMB for Linux clients. SMB is only used for Time Machine backups from macOS and browsing files on the
server from a MacBook. Share health is monitored via the share drive probe.

## Architecture

- TrueNAS (192.168.2.104) serves shares from ZFS datasets under `/mnt/tank/`
- NFS shares mounted on clients at `/mnt/nfs/<dataset>`
- systemd automount handles NFS mounts (via fstab with `x-systemd.automount`)
- Docker services access NFS data via bindmounts from the host's `/mnt/nfs/` into containers
- Share drive probe writes Prometheus metrics to node_exporter textfile collector

## NFS Share Setup (Manual)

```sh
# 1. Create mount directory
mkdir -p /mnt/nfs/<dataset>

# 2. Add to /etc/fstab
192.168.2.104:/mnt/tank/<dataset>  /mnt/nfs/<dataset>  nfs  nofail,_netdev,x-systemd.automount,retrans=2,timeo=5  0  0

# 3. Reload systemd and mount
systemctl daemon-reload
systemctl restart remote-fs.target

# 4. Verify
systemctl status mnt-nfs-<dataset>.automount
ls /mnt/nfs/<dataset>
```

### Deploy via Ansible

```sh
make site tags=shares
make <host> tags=shares
```

## Key Commands

### Check NFS mounts

```sh
mount | grep nfs
findmnt -t nfs4
```

### Manual NFS mount

```sh
sudo mount -t nfs 192.168.2.104:/mnt/tank/<dataset> /mnt/nfs/<dataset>
```

### Unmount

```sh
sudo umount /mnt/nfs/<dataset>
# If busy: sudo umount -l /mnt/nfs/<dataset>
# Force unmount (last resort): sudo umount -f /mnt/nfs/<dataset>
```

### Check automount unit status

```sh
systemctl status mnt-nfs-<dataset>.automount
systemctl list-units 'mnt-nfs-*.mount' 'mnt-nfs-*.automount'
```

## NFS Troubleshooting

### NFS mount shows "Transport endpoint is not connected"

This is the most common NFS issue. Fix by toggling the share on TrueNAS, then restarting affected Docker services.

1. Toggle the NFS share via TrueNAS API (disable then enable):

   ```sh
   # Find share ID
   curl -s -k https://192.168.2.104/api/v2.0/sharing/nfs \
     -H "Authorization: Bearer $TRUENAS_API_KEY" | jq '.[] | {id, path, enabled}'

   # Disable then enable (replace 5 with actual ID)
   curl -X PUT "https://192.168.2.104/api/v2.0/sharing/nfs/id/5" \
     -H "Authorization: Bearer $TRUENAS_API_KEY" -H "Content-Type: application/json" \
     -k -d '{"enabled": false}'
   curl -X PUT "https://192.168.2.104/api/v2.0/sharing/nfs/id/5" \
     -H "Authorization: Bearer $TRUENAS_API_KEY" -H "Content-Type: application/json" \
     -k -d '{"enabled": true}'
   ```

2. On the client, force unmount: `sudo umount -l /mnt/nfs/<dataset>`
3. Remount: `sudo mount -t nfs 192.168.2.104:/mnt/tank/<dataset> /mnt/nfs/<dataset>`
4. Restart Docker services that bindmount this NFS share: `cd /srv/apps && docker compose restart <service>`

### NFS mount hangs or times out

1. Check TrueNAS is reachable: `ping 192.168.2.104`
2. Check NFS service on TrueNAS is running (toggle in TrueNAS UI: Shares > NFS)
3. Check network: `showmount -e 192.168.2.104` (lists exported shares)
4. Check automount unit: `systemctl status mnt-nfs-<dataset>.automount`

### Docker service can't access NFS-backed data

Docker services access NFS data via host bindmounts. If the NFS mount is broken, the container sees an empty or errored
mountpoint.

1. Check the host NFS mount first: `ls -la /mnt/nfs/<dataset>/`
2. If mount is stale, fix it (see above), then restart the Docker service
3. Check docker compose volume mapping points to the correct `/mnt/nfs/` path

### NFS mount not remounting after reboot

1. Check fstab entry has correct options: `nofail,_netdev,x-systemd.automount`
2. `_netdev` ensures mount waits for network; `nofail` prevents boot failure
3. Run `systemctl daemon-reload && systemctl restart remote-fs.target`

## Share Drive Probe (Monitoring)

A systemd timer runs `share_drive_probe.sh` on each client that has NFS mounts. It writes Prometheus metrics to
`/var/lib/node_exporter/textfile_collector/share_drive_probe.prom`.

### Check probe status

```sh
systemctl status share-drive-probe.service
systemctl list-timers share-drive-probe.timer
```

### View probe metrics

```sh
cat /var/lib/node_exporter/textfile_collector/share_drive_probe.prom
```

### View probe logs

```sh
journalctl -u share-drive-probe.service -n 50
```

### Probe metric names

- `share_drive_probe_success` — 1 if probe succeeded, 0 if failed
- `share_drive_probe_state` — -1 error, 0 fail, 1 success
- `share_drive_probe_duration_seconds` — time to complete touch+rm test
- `share_drive_probe_last_run_timestamp_seconds` — UNIX time of last run

### Probe configuration files

- Targets: `/etc/share_drive_probe/targets.list`
- Script: `/usr/local/bin/share_drive_probe.sh`
- Metrics output: `/var/lib/node_exporter/textfile_collector/share_drive_probe.prom`

### Probe failing

1. Check probe service: `systemctl status share-drive-probe.service`
2. Check target list: `cat /etc/share_drive_probe/targets.list`
3. Verify node_exporter is collecting the textfile: check docker compose for textfile_collector volume mount
4. Manually trigger: `systemctl start share-drive-probe.service`

## SMB Shares (macOS only)

SMB is only used for Time Machine backups and browsing files from a MacBook. Not used by any Linux service.

```sh
# Manual SMB mount
sudo mount -t cifs //192.168.2.104/<share> /mnt/<target> \
  -o credentials=/etc/smb-credentials,uid=1001,gid=1001,vers=3.1.1

# List available shares
smbclient -L //192.168.2.104 -U <username>
```

## Related Services

- TrueNAS (NFS/SMB server — see truenas-storage runbook)
- node_exporter (collects share probe metrics)
- Prometheus (scrapes share probe metrics)
