"""Cloudflare Python Worker entry: serves app.main through the Workers ASGI adapter."""

from sqlalchemy.pool import NullPool
from workers import asgi

from app.core import db
from app.infra.db import target_from_hyperdrive
from app.main import create_app

app = create_app()


@app.middleware("http")
async def bind_hyperdrive(request, call_next):
    # Connection details exist only on the request's env binding. NullPool:
    # a Worker cannot reuse a socket across requests (a pooled one hangs the
    # next request), and Hyperdrive already pools connections to Neon.
    if not db.engine_configured():
        # pg8000 (pure Python): psycopg cannot load in Pyodide (no libpq), and
        # pg8000 takes no sslmode (the Worker -> Hyperdrive hop is not TLS).
        url = target_from_hyperdrive(request.scope["env"].HYPERDRIVE).url
        db.configure_engine(url.set(drivername="postgresql+pg8000", query={}), poolclass=NullPool)
    return await call_next(request)


Default = asgi.entrypoint(app)
