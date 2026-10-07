# Environment Variable Reference

Never commit real values. Examples use placeholders only.

## Application / database settings (read by `src/settings.py`)

| Variable | Purpose | Required | Development | Production | Configured where |
| --- | --- | --- | --- | --- | --- |
| `APP_ENV` | Environment name; validated against `development`, `test`, `staging`, `production` | optional (default `development`) | `development` | `production`/`staging` | `.env` / Worker `vars` (`wrangler.jsonc`) |
| `DATABASE_URL` | Postgres connection string (SQLAlchemy URL) | required locally; not used by the Worker | dev branch URL | not set in Worker (Hyperdrive provides it) | `.env`, CI/secrets for migrations |
| `DB_CONNECT_TIMEOUT_SECONDS` | libpq connect timeout, 1–60 | optional (default 5) | `5` | `5` | `.env` / Worker `vars` |
| `DB_APPLICATION_NAME` | libpq `application_name` for observability | optional (default `flutter-wars`) | — | — | `.env` / Worker `vars` |

## Local Worker runtime

| Variable | Purpose | Notes |
| --- | --- | --- |
| `CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE` | Local Hyperdrive mode connection string | `.dev.vars` (gitignored); format `CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_<BINDING>` |

## Deployment / CI (never in source; reference by name only)

| Name | Type | Purpose | Where configured |
| --- | --- | --- | --- |
| `CLOUDFLARE_API_TOKEN` | secret | Deploy the Worker | GitHub Actions secret |
| `CLOUDFLARE_ACCOUNT_ID` | secret | Target Cloudflare account | GitHub Actions secret |
| `STAGING_DATABASE_URL` | secret | Run migrations (staging Neon) | GitHub Environment `staging` secret |
| `PRODUCTION_DATABASE_URL` | secret | Run migrations (production Neon) | GitHub Environment `production` secret |
| `STAGING_BASE_URL` | variable | Smoke-test URL (staging) | GitHub Actions variable |
| `PRODUCTION_BASE_URL` | variable | Smoke-test URL (production) | GitHub Actions variable |

## Worker runtime secrets

**None required.** The `HYPERDRIVE` binding carries the database credentials.
`APP_ENV` is a non-sensitive plain `var`. If a runtime secret is ever added, set it
with `wrangler secret put <KEY> --env <env>` (secrets are non-inheritable and must
be set per environment); locally use `.dev.vars`.

## Rules

- `.env`, `.dev.vars` and all their variants are gitignored — never commit them.
- Diagnostics never print secret values (`Settings.describe()`, `DatabaseTarget`
  and the readiness payload all redact credentials).
- `alembic.ini` contains no URL; migrations read `DATABASE_URL` from the environment.
