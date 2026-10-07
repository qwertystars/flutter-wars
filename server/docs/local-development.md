# Local Development

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (dependency + Python management, Python 3.14)
- Node.js 18+ (for `wrangler` / `pywrangler`)
- A PostgreSQL database — a Neon development branch, or local Postgres

## 1. Install dependencies

```
cd server
uv sync --dev          # Python (runtime + dev tools)
npm ci                 # wrangler (Worker tooling)
```

## 2. Configure the environment

```
cp .env.example .env   # then edit DATABASE_URL
```

`.env` is gitignored. Never commit it. For the local Worker runtime
(`pywrangler dev`) copy `.dev.vars.example` to `.dev.vars` instead of using `.env`.
See [environment-reference.md](environment-reference.md).

## 3. Run the tests

```
export DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/DB?sslmode=require"
uv run pytest -q
```

Tests without a `DATABASE_URL` skip the database-dependent cases.

## 4. Prepare the dev/test harness schema

`/db/roundtrip` and the tests use a probe table that is **not** part of migrations:

```
uv run python scripts/dev_setup.py
```

## 5. Run the backend locally (uvicorn)

The app reads configuration from the process environment (it does not auto-load
`.env`), so load it into the shell first:

```
set -a; . ./.env; set +a
uv run uvicorn app:app --app-dir src --reload --port 8899
curl http://127.0.0.1:8899/health
curl http://127.0.0.1:8899/ready
```

`/ready` should report `"database": {"ok": true, "source": "settings"}`. Use the
same `set -a; . ./.env; set +a` before `pytest`. `pywrangler dev` loads `.dev.vars`
automatically.

## 6. Run the Worker locally (Hyperdrive local mode)

```
export CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE="postgresql://USER:PASSWORD@HOST:5432/DB?sslmode=require"
uv run pywrangler dev
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8787/ready     # source: "hyperdrive"
```

Local Hyperdrive mode connects **directly** to the database (no Hyperdrive pooling
or caching). Production uses the real `HYPERDRIVE` binding.

## Database environment separation

| Use | Connection |
| --- | --- |
| Local dev / tests | `DATABASE_URL` directly (Neon dev branch or local Postgres) |
| Production | Worker → `HYPERDRIVE` binding → Neon |

Local development deliberately does not route through a production Hyperdrive
binding.

## Migrations

See [migrations.md](migrations.md). Quick start:

```
DATABASE_URL=... uv run alembic upgrade head
DATABASE_URL=... uv run alembic current
```
