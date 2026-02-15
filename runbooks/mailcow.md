# Mailcow Mail Server (Inactive)

## Status: STOPPED

Mailcow is currently stopped and not in use. Gmail is used instead for notifications and email alerts
(itsa.big.pizza@gmail.com) — e.g., Paperless email monitoring, Immich share notifications.

The VM may be repurposed or removed in the future.

## Architecture (for reference)

- VM ID: 103, IP: 192.168.2.103, hostname: mailcow
- Domain: itsa.pizza, mail FQDN: mail.itsa.pizza
- Docker-based mail server suite
- Install path: `/opt/mailcow-dockerized`
- DNS via Cloudflare (direct DNS records, not tunneled)

## If Restarting Mailcow

```sh
ssh mail  # john@192.168.2.103
cd /opt/mailcow-dockerized
docker compose up -d
```

Web UI: https://mail.itsa.pizza

## Related Services

- Cloudflare (DNS records for itsa.pizza)
