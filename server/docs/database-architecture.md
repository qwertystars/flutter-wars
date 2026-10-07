# Database Connection Architecture (Module M)

Scope: the database infrastructure boundary owned by Module M. No business tables.
The shared migration workflow lives in `migrations/` (see `docs/migrations.md`);
CI/CD lives in `.github/workflows/`.

## 1. Components

| File | Responsibility | Owner |
| --- | --- | --- |
| `src/settings.py` | Environment separation + DB configuration record and validation | Module M (Foundation consumes it) |
| `src/db.py` | Target resolution, engine/session factory, connectivity check | Module M |
| `src/health.py` | Readiness probe hook + dependency status model | Module M (Foundation owns the endpoint) |
| `src/app.py` | Minimal FastAPI seam app for verification | Foundation will replace/own |
| `src/worker.py` | Cloudflare ASGI entrypoint (`workers.asgi.entrypoint`) | Module M (deployment config) |
| `src/infra_probe.py` | Infra-only probe table for lifecycle tests — **not** a business entity | Module M (temporary) |

## 2. Connection paths

```
LOCAL DEV (uvicorn)                          PRODUCTION (Cloudflare Worker)
  FastAPI                                      FastAPI (Pyodide, sync)
    -> resolve_target()                          -> resolve_target()
    -> DATABASE_URL (settings source)            -> env.HYPERDRIVE binding
    -> psycopg -> Neon (direct, TLS)             -> psycopg -> Hyperdrive -> Neon
```

- Local and production use the **same sync SQLModel/SQLAlchemy + psycopg code path**.
- The only differences are the target source and `sslmode` (Hyperdrive hop is plain,
  direct Neon requires TLS).

## 3. Module A / Module M seam

Module M provides, and Foundation consumes:

- `db.resolve_target(scope_env, settings) -> DatabaseTarget`
- `db.engine_for(target, settings)` and `db.create_db_engine(target, settings)`
- `db.session_scope(engine)` — commit / rollback / close transaction boundary
- `db.check_connectivity(engine) -> (ok, latency_ms, error_type)`
- `health.check_database(scope_env, settings) -> DependencyStatus`
- `health.readiness(scope_env, settings) -> (is_ready, payload)`

Module M **does not** define `get_db()`, the FastAPI bootstrap, or the `/health`
and `/ready` routers. Foundation composes these hooks into `get_db()` and its
readiness endpoint.

## 4. Connection lifecycle

- One `Engine` per request, with `NullPool` — Hyperdrive owns pooling, and the
  Worker must not hold TCP sockets between requests.
- One `Session` per unit of work via `session_scope`: commit on success, rollback
  on error, always close.
- No shared mutable session/connection state across requests.

## 5. Driver decision

- Driver: **psycopg (sync)** — present in Pyodide and verified with Hyperdrive.
- ORM: **synchronous** SQLModel/SQLAlchemy. Async SQLAlchemy is unsupported in
  Python Workers (no greenlet), so async SQLModel/asyncpg are not used for the ORM.

## 6. Hyperdrive integration

- Binding name: `HYPERDRIVE` (`settings.HYPERDRIVE_BINDING`).
- The binding exposes `host/port/user/password/database` (no connection string).
- Worker -> Hyperdrive hop is not TLS: the generated URL uses `sslmode=disable`.
- Hyperdrive pools in **transaction mode**: keep transactions short; do not wrap
  long multi-statement units of work in a single transaction.
- Hyperdrive caches read queries (`max_age`, default 60s) and does **not**
  invalidate on writes. **Decision (final, not TBD): the primary `HYPERDRIVE`
  binding uses a cache-disabled configuration** because this platform has
  correctness-critical reads (credits, inventory, market state, post-commit reads,
  IDE sync, permission/auth reads). A future optimization MAY add
  `HYPERDRIVE_CACHED` / `HYPERDRIVE_FRESH`, with correctness-sensitive reads on the
  fresh path. See `docs/deployment.md`.

## 7. Neon configuration boundary

- Local/dev/test connect directly to Neon with TLS (`sslmode=require` in the URL).
- Use Neon **branches** for dev/test/staging isolation; production is a separate
  branch/project. The staging Worker uses a separate Hyperdrive id + Neon branch
  (ids are placeholders until account access is available).
