"""Cloudflare Python Worker entry: serves app.main through the Workers ASGI adapter.

A Worker receives its configuration (vars, secrets, the Hyperdrive binding) with each
request, not in os.environ. So the app is built on the first request: the Hyperdrive
engine is configured first (Module A then reuses it), Module A's Settings are read
from the bindings, and Module B's Google calls switch to the Workers fetch API.
"""

import os

from pyodide.http import pyfetch
from sqlalchemy.pool import NullPool
from workers import WorkerEntrypoint, asgi

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


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        global _app
        if _app is None:
            _app = _build(self.env)
        return await asgi.fetch(_app, request, self.env, self.ctx)
