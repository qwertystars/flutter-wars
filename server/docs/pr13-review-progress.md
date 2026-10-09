# PR #13 review progress

Source: https://github.com/gdg-vitc/flutter-wars/pull/13#issuecomment-6087145226

The agreed order is service boundaries, performance, then live price delivery. Changes belong on `integration/staging-backend`. The reviewer reply remains a draft and has not been posted.

## Implemented locally

- Shared contracts and session-bound gateway registry; each owner registers its own gateway and routes.
- Removed central wiring and owner adapters. Core and feature modules use contracts rather than another feature's internals.
- Moved catalog, wallet, and inventory organizer routes to their owners, preserving HTTP paths, authorization, and transaction/audit behavior.
- Market publishes a SQL listing/round read model so Pricing retains one-statement consistent quote snapshots.
- Batched organizer listing pricing configuration and catalog names.
- CI boundary checks and a query-count regression test for organizer round detail.

Validation: full PostgreSQL 18 suite passed (444 tests); the subsequently added organizer query-count regression passed separately. Run database tests with `PGTZ=UTC`, as in CI. Ruff and the Cloudflare Worker packaging dry run pass. An existing flaky auction privacy assertion now checks the public response fields instead of searching random UUIDs for the digits of a bid amount.

## Remaining review work

1. Short per-isolate cache for market state and pricing, bounded by `valid_until`, with a database version check and invalidation after committed trades, round changes, and organizer pricing edits. Never use cached reads to authorize a mutation or publish uncommitted values.
2. Python Durable Object WebSocket delivery, one object per round. Authenticate connections, broadcast committed changes, and schedule alarms at `valid_until`. Include `server_time` and `valid_until`; REST remains the fallback. Verify reconnects, alarms, lifecycle changes, and commit/rollback delivery in the Worker runtime.
3. Existing dynamic supply/demand pricing already evaluates elapsed intervals lazily. Preserve the formula unless the reviewer requests a change.
4. Once all review work is validated, update PR #13 and staging according to the existing deployment workflow. Posting the reviewer reply still needs the user's approval.
