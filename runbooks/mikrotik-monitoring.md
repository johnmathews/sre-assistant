# MikroTik Router Monitoring

## Purpose

Monitors the MikroTik hAP ax³ router using MKTXP, a Prometheus exporter that pulls metrics via the RouterOS API. Provides
network throughput, WiFi client counts, DHCP lease tracking, firewall counters, and router system health.

## Architecture

```
MikroTik Router (192.168.2.1:8728) -> MKTXP Container (infra:49090) -> Prometheus
```

- MKTXP runs as a Docker container on the infra VM
- Connects to RouterOS API on port 8728
- Exposes metrics on port 49090 for Prometheus scraping
- All metrics prefixed with `mktxp_`

## Interface Topology

The hAP ax³ has the following interfaces, identified by the `name` label on `mktxp_interface_*` metrics:

| `name` label      | Role           | Description                                                                  |
| ----------------- | -------------- | ---------------------------------------------------------------------------- |
| `youfone.nl`      | WAN (PPPoE)    | ISP tunnel — **best metric for internet traffic**                            |
| `ether1`          | WAN (physical) | Physical port carrying PPPoE; nearly identical to `youfone.nl` plus overhead |
| `ether2`–`ether5` | LAN (physical) | LAN ports; some may be unused (zero traffic)                                 |
| `defconf`         | LAN (bridge)   | Default bridge aggregating all LAN ports + WiFi — **total LAN traffic**      |
| `2GHz`            | WiFi           | 2.4 GHz radio                                                                |
| `5GHz`            | WiFi           | 5 GHz radio                                                                  |
| `lo`              | Loopback       | Always zero                                                                  |

**Important:** interface metrics use the `name` label (NOT `interface`). For example:
`mktxp_interface_download_bytes_per_second{name="youfone.nl"}`.

## Key Metrics

### Router System Health

- `mktxp_system_cpu_load` — CPU utilization (%)
- `mktxp_system_cpu_temperature` — CPU temperature
- `mktxp_system_cpu_frequency` — current CPU frequency
- `mktxp_system_free_memory`, `mktxp_system_total_memory` — memory usage
- `mktxp_system_uptime` — router uptime (seconds)
- `mktxp_system_identity_info` — router identity (info metric, name in labels)
- `mktxp_public_ip_address_info` — public IP (info metric, IP in labels)

### Network Throughput

- `mktxp_interface_download_bytes_per_second` — current download rate per interface
- `mktxp_interface_upload_bytes_per_second` — current upload rate per interface
- `mktxp_interface_rx_byte_total` — cumulative received bytes (counter)
- `mktxp_interface_tx_byte_total` — cumulative transmitted bytes (counter)
- `mktxp_interface_rx_packet_total`, `mktxp_interface_tx_packet_total` — packet counters
- `mktxp_interface_rate` — physical link speed (bits/sec, e.g. 1000000000 = 1 Gbps)

**CRITICAL: download bytes/sec values are negative.** This is an MKTXP exporter convention:

- `mktxp_interface_download_bytes_per_second` → negative values
- `mktxp_interface_upload_bytes_per_second` → positive values
- Use `abs()` in PromQL to convert, e.g. `abs(mktxp_interface_download_bytes_per_second{name="youfone.nl"})`
- `max_over_time` on negative values returns the value closest to zero (lowest rate) — use `min_over_time` to get the
  most negative value (highest download rate)

### Network Health

- `mktxp_interface_rx_error_total`, `mktxp_interface_tx_error_total` — error counters (should be near 0)
- `mktxp_interface_rx_drop_total`, `mktxp_interface_tx_drop_total` — drop counters
- `mktxp_link_downs_total` — link flap counter (should be 0 for stable interfaces)
- `mktxp_interface_running` — interface running status (1=up, 0=down)
- `mktxp_interface_status` — link status
- `mktxp_interface_full_duplex` — duplex mode

### WiFi

- `mktxp_wlan_registered_clients` — connected WiFi client count
- `mktxp_wlan_clients_signal_strength` — client signal (dBm; -30=excellent, -67=good, -80=poor)
- `mktxp_wlan_clients_signal_to_noise` — signal-to-noise ratio
- `mktxp_wlan_clients_tx_ccq` — connection quality (%)
- `mktxp_wlan_clients_rx_bytes_total`, `mktxp_wlan_clients_tx_bytes_total` — per-client bytes

