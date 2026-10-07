"""Cloudflare Python Worker entry: serves app.main through the Workers ASGI adapter."""

from urllib.parse import quote

from sqlalchemy.pool import NullPool
from workers import asgi

from app.core import db
from app.main import create_app

app = create_app()


@app.middleware("http")
async def bind_hyperdrive(request, call_next):
    # Connection details exist only on the request's env binding. NullPool:
    # a Worker cannot reuse a socket across requests (a pooled one hangs the
    # next request), and Hyperdrive already pools connections to Neon.
    if not db.engine_configured():
        hd = request.scope["env"].HYPERDRIVE
        db.configure_engine(
            f"postgresql+pg8000://{quote(hd.user, safe='')}:{quote(hd.password, safe='')}"
            f"@{hd.host}:{hd.port}/{hd.database}",
            poolclass=NullPool,
        )
    return await call_next(request)


Default = asgi.entrypoint(app)
