# Deployment (Module M)

Backend: Cloudflare Python Worker → FastAPI → sync SQLModel/SQLAlchemy → psycopg
→ Hyperdrive → Neon PostgreSQL. Migrations run outside the Worker, directly
against Neon (control-plane operation), never through Hyperdrive.

## Prerequisite: Workers Paid plan

Current Worker bundle (dry-run): **~34.24 MiB uncompressed / ~7.66 MiB gzip**.
Cloudflare limits: 64 MiB uncompressed; **Workers Free 3 MiB compressed**;
**Workers Paid 10 MiB compressed**. This bundle therefore **requires Workers Paid**
unless the compressed bundle is reduced below the Free-plan limit. Free-plan
compatibility is **not** claimed.

## Hyperdrive caching decision: cache-disabled (primary binding)

Hyperdrive caches eligible read queries by default (`max_age` default 60s) and does
**not** invalidate cached results on writes. This platform has correctness-critical
reads — credits/wallet, inventory, market state, the read immediately following a
purchase/auction/transaction commit, IDE synchronization, and permission/auth reads
once those modules exist — where stale results are unacceptable. Therefore the
**primary `HYPERDRIVE` binding must target a cache-disabled Hyperdrive
configuration** (created with `--caching-disabled`, see step 2 below).

A future optimization MAY split this into `HYPERDRIVE_CACHED` (only for safe
catalog/public reads) and `HYPERDRIVE_FRESH`; correctness-sensitive reads must use
the fresh path. Cache behavior cannot be verified without real Cloudflare
credentials and is **not** claimed as production-tested.

## Environments

| Environment | Worker | DB target | APP_ENV | Binding |
| --- | --- | --- | --- | --- |
| development | `pywrangler dev` (local) | `DATABASE_URL` (Neon dev branch or local PG) | `development` | local Hyperdrive mode |
| staging | `flutter-wars-api-staging` | staging Hyperdrive → Neon staging branch | `staging` | `HYPERDRIVE` (staging id) |
| production | `flutter-wars-api-production` | production Hyperdrive → Neon production branch | `production` | `HYPERDRIVE` (production id) |

Local development deliberately does **not** go through a production Hyperdrive
binding; it uses `DATABASE_URL` directly.

## One-time setup (needs Cloudflare + Neon accounts)

1. Create Neon branches/projects for staging and production.
2. Create a **cache-disabled** Hyperdrive configuration per environment (replace the
   connection string with the Neon string; include `sslmode=require`):
   ```
   npx wrangler hyperdrive create flutter-wars-staging \
     --connection-string="postgresql://USER:PASSWORD@HOST/DB?sslmode=require" \
     --caching-disabled
   npx wrangler hyperdrive create flutter-wars-production \
     --connection-string="postgresql://USER:PASSWORD@HOST/DB?sslmode=require" \
     --caching-disabled
   ```
3. Put the returned ids into `wrangler.jsonc` (`env.staging` / `env.production`
   `hyperdrive[].id`), replacing the `REPLACE_WITH_*` placeholders. Hyperdrive ids
   are identifiers, not secrets, but are account-specific.

## Credentials / secrets

Nothing sensitive is committed. Configure these outside the repo:

| Name | Where | Purpose |
| --- | --- | --- |
| `CLOUDFLARE_API_TOKEN` | GitHub Actions secret | Deploy Worker |
| `CLOUDFLARE_ACCOUNT_ID` | GitHub Actions secret | Deploy Worker |
| `STAGING_DATABASE_URL` | GitHub Actions secret (env `staging`) | Run migrations |
| `PRODUCTION_DATABASE_URL` | GitHub Actions secret (env `production`) | Run migrations |
| `STAGING_BASE_URL` / `PRODUCTION_BASE_URL` | GitHub Actions variables | Smoke-test URL |

No Worker runtime secrets are needed (the Hyperdrive binding carries DB creds). If
one is ever added: `wrangler secret put <KEY> --env <env>` (secrets are
non-inheritable and must be set per environment). Locally use `.dev.vars`.

GitHub Environments `staging` / `production` gate the deploy jobs; add **required
reviewers** to `production` for manual approval.

## Deployment order

Prescribed flow (see `.github/workflows/deploy.yml`):

1. Validate code (checkout, dependency install).
2. Run tests.
3. Validate migration state (`alembic check` — fail on drift).
4. Deploy the compatible application (`pywrangler deploy --env <env>`).
5. Apply migrations (`alembic upgrade head`).
6. Verify deployment + health/readiness (`scripts/verify_deployment.sh`).
7. Mark the release successful.

**Compatibility rule (critical).** A migration is applied *after* the app deploy,
so the newly deployed app must remain correct against the pre-migration schema.
Migrations must therefore be **backward compatible (expand pattern)**:
- Expand first: add nullable columns / new tables / new indexes.
- Migrate/backfill and switch reads/writes across releases.
- Contract (drop/rename/constrain) only in a later release, after no running app
  version uses the old shape.

If a new app version genuinely cannot run against the old schema, run that
migration **before** the deploy instead, and still keep it backward compatible with
the currently deployed version. Never pair an incompatible deploy with a
destructive migration.

## Commands

```
# deploy (from server/), Cloudflare auth required
scripts/deploy.sh staging
scripts/deploy.sh production

# migrations (control plane; targets Neon directly)
DATABASE_URL="postgresql://...?sslmode=require" scripts/migrate.sh

# verify a deployment
scripts/verify_deployment.sh https://flutter-wars-api-staging.<subdomain>.workers.dev
```

## Zero-downtime policy (decided — closed)

- No hard contractual zero-downtime requirement.
- Planned application deployments should aim for no intentional user-visible downtime.
- Risky schema changes use **expand → migrate → contract**.
- Do not perform unsafe destructive migrations during normal deployment.
- Prefer **forward-fix** over a DB downgrade once deployed application/data depends
  on the new schema.
- During live competition rounds, **freeze** normal production deployments except
  emergency fixes.

## Rollback

- Application: redeploy the previous Worker version — `wrangler versions list`,
  then `wrangler rollback` (or `wrangler versions deploy <version-id>`).
- Migrations: see `docs/migrations.md` (prefer forward-fix once data/code depends
  on the change; only `downgrade` a reversible revision with no dependent deploy).

## Status: real deployment UNVERIFIED

No Cloudflare or Neon credentials were available in this environment, so the real
`Cloudflare → Worker → Hyperdrive → Neon` path was **not** executed. Verified
locally only: Worker config validity, `wrangler`/`pywrangler` dry-run bundle,
local Hyperdrive-mode connectivity to Postgres, and the migration workflow. The
following remain externally unverified: authenticated `wrangler deploy`, real
Hyperdrive creation/binding, real Neon connectivity from the edge, and the CI
deploy jobs (which run only in GitHub Actions with the secrets/vars above).