### DHCP and IP

- `mktxp_dhcp_lease_active_count` — active leases per DHCP server
- `mktxp_dhcp_lease_info` — individual lease details (info metric)
- `mktxp_ip_connections_total` — active connection tracking entries
- `mktxp_ip_pool_used` — used addresses per IP pool

### Firewall

- `mktxp_firewall_filter_total` — filter rule hit counters
- `mktxp_firewall_nat_total` — NAT rule hit counters
- `mktxp_firewall_mangle_total` — mangle rule hit counters
- `mktxp_firewall_raw_total` — raw rule hit counters

### Routing

- `mktxp_routes_total_routes` — total routes in routing table
- `mktxp_routes_protocol_count` — routes per protocol

## Common PromQL Queries

### Bandwidth

```promql
# Current internet download rate (MB/s)
abs(mktxp_interface_download_bytes_per_second{name="youfone.nl"}) / 1000000

# Peak download rate in the last 7 days (instant query)
abs(min_over_time(mktxp_interface_download_bytes_per_second{name="youfone.nl"}[7d]))

# Total data downloaded in the last 24h
increase(mktxp_interface_rx_byte_total{name="youfone.nl"}[24h])

# Download rate over time (for range queries, use with abs())
abs(mktxp_interface_download_bytes_per_second{name="youfone.nl"})
```

### Bandwidth Conversion Reference

- Bytes/sec ÷ 1,000,000 = MB/s (megabytes per second)
- Bytes/sec × 8 ÷ 1,000,000 = Mbps (megabits per second, ISP unit)
- Example: 12,500,000 B/s = 12.5 MB/s = 100 Mbps

### Network Health

```promql
# Link flaps in the past 7 days (should be 0)
increase(mktxp_link_downs_total{name="ether1"}[7d])

# Interface error rate
rate(mktxp_interface_rx_error_total{name="ether1"}[5m])

# Physical link speed
mktxp_interface_rate{name="ether1"}
```

### WiFi

```promql
# Current WiFi client count
mktxp_wlan_registered_clients

# Worst WiFi signal (most negative dBm = weakest)
bottomk(5, mktxp_wlan_clients_signal_strength)
```

## Key Commands

### Verify metrics endpoint

```sh
curl -s http://192.168.2.106:49090/metrics | grep mktxp | head -10
```

### Test API connectivity from infra VM

```sh
ssh infra  # john@192.168.2.106
nc -zv 192.168.2.1 8728
```

### Check container logs

```sh
ssh infra  # john@192.168.2.106
docker logs mikrotik_exporter
```

### Deploy via Ansible

```sh
make infra t=mikrotik_exporter
make prometheus
```

## Router Configuration

API access enabled on the router with a read-only user:

```routeros
/ip service enable api
/user add name=mktxp_exporter group=read password=<from vault>
/user set mktxp_exporter address=192.168.2.106/32
```

Router IP configured in `group_vars/all/main.yml`:

```yaml
mikrotik_router_ip: 192.168.2.1
```

MKTXP config templates: `roles/infra_vm/templates/mikrotik_exporter/`

## Troubleshooting

### No metrics in Prometheus

1. Check MKTXP container: `docker logs mikrotik_exporter`
2. Test API connectivity: `nc -zv 192.168.2.1 8728`
3. Verify metrics endpoint: `curl -s http://192.168.2.106:49090/metrics | head`
4. Check Prometheus targets: verify the scrape config includes the exporter

### API authentication failure

1. Verify credentials in Ansible vault match router user
2. Check API restriction: user should be allowed from 192.168.2.106/32
3. RouterOS API uses username/password only (no API tokens supported)

### Download rate looks wrong

1. Remember: `mktxp_interface_download_bytes_per_second` values are **negative** by convention
2. Use `abs()` in PromQL to get positive values
3. Don't use `max_over_time` for peak download — use `min_over_time` (most negative = highest rate)
4. Verify the right interface: `youfone.nl` for internet traffic, not `ether1` or `defconf`

## Related Services

- Prometheus (scrapes MKTXP metrics)
- Grafana (dashboards for network monitoring)
- AdGuard Home (DNS server, address provided via MikroTik DHCP)
