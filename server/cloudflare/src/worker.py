"""Cloudflare Python Worker entry: serves app.main through the Workers ASGI adapter.

A Worker receives its configuration (vars, secrets, the Hyperdrive binding) with each
request, not in os.environ. So the app is built on the first request: the Hyperdrive
engine is configured first (Module A then reuses it), Module A's Settings are read
from the bindings, and Module B's Google calls switch to the Workers fetch API.
"""

import logging
import os
from urllib.parse import urlparse
from uuid import UUID

from pyodide.http import pyfetch
from round_stream import RoundStream
from sqlalchemy.pool import NullPool
from workers import Response, WorkerEntrypoint, asgi

__all__ = ["Default", "RoundStream"]

# Module A Settings, from wrangler vars and secrets (see wrangler.jsonc / docs/deployment.md).
SETTINGS = (
    "APP_NAME",
    "ENVIRONMENT",
    "LOG_LEVEL",
    "DATABASE_CONNECT_TIMEOUT_SECONDS",
    "DATABASE_POOL_SIZE",
    "DATABASE_MAX_OVERFLOW",
    "JWT_SECRET_KEY",
    "JWT_ALGORITHM",
    "JWT_ACCESS_TOKEN_MINUTES",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
)
_app = None


async def _get_json(url):
    from app.modules.authentication.http import HttpError

    response = await pyfetch(url)
    if not response.ok:
        raise HttpError(str(response.status))
    return await response.json()


async def _post_form(url, data):
    from urllib.parse import urlencode

    from app.modules.authentication.http import HttpError

    response = await pyfetch(
        url,
        method="POST",
        body=urlencode(data),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if not response.ok:
        raise HttpError(str(response.status))
    return await response.json()


def _run_blocking_inline():
    """A Worker has one thread: run FastAPI's sync handlers inline, not in a thread pool.

    The workers SDK ships the same patch through an import hook that does not always
    apply; doing it here keeps sync route handlers working either way.
    """
    import anyio.to_thread

    async def run_sync(func, *args, abandon_on_cancel=False, cancellable=None, limiter=None):
        return func(*args)

    anyio.to_thread.run_sync = run_sync


def _build(env):
    _run_blocking_inline()
    from app.core import db
    from app.infra.db import target_from_hyperdrive

    # pg8000 (pure Python): psycopg cannot load in Pyodide (no libpq), and pg8000
    # takes no sslmode (the Worker -> Hyperdrive hop is not TLS). NullPool: a Worker
    # cannot reuse a socket across requests, and Hyperdrive already pools to Neon.
    url = target_from_hyperdrive(env.HYPERDRIVE).url.set(drivername="postgresql+pg8000", query={})
    db.configure_engine(url, poolclass=NullPool)
    for name in SETTINGS:
        value = getattr(env, name, None)
        if value is not None:
            os.environ[name] = str(value)
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)

    from app.modules.authentication import http

    http.install(get=_get_json, post=_post_form)
    from app.main import app  # builds the app from the Settings above

    return app


def get_app(env):
    global _app
    if _app is None:
        _app = _build(env)
    return _app


async def notify_streams(env, path):
    """Called only after successful mutating handlers have explicitly committed.

    Notification failure cannot turn a committed trade into an HTTP failure. Each
    connected DO also checks every five seconds, so clients recover missed signals.
    """
    try:
        from app.contracts.market import MarketGateway
        from app.core.db import session_factory
        from app.core.services import gateway

        round_ids = set()
        with session_factory() as session:
            current = gateway(MarketGateway, session).current_round_id()
            if current:
                round_ids.add(current)
        parts = path.strip("/").split("/")
        if "rounds" in parts:
            try:
                round_ids.add(UUID(parts[parts.index("rounds") + 1]))
            except (ValueError, IndexError):
                pass
        for round_id in round_ids:
            stub = env.ROUND_STREAM.getByName(str(round_id))
            await stub.fetch("https://round-stream/refresh", method="POST")
    except Exception:
        logging.getLogger("flutterwars.stream").warning("market stream notification failed")


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        app = get_app(self.env)
        path = urlparse(request.url).path
        if path.startswith("/market/rounds/") and path.endswith("/stream"):
            if request.method != "GET" or (request.headers.get("Upgrade") or "").lower() != "websocket":
                return Response("WebSocket upgrade required", status=426)
            try:
                round_id = UUID(path.split("/")[-2])
            except ValueError:
                return Response("Invalid round", status=400)
            return await self.env.ROUND_STREAM.getByName(str(round_id)).fetch(request)
        response = await asgi.fetch(app, request, self.env, self.ctx)
        if (
            request.method in ("POST", "PATCH", "DELETE")
            and 200 <= response.status < 300
            and not path.endswith("/stream-ticket")
            and path.startswith(
                (
                    "/market",
                    "/purchases",
                    "/sales",
                    "/admin/market",
                    "/admin/widgets",
                    "/admin/auctions",
                    "/admin/teams",
                )
            )
        ):
            self.ctx.waitUntil(notify_streams(self.env, path))
        return response
