# UPS Power Management

## Purpose

UPS (Uninterruptible Power Supply) protects the homelab from power outages. NUT (Network UPS Tools) monitors the UPS and
triggers graceful shutdown of TrueNAS and Proxmox when battery runs low.

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

## Prometheus Metrics

NUT metrics are available if the `nut_exporter` or Proxmox node_exporter NUT integration is configured.

```promql
# UPS status (1 = online/OL, 0 = on battery/OB)
# Metric name depends on exporter — common patterns:
nut_ups_status{ups="ups"}
network_ups_tools_ups_status{flag="OL"}

# Battery charge percentage
nut_battery_charge{ups="ups"}
network_ups_tools_battery_charge

# Battery runtime remaining (seconds)
nut_battery_runtime_seconds{ups="ups"}
network_ups_tools_battery_runtime_seconds

# Input voltage
nut_input_voltage{ups="ups"}
```

## Power Monitoring (Home Assistant)

The entire server rack (Proxmox host + UPS + MikroTik router) is plugged into a smart plug that reports real-time power
consumption to Home Assistant, which is scraped by Prometheus.

### Key metric

```promql
# Real-time rack power draw (watts)
homeassistant_sensor_power_w{entity="sensor.tech_shelf_power"}

# Average power over 3 days
avg_over_time(homeassistant_sensor_power_w{entity="sensor.tech_shelf_power"}[3d])

# Peak power in the last 7 days
max_over_time(homeassistant_sensor_power_w{entity="sensor.tech_shelf_power"}[7d])

# Power trend over the last 24 hours (range query with 15m step)
homeassistant_sensor_power_w{entity="sensor.tech_shelf_power"}
```

### Why NOT node_hwmon_power_watt

In a Proxmox virtualised environment, `node_hwmon_power_watt` reports PCIe device power readings from virtualised
`/sys/class/hwmon/`. These are **not** real electricity consumption — they reflect what the hypervisor exposes to each
VM's virtual hardware monitor. Multiple VMs will show suspiciously similar ~9-10W values regardless of actual workload.
This metric is meaningless for "how much electricity does the homelab use?"

### Agent strategy for power consumption questions

Use `prometheus_instant_query` or `prometheus_range_query` with the Home Assistant smart plug metric
(`homeassistant_sensor_power_w{entity="sensor.tech_shelf_power"}`). This measures the entire rack at the wall outlet —
the only accurate source for electricity consumption.

### Agent strategy for UPS questions

If NUT metrics are not in Prometheus, the agent cannot answer UPS questions directly. In that case, advise the user to
check manually:

```sh
ssh pve && upsc ups@localhost
```

Key values: `ups.status` (OL=online, OB=on battery), `battery.charge` (%), `battery.runtime` (seconds).

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
