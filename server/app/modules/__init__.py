"""Explicit module-router registration (Module A), then cross-module wiring."""

from fastapi import FastAPI

from app.modules.admin.catalog_router import router as admin_catalog_router
from app.modules.admin.router import public_router as admin_public_router
from app.modules.admin.router import router as admin_router
from app.modules.admin.team_assets_router import router as admin_team_assets_router
from app.modules.authentication.router import router as authentication_router
from app.modules.catalog.router import router as catalog_router
from app.modules.foundation.router import router as foundation_router
from app.modules.ide_sync.router import router as ide_sync_router
from app.modules.inventory.router import router as inventory_router
from app.modules.ledger.router import router as ledger_router
from app.modules.market import router as market_router
from app.modules.pricing import router as pricing_router


def register_modules(app: FastAPI) -> None:
    """Register independently owned feature routers explicitly."""
    app.include_router(foundation_router)  # A: /health, /ready
    app.include_router(authentication_router)  # B: /auth
    app.include_router(ide_sync_router)  # C: /ide/state, /admin/teams/{id}/api-keys
    app.include_router(catalog_router)  # D: /widgets
    app.include_router(ledger_router)  # E: /wallet
    app.include_router(inventory_router)  # F: /inventory
    app.include_router(market_router.router)  # G: /market
    app.include_router(market_router.admin)  # G: /admin/market
    app.include_router(pricing_router.router)  # H: /market/listings/{id}/price
    app.include_router(pricing_router.admin)  # H: /admin/market/.../pricing
    app.include_router(admin_public_router)  # K: /controls
    app.include_router(admin_router)  # K: /admin
    app.include_router(admin_team_assets_router)  # K: /admin/teams/{id}/wallet|credits|inventory
    app.include_router(admin_catalog_router)  # K: /admin/widgets (Module D's organizer routes)

    # I and J (trading, auction) and every cross-module port.
    from app.integration import wiring

    wiring.configure(app)
