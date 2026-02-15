# Tailscale VPN Remote Access

## Purpose

Tailscale provides zero-config mesh VPN for remote SSH/Ansible access to the homelab and encrypted DNS routing when
traveling. Built on WireGuard with automatic NAT traversal.

**Note:** Tailscale is mainly useful when the laptop is away from the home network, which is rare. The SRE assistant
itself will eventually run as a Docker service on Proxmox, always on the home network — so it will use local IPs
(192.168.2.x) rather than Tailscale IPs.

## Architecture

**At home:** Clients use local network (192.168.2.x) directly.

**When traveling:** Clients connect via Tailscale VPN tunnel. DNS queries route to home AdGuard for privacy and ad blocking.

```
Device (any WiFi) -> Tailscale tunnel -> AdGuard (100.108.0.112) -> Unbound -> Quad9
```

- Every VM/LXC has its own Tailscale instance
- AdGuard LXC has Tailscale for remote DNS routing
- Tailscale DNS settings point to AdGuard's Tailscale IP
- Remote Ansible uses `inventory-tailscale.ini` with Tailscale IPs (100.x.x.x)

## Key Commands

### Check status

```sh
tailscale status
tailscale ip -4
tailscale netcheck
```

### Restart Tailscale

```sh
sudo systemctl restart tailscaled
sudo tailscale up  # re-authenticate if needed
```

### Deploy to hosts

```sh
# All hosts
make tailscale

# Specific host
make tailscale LIMIT=adguard
```

### Run Ansible remotely

```sh
ansible-playbook -i inventory-tailscale.ini playbooks/<playbook>.yml --vault-password-file=.vault_pass.txt
```

### Access web services remotely

- AdGuard: http://100.108.0.112
- Prometheus: http://100.64.0.7:9090
- Grafana: http://100.64.0.8:3000
- Proxmox: https://100.64.0.1:8006

## DNS Verification

```sh
# Check DNS server in use
nslookup google.com
# Should show 100.100.100.100 (MagicDNS) or 100.108.0.112 (AdGuard)

# DNS leak test: visit https://www.dnsleaktest.com/
# Should show Quad9/PCH, NOT your local ISP or cafe network
```

## Troubleshooting

### DNS shows local network server instead of Tailscale

1. Ensure "Use Tailscale DNS settings" is enabled in Tailscale app
2. On Linux: `sudo tailscale up --accept-dns`
3. Check Tailscale admin DNS settings include AdGuard Tailscale IP
4. Restart Tailscale app

### Can't SSH to hosts via Tailscale

1. Verify Tailscale is connected: `tailscale status`
2. Check target host Tailscale is up: ping its Tailscale IP
3. Verify `inventory-tailscale.ini` has correct Tailscale IPs
4. Check firewall: Tailscale needs UDP 41641 and HTTPS (443)

### Slow DNS when traveling

1. Check if using DERP relay (slower): `tailscale status` — look for "relay" vs "direct"
2. Ping AdGuard: `ping 100.108.0.112`
3. Test DNS directly: `dig @100.108.0.112 google.com`
4. If persistent, temporarily disable Tailscale DNS

### LXC containers can't run Tailscale

The `proxmox_lxc_tun` role must be applied first (`make pve`) to add TUN device support to LXC configs.

## SSH Aliases (Tailscale)

When traveling, use the `t`-suffixed aliases to connect via Tailscale IPs:

- `ssh mediat` — media VM (100.88.114.14)
- `ssh infrat` — infra VM (100.64.76.3)
- `ssh cloudflaredt` — cloudflared LXC (100.117.104.102)
- `ssh prometheust` — prometheus LXC (100.108.161.119)
- `ssh nast` — TrueNAS (100.118.76.29)
- `ssh pvet` — Proxmox (100.99.115.121)

## Related Services

- AdGuard Home (DNS server, routes via Tailscale when remote)
- Proxmox (hosts LXC containers, subnet router candidate)
- All VMs/LXCs (each has Tailscale instance)
