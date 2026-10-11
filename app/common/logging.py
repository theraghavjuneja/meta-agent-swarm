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
    """Configure structlog + the stdlib root logger.

    Call **once** at process start (e.g. in the worker/API entrypoint).
    Safe to call multiple times; subsequent calls are idempotent.
    """
    _level_int = (
        level if isinstance(level, int) else getattr(logging, level.upper(), logging.INFO)
    )

    structlog.configure(
        processors=[
            # Add lower-cased level name ("info", "warning", …).
            structlog.stdlib.add_log_level,
            # ISO-8601 UTC timestamp.
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # Render tracebacks into "exception" key inside the JSON envelope.
            structlog.processors.format_exc_info,
            # Rename the positional "event" argument to the "event" key.
            structlog.processors.EventRenamer("event"),
            # Final step: serialise everything to a JSON string.
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_level_int),
        context_class=dict,
        # PrintLoggerFactory writes directly to stdout — no stdlib routing, no
        # double-formatting.
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Configure the root stdlib logger for third-party libraries that emit
    # through the stdlib (httpx, SQLAlchemy, asyncio …).  Their records go
    # through a plain StreamHandler with a minimal text format — simple and
    # interference-free with the structlog JSON stream above.
    root = logging.getLogger()
    root.setLevel(_level_int)
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)

    # Quiet down noisy third-party libraries.
    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, _level_int))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger for *name* (pass ``__name__``).

    The ``name`` is bound as the ``logger`` field on every record emitted
    through the returned logger.

    Safe to call at module-import time, before ``configure_logging()`` runs.
    """
    return structlog.get_logger(name).bind(logger=name)