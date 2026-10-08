# Environment Variable Reference

Never commit real values. Examples use placeholders only.

## Application / database settings

`app/infra/settings.py` validates the infra settings below. The current FastAPI
app uses `app/core/config.py` for `DATABASE_URL`; the Worker configures its pg8000
engine from Hyperdrive. Infra libpq options are not applied to the Worker engine.

| Variable | Purpose | Required | Development | Production | Configured where |
| --- | --- | --- | --- | --- | --- |
| `APP_ENV` | Environment name; validated against `development`, `test`, `staging`, `production` | optional (default `development`) | `development` | `production` (staging is a future option) | `.env` / Worker `vars` (`cloudflare/wrangler.jsonc`) |
| `DATABASE_URL` | Postgres connection string (SQLAlchemy URL) | required locally; not used by the Worker | dev branch URL | not set in Worker (Hyperdrive provides it) | `.env`, CI/secrets for migrations |
| `DB_CONNECT_TIMEOUT_SECONDS` | psycopg infra-helper connect timeout, 1–60 | optional (default 5) | `5` | not applied by Worker | `.env` / process environment |
| `DB_APPLICATION_NAME` | psycopg infra-helper `application_name` | optional (default `flutter-wars`) | — | not applied by Worker | `.env` / process environment |

## Test databases

| Variable | Purpose | Required database name |
| --- | --- | --- |
| `TEST_DATABASE_URL` | PostgreSQL module and infra tests (psycopg) | disposable `*_modules_test` |
| `MIGRATION_DATABASE_URL` | `tests/test_migrations.py` (psycopg) | disposable `*_migration_test` |

These tests reset tables. `SPIKE_DATABASE_URL` is no longer used.

## Local Worker runtime

| Variable | Purpose | Notes |
| --- | --- | --- |
| `CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE` | Local Hyperdrive mode connection string | `cloudflare/.dev.vars` (gitignored); format `CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_<BINDING>` |

## Deployment / CI (never in source; reference by name only)

| Name | Type | Purpose | Where configured |
| --- | --- | --- | --- |
| `CLOUDFLARE_API_TOKEN` | secret | Deploy the Worker | GitHub Actions secret |
| `CLOUDFLARE_ACCOUNT_ID` | secret | Target Cloudflare account | GitHub Actions secret |
| `PRODUCTION_DATABASE_URL` | secret | Run migrations (production Neon direct endpoint) | GitHub Environment `production` secret |
| `PRODUCTION_BASE_URL` | variable | Smoke-test URL (production) | GitHub Actions variable |

`DEPLOY_ENABLED` is a repository variable; set it to `true` to enable the
production deploy workflow after green CI on `main` (or manual dispatch).
There are no Wrangler staging/production environments.

## Worker runtime secrets

**None required.** The `HYPERDRIVE` binding carries the database credentials.
`APP_ENV` is a non-sensitive plain `var`. If a runtime secret is ever added, set it
with `wrangler secret put <KEY>` from `cloudflare/`; locally use `cloudflare/.dev.vars`.

## Rules

- `.env`, `.dev.vars` and all their variants are gitignored — never commit them.
- Diagnostics never print secret values (`Settings.describe()`, `DatabaseTarget`
  redact credentials; the HTTP readiness payload exposes only status).
- `alembic.ini` contains no URL; migrations read `DATABASE_URL` or `-x db_url`.
