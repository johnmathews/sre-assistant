# MikroTik Router Monitoring

## Purpose

Monitors the MikroTik hAP ax3 router using MKTXP, a Prometheus exporter that pulls metrics via the RouterOS API. Provides
network throughput, WiFi client counts, DHCP lease tracking, and router system health.

## Architecture

```
MikroTik Router (192.168.2.1:8728) -> MKTXP Container (infra:49090) -> Prometheus
```

- MKTXP runs as a Docker container on the infra VM
- Connects to RouterOS API on port 8728
- Exposes metrics on port 49090 for Prometheus scraping
- All metrics prefixed with `mktxp_`

## Key Metrics

- `mktxp_system_uptime` — router uptime
- `mktxp_system_cpu_load` — CPU utilization
- `mktxp_system_free_memory`, `mktxp_system_total_memory` — memory usage
- `mktxp_interface_tx_byte`, `mktxp_interface_rx_byte` — network throughput per interface
- `mktxp_wireless_clients_count` — connected WiFi clients
- `mktxp_dhcp_lease_count` — active DHCP leases

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

## Related Services

- Prometheus (scrapes MKTXP metrics)
- Grafana (dashboards for network monitoring)
- AdGuard Home (DNS server, address provided via MikroTik DHCP)
