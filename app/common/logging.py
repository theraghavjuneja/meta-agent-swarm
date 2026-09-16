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

# Processors shared between the structlog chain and the stdlib bridge so that
# records from third-party libraries (httpx, SQLAlchemy …) are rendered the
# same way.
_SHARED_PROCESSORS: list = [
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.EventRenamer("event"),
]


def configure_logging(*, level: str | int = "INFO") -> None:
    """Configure structlog + the stdlib root logger.

    Call **once** at process start (e.g. in the worker/API entrypoint).
    Safe to call multiple times; subsequent calls are idempotent.
    """
    _level_int = level if isinstance(level, int) else getattr(logging, level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            # Final step: serialise to JSON.
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_level_int),
        context_class=dict,
        # Use the stdlib logger factory so that ``add_logger_name`` can read
        # ``logger.name`` from the underlying stdlib Logger object.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Wire stdlib so that third-party libraries route through structlog's
    # JSONRenderer rather than their own formatting.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=_level_int,
        force=True,
    )
    # Feed stdlib records back through structlog's renderer.
    logging.getLogger().handlers[0].setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                *_SHARED_PROCESSORS,
                structlog.processors.JSONRenderer(),
            ],
            foreign_pre_chain=_SHARED_PROCESSORS,
        )
    )

    # Quiet down noisy third-party libraries.
    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, _level_int))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger for *name* (pass ``__name__``).

    Safe to call at module-import time, before ``configure_logging()``
    runs – structlog buffers records until a renderer is attached.
    """
    return structlog.get_logger(name)