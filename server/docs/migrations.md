# Shared Migration Workflow (Module M)

Module M owns the migration **execution process**. It does NOT own business
entities. Feature modules own their SQLModel entities and their migrations, and
contribute them to this one shared, ordered history.

## Tool: Alembic

Chosen because it is SQLAlchemy-native (SQLModel is built on SQLAlchemy), supports
versioned/ordered/reviewable revisions, offline SQL generation, and drift
detection (`alembic check`). It uses the same sync SQLModel/SQLAlchemy + psycopg
stack as the app. Alembic is a **dev-only** dependency — it is never bundled into
the Worker.

## Layout

```
server/
  alembic.ini                       # script_location only; URL comes from env.py
  migrations/
    env.py                          # shared environment (DATABASE_URL, metadata, exclusions)
    metadata_registry.py            # feature modules register their models here
    script.py.mako                  # revision template
    versions/
      0001_infrastructure_baseline.py   # empty baseline (no business tables)
```

## Commands

Run from `server/` with `DATABASE_URL` set:

| Task | Command |
| --- | --- |
| Apply all pending | `uv run alembic upgrade head` (or `scripts/migrate.sh`) |
| Current revision | `uv run alembic current` |
| History | `uv run alembic history --verbose` |
| Drift check (no DB change) | `uv run alembic check` |
| Autogenerate a revision | `uv run alembic revision --autogenerate -m "widget catalog"` |
| Empty/manual revision | `uv run alembic revision -m "..."` |
| Roll back one | `uv run alembic downgrade -1` |
| Offline SQL preview | `uv run alembic upgrade head --sql` |

`-x db_url=...` overrides `DATABASE_URL` for a single invocation.

## How future modules add migrations

1. Create your SQLModel entities in your module, e.g. `src/app/modules/<module>/models.py`.
2. Register the module once in `migrations/metadata_registry.py`:

   ```python
   FEATURE_MODEL_MODULES = (
       "app.modules.catalog.models",  # Module D
   )
   ```

   Module M never imports business code directly — this registry is the only seam.
3. Generate the revision: `uv run alembic revision --autogenerate -m "<module>: <change>"`.
4. **Review the generated file** (autogenerate is a draft, not authority): confirm
   tables/columns/indexes, check ordering, and add explicit `downgrade()` logic.
5. Apply and verify locally against a Neon dev branch:
   `uv run alembic upgrade head && uv run alembic check`.
6. Commit the migration file with your module code. Migrations are reviewed like code.

Conventions:
- One logical change per revision; never edit a revision that has been applied to a shared database.
- `down_revision` must form a single linear history (no divergent heads).
- Prefer expand -> migrate -> contract for risky changes (see below).

## Local execution

```
export DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/DB?sslmode=require"
uv run alembic upgrade head
```

## Production / staging execution

Migrations run **outside** the Worker and connect to Neon **directly** (not through
Hyperdrive), using a `DATABASE_URL` secret provided by the deploy environment:

```
DATABASE_URL=... scripts/migrate.sh      # uv run alembic upgrade head
```

Deployment ordering (the authoritative flow is in [deployment.md](deployment.md)):
`validate -> deploy the compatible app -> apply migrations`. Because the migration
is applied **after** the deploy, it must remain compatible with the previously
deployed app:
1. Expand first — add nullable columns / new tables / new indexes so the currently
   running app is unaffected.
2. Backfill and switch reads/writes across releases.
3. Contract (drop/rename/constrain) only later, once no running version uses the old shape.
4. If the new app genuinely cannot run against the old schema, migrate **before**
   deploy instead — still keeping the migration compatible with the currently
   deployed version.
Run `alembic check` before deploy to catch drift.

## Recovery

**Migration fails during apply:** Alembic runs each revision in a transaction
(Postgres transactional DDL), so a failed revision rolls back and the DB stays at
the previous revision. Deployment must stop on non-zero exit (do not proceed to
app deploy). Inspect state with `uv run alembic current` and `uv run alembic history`.

**Choose rollback vs forward-fix:**
- Safe to `alembic downgrade -1` when the revision is reversible and no application
  version already depends on the new schema. Remember: a schema rollback also
  requires the previously deployed app version.
- Prefer **forward-fix** (add a new corrective revision) once the schema change has
  been used by deployed code or written production data — rolling back could destroy
  or orphan data.

**Stuck at a partial state:** never hand-edit the `alembic_version` table or the
schema in production. Inspect, then add a corrective revision.

## Guardrails

- No manual production database edits in normal deployment.
- The `infra_probe` dev/test harness table is excluded from migrations
  (`migrations/env.py` `include_object`) and must never enter the shared history.
- Never commit `DATABASE_URL` or any credential. Migrations read it from the
  environment.
