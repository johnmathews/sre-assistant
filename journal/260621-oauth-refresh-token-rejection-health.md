# Surface dead OAuth refresh tokens in health (260621)

## Incident

The deployed agent stopped answering. Logs showed, on every `/ask/stream`:

```
WARNING:src.agent.oauth_refresh:OAuth refresh returned HTTP 400: {"error": "invalid_grant", "error_description": "Refresh token not found or invalid"}
ERROR:src.agent.sdk_agent:SDK streaming failed
Exception: Command failed with exit code 1
```

**Root cause:** the OAuth *refresh* token in the mounted `~/.claude/.credentials.json` had been revoked
(`400 invalid_grant`). The access token self-heals on every call, but a dead refresh token does not — a human must
re-authenticate. `_do_refresh` logged a warning and returned silently (by design), so the SDK proceeded with the
expired access token and the `claude` CLI subprocess exited 1. The SDK traceback was just the downstream symptom.

**Recovery (done by operator):** `claude login` on the host (rewrites `claudeAiOauth` in `.credentials.json`), then
`docker compose restart sre-agent`. Rotation was not at fault — a plain re-login fixed it.

## Fix — make the failure visible (follow-up 1)

This violated the project's "never silently fail" principle: `get_token_health()` assumed *any* present refresh token
was self-healing (`oauth_refresh.py:202-205`), so `/health` stayed `healthy` while the agent was hard-down.

Changes in `src/agent/oauth_refresh.py`:

- Track a process-global `_rejected_refresh_token_hash` — set when a refresh returns `400 invalid_grant`, cleared on a
  successful refresh. Stored as a SHA-256 hash (never hold/log the raw secret). Process-global is safe given the
  single-worker Dockerfile.
- `get_token_health()` now returns `unhealthy` when the on-disk refresh token's hash matches the rejected one, with
  detail telling the operator to run `claude login`. Keying on token *identity* means re-authentication self-clears the
  flag immediately — no stale "unhealthy" until the next refresh ~8h later.
- Transient errors (5xx, network) are deliberately **not** flagged — they recover on the next call.

Docs: added "When the refresh token itself dies" to `docs/dependencies.md` with the recovery runbook and a
ready-to-provision Prometheus alert on `sre_assistant_oauth_refresh_total{status="error"}` (alert rules live in the
homelab Prometheus/Grafana, not this repo).

Tests: `tests/test_oauth_refresh.py` — invalid_grant → unhealthy; re-login self-clears; transient error stays healthy;
successful refresh clears a prior rejection. Added an autouse fixture to reset the global between tests. Full suite: 978
passing, lint + mypy clean.

## Not done (deferred)

Follow-up 2 (dedicated container-only `CLAUDE_CONFIG_DIR` to avoid host/container refresh-token rotation races) — not
needed here; rotation wasn't the cause. Left for later if cross-process races ever appear.
