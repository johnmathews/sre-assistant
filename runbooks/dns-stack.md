# DNS Stack: AdGuard Home + Unbound

## Purpose

Two-layer DNS resolution for the homelab. AdGuard Home handles DNS filtering (ad blocking, tracking protection) and
serves as the network's DNS server. Unbound sits behind AdGuard as a recursive resolver, forwarding queries to Quad9 over
DNS-over-TLS for privacy.

## Architecture

```
Clients (all VMs/LXCs via DHCP)
    -> AdGuard Home (192.168.2.111, LXC)
        -> Unbound (localhost:5335, same LXC)
            -> Quad9 (9.9.9.9, DNS-over-TLS)
```

- AdGuard Home listens on port 53 (DNS) and port 80 (web UI)
- Unbound listens on localhost:5335 as a recursive resolver
- MikroTik DHCP hands out 192.168.2.111 as the DNS server to all clients
- Tailscale DNS routes remote queries to AdGuard via Tailscale IP (100.108.0.112)

## Key Commands

### Check AdGuard Home status

```sh
ssh adguard  # root@192.168.2.111
systemctl status AdGuardHome
```

### Check Unbound status

```sh
ssh adguard  # root@192.168.2.111
systemctl status unbound
unbound-control status
```

### Test DNS resolution end-to-end

```sh
# From any client
nslookup google.com 192.168.2.111

# Test Unbound directly (from AdGuard LXC)
dig @127.0.0.1 -p 5335 google.com
```

### Restart services

```sh
systemctl restart AdGuardHome
systemctl restart unbound
```

### View AdGuard web UI

- Local: http://192.168.2.111
- Remote (Tailscale): http://100.108.0.112

### Deploy via Ansible

```sh
make adguard
```

## Prometheus Metrics

AdGuard Home exposes metrics on its API. Unbound stats are available via `unbound-control`.

```promql
# AdGuard LXC host health
up{instance=~".*111.*"}

# CPU/memory on the DNS LXC
rate(node_cpu_seconds_total{instance=~".*111.*", mode!="idle"}[5m])
node_memory_MemAvailable_bytes{instance=~".*111.*"}
```

### Agent strategy for DNS performance questions

DNS latency and query stats are not directly in Prometheus. Use this multi-step approach:

1. Check LXC is up: `up{instance=~".*111.*"}`
2. Check Loki for AdGuard/Unbound errors: `{hostname=~".*adguard.*"} |= "error"`
3. For cache hit ratio, the agent cannot query directly — advise the user to check:
   - AdGuard web UI (http://192.168.2.111) → Query Log and Statistics
   - `ssh adguard && unbound-control stats_noreset | grep cache`

## Troubleshooting

### DNS resolution fails for all clients

1. Check AdGuard is running: `systemctl status AdGuardHome`
2. Check Unbound is running: `systemctl status unbound`
3. Test Unbound directly: `dig @127.0.0.1 -p 5335 google.com`
4. If Unbound is down, AdGuard has no upstream — restart Unbound first
5. Check MikroTik DHCP is handing out 192.168.2.111 as DNS

### DNS works locally but not via Tailscale

1. Verify AdGuard is listening on all interfaces (not just 192.168.2.111)
2. Check Tailscale is running on AdGuard LXC: `tailscale status`
3. Verify Tailscale DNS config points to AdGuard's Tailscale IP

### High query latency

1. Check Unbound cache stats: `unbound-control stats_noreset | grep cache`
2. Check upstream Quad9 connectivity: `dig @9.9.9.9 google.com`
3. Review AdGuard query log for unusual patterns

## Related Services

- MikroTik router (DHCP server, hands out DNS address)
- Tailscale (remote DNS access)
- Prometheus node_exporter (monitors the LXC)
