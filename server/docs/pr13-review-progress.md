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

- Bounded market/pricing cache with PostgreSQL snapshot freshness checks, transaction bypass, and expiry at quote deadlines.
- Authenticated per-round Python Durable Object sockets, hibernation, persistent sequences, price alarms, committed-change notifications, and REST fallback.
- Batched connection membership checks and a local ASGI stream for development.

Validation: all 468 PostgreSQL 18 tests pass with `PGTZ=UTC`, including cache concurrency/rollback, socket authorization, reconnect, and alarm coverage. Ruff and Worker packaging pass. A real local workerd smoke check covers successful and rejected trades, interval alarms, reconnects, round closure, and revoked team access.

See [market stream and cache documentation](market-stream.md) for client protocol, limits, and performance tradeoffs. Existing dynamic supply/demand pricing retains its formula.

## Deployment status

Changes target PR #13 on `integration/staging-backend`. Local runtime validation does not deploy to staging or production. The reviewer reply remains unposted pending user approval.
