# Troubleshooting Runbook

## Health vs readiness

| Endpoint | Meaning | Touches DB |
| --- | --- | --- |
| `GET /health` | The application process is alive. | No |
| `GET /ready` | The application can use required dependencies (DB reachable). | Yes (`SELECT 1`) |

`/ready` returns 200 with a dependency list when healthy, 503 when not. The payload
never contains credentials, SQL, stack traces or connection strings — only
`status`, and per dependency `name`, `ok`, `latency_ms`, `source`, `error_type`.

## Reading a failing `/ready`

Example:
```json
{"status":"not_ready","dependencies":[{"name":"database","ok":false,"source":"hyperdrive","error_type":"OperationalError"}]}
```

| `source` | `error_type` | Likely cause | Action |
| --- | --- | --- | --- |
| `hyperdrive` | `OperationalError` / timeout | Neon unavailable, or Hyperdrive binding/connection misconfigured | Check Neon status; verify the binding id (`npx wrangler hyperdrive list`) matches `wrangler.jsonc` |
| `settings` | `ConfigError` | No `HYPERDRIVE` binding **and** no `DATABASE_URL` | Local: set `DATABASE_URL`; Worker: ensure the binding exists for the env |
| `settings` | `OperationalError` | `DATABASE_URL` wrong/unreachable (local dev) | Verify host/credentials; check `sslmode` |
| any | cold start | First request after idle is slower | Expected; retry |

## Common failures

**Worker deploy fails: "Script startup exceeded CPU time limit".** Global-scope work
exceeds 1 s. Keep import-time work minimal (the ASGI adapter drives lifespan per
request; do not put heavy initialization in lifespan). Check `wrangler deploy`
startup output.

**Worker bundle too large.** Guardrails (project): <45 MiB comfortable, 45–52 MiB
review, >52 MiB stop. Inspect `du -sh python_modules/*`.

**Local `pywrangler dev`: "Network connection lost" / "Error inside ProxyWorker".**
Miniflare's local dev proxy under heavy bursts; not a production behavior. Do not
add DDL per request (the harness table is created by `scripts/dev_setup.py`).

**Migration fails during apply.** Each revision runs in a transaction (Postgres
transactional DDL), so it rolls back and the DB stays at the previous revision. Run
`uv run alembic current` and `uv run alembic history`. Fix forward with a corrective
revision; do not hand-edit the schema or `alembic_version`.

**`alembic check` reports drift.** Autogenerate detects a difference between models
and DB — e.g., a table created outside migrations. The dev harness `infra_probe`
table is excluded; any other extra table is a real drift signal. Do not suppress it.

**Application errors but `/ready` is green.** Readiness only covers DB
connectivity; check Worker logs (`npx wrangler tail --env production`).

## Logs and diagnostics

- `npx wrangler tail --env <env>` — live Worker logs (Observability is enabled).
- `uv run alembic current` / `history` — schema state.
- `scripts/verify_deployment.sh <base-url>` — post-deploy smoke.

Never print secrets; readiness/log payloads are already redacted.
