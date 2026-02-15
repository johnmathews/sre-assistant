# UPS Power Management

## Purpose

UPS (Uninterruptible Power Supply) protects the homelab from power outages. NUT (Network UPS Tools) monitors the UPS and triggers graceful shutdown of TrueNAS and Proxmox when battery runs low.

## Architecture

- UPS connected via USB to the Proxmox host
- NUT runs on Proxmox as the master/server
- TrueNAS configured as a NUT client (connects to Proxmox NUT server)
- On low battery: TrueNAS shuts down first, then Proxmox follows

## Key Commands

### Check UPS status

```sh
# On Proxmox host (ssh pve / root@192.168.2.214)
upsc ups@localhost

# Key values to look for:
# ups.status: OL (online/on mains) or OB (on battery)
# battery.charge: percentage
# battery.runtime: seconds remaining
```

### Check NUT service

```sh
systemctl status nut-server
systemctl status nut-monitor
```

### View UPS events

```sh
journalctl -u nut-monitor -n 50
```

## Configuration

NUT config files on Proxmox:

- `/etc/nut/ups.conf` — UPS device definition
- `/etc/nut/upsd.conf` — NUT server config (listen address)
- `/etc/nut/upsd.users` — NUT user authentication
- `/etc/nut/upsmon.conf` — Monitoring and shutdown config

TrueNAS NUT client config:
- Configured via TrueNAS web UI: Services > UPS
- Points to Proxmox NUT server IP

## Deploy via Ansible

```sh
make pve tags=ups
```

## Troubleshooting

### UPS shows "on battery" unexpectedly

1. Check physical power connection to UPS
2. Verify with `upsc ups@localhost` — look at `ups.status`
3. If `OB` (on battery): mains power is lost, monitor battery.charge
4. If battery.charge drops below threshold, automatic shutdown begins

### NUT client (TrueNAS) not connecting

1. Check NUT server is running on Proxmox: `systemctl status nut-server`
2. Verify NUT is listening: `ss -tlnp | grep 3493`
3. Check firewall allows port 3493 from TrueNAS IP
4. Test from TrueNAS: `upsc ups@<proxmox-ip>`
5. Check credentials match between upsd.users and TrueNAS config

### UPS not detected

1. Check USB connection: `lsusb` should show the UPS
2. Check NUT driver: `upsdrvctl start`
3. Review NUT logs: `journalctl -u nut-driver -n 50`

## Related Services

- Proxmox (host for NUT server)
- TrueNAS (NUT client, shuts down first)
- All VMs/LXCs (shut down by Proxmox during graceful shutdown)
