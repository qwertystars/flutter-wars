"""Application entrypoint.

PLACEHOLDER bootstrap until Module A lands. G/H contribute routers through
MODULES. I/J (trading, auction) are mounted through their own composition
boundary: `runtime` (session + owner adapters) and `principal_dependency`
(verified identity) stay unset until Foundation/Auth supply them, so those
routes answer 503/401 instead of guessing.
"""

from collections.abc import Callable, Sequence
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auction.router import build_router as auction_router
from app.core.errors import install_error_handlers
from app.integration.auth import Principal
from app.integration.errors import BusinessError, ConfigurationRequired
from app.integration.runtime import BackendModules
from app.modules.market import router as market_router
from app.modules.pricing import router as pricing_router
from app.trading.router import build_router as trading_router

MODULES: dict[str, Sequence[APIRouter]] = {
    "market": (market_router.router, market_router.admin),
    "pricing": (pricing_router.router, pricing_router.admin),
}


def create_app(
    modules: Sequence[str] | None = None,
    *,
    runtime: BackendModules | None = None,
    principal_dependency: Callable[..., Principal] | None = None,
) -> FastAPI:
    app = FastAPI(title="Flutter Wars backend")
    install_error_handlers(app)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    for name in MODULES if modules is None else modules:
        for router in MODULES[name]:
            app.include_router(router)

    _mount_trading_and_auction(app, runtime, principal_dependency)
    return app


def _mount_trading_and_auction(
    app: FastAPI,
    runtime: BackendModules | None,
    principal_dependency: Callable[..., Principal] | None,
) -> None:
    def get_runtime() -> BackendModules:
        if runtime is None:
            raise ConfigurationRequired()
        return runtime

    def unconfigured_auth() -> Principal:
        raise HTTPException(status_code=401, detail="Authentication required.")

    auth = principal_dependency or unconfigured_auth

    def require_team(principal: Principal = Depends(auth)) -> UUID:
        if principal.team_id is None:
            raise HTTPException(status_code=403, detail="Participant team required.")
        return principal.team_id

    def require_organizer(principal: Principal = Depends(auth)) -> None:
        if not principal.organizer:
            raise HTTPException(status_code=403, detail="Organizer permission required.")

    @app.exception_handler(BusinessError)
    async def business_error(request: Request, exc: BusinessError):
        # Class-owned public text only: never echo adapter/SQL exception arguments.
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "The operation could not be completed.",
                }
            },
        )

    app.include_router(trading_router(get_runtime, require_team))
    app.include_router(auction_router(get_runtime, require_team, require_organizer))


app = create_app()
