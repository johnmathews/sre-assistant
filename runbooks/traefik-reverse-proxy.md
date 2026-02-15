# Traefik Reverse Proxy

## Purpose

Traefik handles ingress for public-facing services (Immich, Jellyfin) that are exposed through the Cloudflare Tunnel. It
provides rate limiting on authentication and API routes since these services bypass Cloudflare Zero Trust access
policies.

## Architecture

```
Cloudflare Tunnel -> cloudflared LXC -> Traefik -> Backend services
```

- Only port 443 is exposed to the public internet (via Cloudflare proxy)
- Traefik applies rate limiting to auth/API routes for Immich and Jellyfin
- Dashboard: https://traefik.itsa.pizza/dashboard/
- API overview: https://traefik.itsa.pizza/api/overview

## Key Commands

### Check Traefik status

```sh
ssh traefik  # root@192.168.2.108
docker ps | grep traefik
docker logs traefik --tail 50
```

### View dashboard

- https://traefik.itsa.pizza/dashboard/

## Troubleshooting

### Service unreachable through Cloudflare

1. Verify cloudflared tunnel is up (see cloudflared runbook)
2. Check Traefik container is running
3. Check Traefik logs for routing errors
4. Verify Traefik config has correct backend service addresses
5. Test backend service directly: `curl http://<backend-ip>:<port>`

### Rate limiting too aggressive

1. Check Traefik middleware configuration for rate limit settings
2. Review Traefik access logs for blocked requests
3. Adjust rate limit values in Traefik config

## Related Services

- Cloudflare Tunnel (upstream traffic source)
- Immich, Jellyfin (backend services)
- Cloudflare Zero Trust (access policies for other services)
