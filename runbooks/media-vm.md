# Media VM

## Purpose

Hosts media services (qBittorrent, Jellyfin, etc.) with Mullvad VPN for privacy. Media files are stored on TrueNAS and
mounted via SMB shares.

## Architecture

- Media files live on TrueNAS NFS/SMB shares, not on the VM itself
- SMB credentials stored at `/etc/smb-media-credentials`
- PUID/GUID must match TrueNAS dataset permissions (configured in `roles/media_vm/defaults/main.yml`)
- Mullvad VPN protects outbound traffic (account number in 1Password)

## Key Commands

### Check running containers

```sh
ssh media  # john@192.168.2.105
docker ps
```

### qBittorrent

- Default user: `admin`
- If auth breaks, check logs for temporary password: `docker compose logs qbittorrent`
- Template config file contains the correct password hash

### Check SMB mount health

```sh
mount | grep cifs
ls /mnt/media/
```

## Troubleshooting

### qBittorrent can't access media files

1. Check SMB mount: `mount | grep cifs`
2. Verify PUID/GUID in docker compose matches TrueNAS user
3. Check TrueNAS SMB share is enabled
4. During quiet hours, shares may be disabled — check quiet hours status

### VPN not connected

1. Check Mullvad container logs
2. Verify account is active (check 1Password for account number)

## Related Services

- TrueNAS (SMB shares for media storage)
- Quiet hours (pauses/stops media containers at night)
- Cloudflare Tunnel + Traefik (Jellyfin public access)
