import asyncio
from typing import Callable, TypeVar

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.repository import Repository

_T = TypeVar("_T")


settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_session():
    return SessionLocal()


async def run_in_session(fn: Callable[[Session], _T]) -> _T:
    """Run fn(session) against a fresh get_session() inside a worker thread.

    aiogram handlers are async and share one event loop for the whole bot
    process; calling synchronous SQLAlchemy I/O directly from one blocks
    every other in-flight update until the query returns. Use this from
    async handlers for straight-line DB-only work (no other awaits
    in-between) instead of `with get_session() as session:` so the event
    loop stays free.
    """
    def _call() -> _T:
        with get_session() as session:
            return fn(session)

    return await asyncio.to_thread(_call)


class ScopedRepository:
    """Repository-shaped facade where every call opens and closes its own
    short-lived DB session, instead of one session held open for an entire
    Celery task.

    A long task (download + ffmpeg transcode/compress can take several
    minutes) that holds a single session open the whole time risks its
    connection going idle long enough for Postgres or an intermediate
    network device to close it — pool_pre_ping only re-validates a
    connection when it's checked OUT of the pool, which never happens again
    once one connection is held for the task's full duration, so the drop
    only surfaces as an OperationalError on the next query deep in the task.
    Opening a fresh session per call re-triggers pre_ping every time, so a
    dead pooled connection gets transparently replaced instead.

    Each Repository method already commits internally, so this changes
    nothing transactionally — it only changes how long a connection is
    checked out between calls.
    """

    def __getattr__(self, name: str):
        def _call(*args, **kwargs):
            with get_session() as session:
                return getattr(Repository(session), name)(*args, **kwargs)

        return _call