- Neon's pooled endpoint (`...-pooler...`) is optional for local tooling; the
  Worker path goes through Hyperdrive instead.
- No Neon-specific code in the app — only the connection target differs.

## 8. Environment separation

| Env | Target source | `DATABASE_URL` | Secrets location |
| --- | --- | --- | --- |
| development | settings | required (dev Neon branch) | local `.env` (gitignored) |
| test | settings | required (test DB/branch) | CI secret / `.env` |
| staging | Hyperdrive binding | not used in the Worker | CI secret for migrations |
| production | Hyperdrive binding | not used in the Worker | `wrangler secret put` / CI secret |

Env vars: `APP_ENV`, `DATABASE_URL`, `DB_CONNECT_TIMEOUT_SECONDS`,
`DB_APPLICATION_NAME`. See `.env.example` / `.dev.vars.example`.

## 9. Concurrency decision (measured, project-specific)

Cloudflare's Hyperdrive documentation recommends serializing synchronous DB
operations with an `asyncio.Lock`. **This project does not use that lock.**

Measured in the real Pyodide Worker runtime (`GET /ready`, real DB ping):

| Concurrency | With global `asyncio.Lock` | Without lock |
| --- | --- | --- |
| 1 | 30/30 | ok |
| 3 | 1/30 | ok |
| 10 | 1/50, then 0/30 | 50/50 |
| 25 | — | 200/200 |
| 50 | — | 300/300 |
| 100 | — | 400/400 |

With the lock, waiters starve while the holder runs a synchronous DB call and the
runtime cancels them ("your Worker's code had hung"). This is a **project-specific
measured decision**, not a claim that Cloudflare's guidance is universally wrong;
it may not represent other workloads or future runtime versions.

## 10. Failure behavior

- `check_connectivity` / `check_database` never return exception messages, SQL,
  connection strings, or credentials — only `error_type` names.
- Configuration failures surface as `ConfigError` (readiness fails closed).
- Connect timeouts are bounded by `DB_CONNECT_TIMEOUT_SECONDS`.
- The `/ready` payload contains only: status, dependency name, ok, latency_ms,
  source, error_type.

## 11. Worker limits (recorded baseline)

- Bundle (dry-run): **34244 KiB uncompressed / 7661 KiB gzip** (Phase 1: 34243 KiB).
  Guardrails (project): <45 MiB comfortable, 45–52 MiB review, >52 MiB stop.
- Largest dependencies: `psycopg_binary` 16 MiB, `sqlalchemy` 8.7 MiB,
  `pydantic_core` 4.3 MiB.
- Worker startup limit is 1 s; monitor on each deploy.

## 12. Resolved: dev-probe DDL under load

The `/db/roundtrip` dev probe originally ran `create_all` per request; under a
c=50 burst the local `wrangler dev` (Miniflare) proxy dropped connections
(`Error inside ProxyWorker ... Network connection lost`), while `/ready` stayed
stable to c=100. Fix (dev harness only, no production change): the harness table
is now created once via `scripts/dev_setup.py` / test fixtures, and
`/db/roundtrip` performs only transaction + INSERT/SELECT. After the fix, a
c=50/100 regression showed zero failures on both endpoints.

## 13. Deploy-time policy decisions

Resolved (not TBD):

- **Hyperdrive caching:** primary binding is cache-disabled (see §6).
- **Zero-downtime:** no contractual requirement; planned deployments aim for no
  intentional user-visible downtime; risky changes use expand → migrate → contract;
  forward-fix preferred over downgrade once data/code depends on the new schema;
  production deployments freeze during live competition rounds except emergency
  fixes. See `docs/deployment.md` ("Zero-downtime policy").

## 14. Known open items

- Staging environment wiring (config shape exists; Hyperdrive/Neon ids pending).
- Real Cloudflare/Neon deployment verification (no credentials yet).

## 15. Related docs

- Migrations: `docs/migrations.md` (shared Alembic workflow; how modules contribute).
- Deployment: `wrangler.jsonc` (dev/staging/production envs), `scripts/migrate.sh`,
  `scripts/verify_deployment.sh`.
