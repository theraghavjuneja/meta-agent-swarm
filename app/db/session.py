"""Async engine and session lifecycle management.

Pure infrastructure: no queries, no repositories, no business logic. Later
packages build their own `repository.py` on top of `session_scope()` /
`get_db_session()`.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger
from app.config import get_settings

logger = get_logger(__name__)

_DSN_CREDENTIALS_RE = re.compile(r"//([^:@/]+):([^@/]+)@")


def _redact_dsn(dsn: str) -> str:
    """Redact the password portion of a DSN for safe logging."""
    return _DSN_CREDENTIALS_RE.sub(r"//\1:***@", dsn)


def _build_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
    )


# Constructed once at import time — not recreated per request/session.
engine: AsyncEngine = _build_engine()

# `expire_on_commit=False` so ORM objects (and DTOs built from them) remain
# usable after commit without triggering an implicit lazy reload.
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session; commit on success, roll back on exception.

    Unexpected database errors are wrapped as
    `app.common.exceptions.InfrastructureError` so callers never have to
    catch raw driver/SQLAlchemy exceptions directly — consistent with how
    Module 1's retry helper wraps provider errors.
    """
    session = async_session_factory()
    try:
        yield session
        await session.commit()
    except InfrastructureError:
        await session.rollback()
        raise
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.error(
            "Database session error against %s",
            _redact_dsn(get_settings().database_url),
            exc_info=exc,
        )
        raise InfrastructureError("Database operation failed") from exc
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: `session: AsyncSession = Depends(get_db_session)`.

    Not wired into any router yet — no router exists until the `api`
    module — but shaped correctly (an `AsyncGenerator` that yields exactly
    one session) for `Depends()` to use once it does.
    """
    async with session_scope() as session:
        yield session