"""Composition root: connects the independently owned modules at startup.

Every cross-module link is registered here, so each module keeps depending only on
another module's published port or service:

  B -> K   Module B's team directory backs Module K's team management.
  K -> B   Module K's organizer table decides organizer-only logins.
  F -> C   Module F's inventory backs the IDE sync state (Module C).
  G, I -> K  market status and the cross-team transaction feed for the dashboard.
  D, E, F, G, H, K -> I, J  the trading and auction runtime over the owners' adapters,
           with Module K's emergency freeze checked first in every participant mutation.
"""

import base64
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from sqlmodel import Session

from app.auction.router import build_router as auction_router
from app.contracts.principal import Principal
from app.core.auth import require_participant
from app.core.db import session_factory
from app.core.errors import AppError
from app.integration.contracts import Adapters
from app.integration.errors import ConfigurationRequired
from app.integration.owner_adapters import CatalogAdapter, InventoryAdapter, LedgerAdapter
from app.integration.runtime import BackendModules
from app.modules.admin import Permission, ensure_not_frozen, ports, require_permission
from app.modules.admin import repository as admin_repo
from app.modules.authentication.service import set_organizer_lookup
from app.modules.authentication.team_directory import TeamDirectory
from app.modules.ide_sync.contracts import WidgetAllowance
from app.modules.inventory import service as inventory
from app.modules.market import service as market
from app.modules.market.adapter import MarketAdapter, release_unsold_lot
from app.modules.pricing.adapter import PricingAdapter
from app.trading.repository import TradeRepository
from app.trading.router import build_router as trading_router
from app.trading.schemas import TradeResponse


def owner_adapters(session: Session) -> Adapters:
    return Adapters(
        market=MarketAdapter(session),
        pricing=PricingAdapter(session),
        ledger=LedgerAdapter(session),
        inventory=InventoryAdapter(session),
        catalog=CatalogAdapter(session),
    )


def build_runtime() -> BackendModules:
    return BackendModules(
        session_factory=session_factory,
        adapter_factory=owner_adapters,
        no_bid_handler=release_unsold_lot,
        freeze_guard=ensure_not_frozen,
    )


class InventoryReader:
    """Module F's read contract for the IDE (Module C), in its own short session."""

    def get_team_inventory(self, team_id: str) -> list[WidgetAllowance]:
        with session_factory() as session:
            items = inventory.get_team_inventory(session, UUID(team_id))
        return [WidgetAllowance(widget_id=item.widget_id, quantity=item.quantity) for item in items]


def _is_organizer(session: Session, email: str) -> bool:
    return admin_repo.get_active_organizer_by_email(session, email) is not None


def _encode_cursor(created_at: datetime, trade_id: UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{trade_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        created_at, trade_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(created_at), UUID(trade_id)
    except ValueError as exc:
        raise AppError("INVALID_CURSOR", "The cursor is not valid.", 422) from exc


def transaction_feed(session: Session, query: ports.TransactionQuery) -> dict[str, Any]:
    before = _decode_cursor(query.cursor) if query.cursor else None
    trades = TradeRepository(session).feed(team_id=query.team_id, limit=query.limit, before=before)
    items = [
        TradeResponse.model_validate(t).model_dump(mode="json") | {"team_id": str(t.team_id)}
        for t in trades
    ]
    last = trades[-1] if len(trades) == query.limit else None
    return {"items": items, "next_cursor": _encode_cursor(last.created_at, last.id) if last else None}


def register_ports() -> None:
    ports.set_team_directory(TeamDirectory())
    ports.set_market_status_provider(market.status_summary)
    ports.set_transaction_feed(transaction_feed)
    set_organizer_lookup(_is_organizer)


def mount_trading_and_auction(app: FastAPI) -> None:
    """Modules I and J. Tests may replace app.state.backend_runtime."""
    app.state.backend_runtime = build_runtime()

    def get_runtime(request: Request) -> BackendModules:
        runtime = getattr(request.app.state, "backend_runtime", None)
        if runtime is None:
            raise ConfigurationRequired()
        return runtime

    def require_team(principal: Principal = Depends(require_participant)) -> UUID:
        assert principal.team_id is not None
        return principal.team_id

    organizer = require_permission(Permission.MARKET_MANAGE)
    app.include_router(trading_router(get_runtime, require_team))
    app.include_router(auction_router(get_runtime, require_team, organizer))


def configure(app: FastAPI) -> None:
    register_ports()
    app.state.inventory_reader = InventoryReader()
    mount_trading_and_auction(app)
