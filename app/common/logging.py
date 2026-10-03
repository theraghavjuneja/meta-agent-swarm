"""Structured JSON logging via structlog.

Configure once at process start with ``configure_logging()``.
Every other module obtains a logger with ``get_logger(__name__)``.

Each log record is emitted as a single JSON line with the fields:
    timestamp  – ISO-8601 UTC, e.g. "2026-09-16T11:50:00Z"
    level      – lower-cased level name, e.g. "info"
    logger     – dotted module path, e.g. "app.assets.adapters.storage_fixture"
    event      – the first positional argument, e.g. "fixture_storage_saved"
    <kwargs>   – any extra keyword arguments passed at the call site

Example:
    logger.info("research_run_started", run_id=run_id, campaign_id=campaign_id)

    → {"timestamp": "2026-09-16T11:50:00Z", "level": "info",
       "logger": "app.research.activities",
       "event": "research_run_started", "run_id": "...", "campaign_id": "..."}
"""
from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(*, level: str | int = "INFO") -> None:
    """Configure structlog and the stdlib root logger.

    Call **once** at process start (e.g. in the worker/API entrypoint).
    Safe to call multiple times; subsequent calls are idempotent.
    """
    structlog.configure(
        processors=[
            # Merge stdlib ``extra`` dict into the structlog event dict.
            structlog.stdlib.ExtraAdder(),
            # Add log level as a lower-cased string.
            structlog.stdlib.add_log_level,
            # Add the logger name (module path).
            structlog.stdlib.add_logger_name,
            # ISO-8601 UTC timestamp.
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # If an exception is attached, render the traceback into
            # "exception" key so it stays inside the JSON envelope.
            structlog.processors.format_exc_info,
            # Re-order keys for readability: timestamp → level → logger →
            # event → everything else.  This is cosmetic only.
            structlog.processors.EventRenamer("event"),
            # Final step: serialise to JSON.
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level) if isinstance(level, str) else level
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Also configure the stdlib root logger so that libraries that emit
    # through logging (SQLAlchemy, httpx, asyncio …) are captured.
    _stdlib_level = level if isinstance(level, int) else getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=_stdlib_level,
        force=True,
    )
    # Quiet down noisy third-party libraries.
    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, _stdlib_level))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger for *name* (pass ``__name__``).

    Safe to call at module-import time, before ``configure_logging()``
    runs – structlog buffers records until a renderer is attached.
    """
    return structlog.get_logger(name)