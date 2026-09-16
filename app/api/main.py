"""FastAPI application wiring.

This is where ``app.common``'s deliberately FastAPI-free exception hierarchy
finally meets FastAPI, and the mapping is the whole point of the file:

    ValidationError       -> 422  (bad input, nothing happened)
    DomainError           -> 409  (valid input, wrong state) unless the
                                   instance overrides it, which is how 404s
                                   are expressed without a second mechanism
    InfrastructureError   -> 502  (a dependency failed; not the caller's fault)

Each type's own ``default_http_status`` is the source of that mapping rather
than a lookup table here, so the codes cannot drift apart from the
hierarchy.

Run with::

    uvicorn app.api.main:app --reload

and, in a second process, ``python -m app.worker``. The API never executes
activities - it starts workflows, signals them, and reads the database.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routers import campaigns_router
from app.api.temporal_client import close_temporal_client, get_temporal_client
from app.common.exceptions import (
    AppError,
    DomainError,
    InfrastructureError,
    ValidationError,
)
from app.common.logging import configure_logging, get_logger
from app.config import get_settings

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging, open the Temporal connection, close it on shutdown.

    Connecting here rather than lazily on first request means a misconfigured
    ``TEMPORAL_HOST`` fails at boot, where it is obvious, instead of turning
    the first POST into a confusing 502. The failure is logged and
    re-raised - starting an API that cannot start workflows would only
    accept requests it must then reject.
    """
    settings = get_settings()
    # Settings exposes no log_format/log_level field today, so this reads
    # defensively. See the module notes: configure_logging's own parameter is
    # `level`, and the JSON renderer is unconditional.
    configure_logging(level=getattr(settings, "log_level", "INFO"))

    logger.info(
        "api_starting",
        environment=settings.environment,
        provider_mode=settings.provider_mode,
        temporal_host=settings.temporal_host,
        task_queue=settings.temporal_task_queue,
    )

    try:
        await get_temporal_client()
    except InfrastructureError:
        logger.error("api_startup_temporal_unavailable", exc_info=True)
        raise

    try:
        yield
    finally:
        await close_temporal_client()
        logger.info("api_stopped")


app = FastAPI(
    title="Campaign Studio API",
    version="0.1.0",
    summary=(
        "Research creative angles for a product, turn one into a campaign "
        "concept, and produce two coordinated image ads and a short video."
    ),
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

# Deliberately permissive: the frontend is a later, out-of-scope module and
# there is no auth in this project, so there are no credentials to protect
# here. `allow_credentials` stays False so this stays a wildcard rather than
# quietly becoming a cross-origin credential leak if auth is added later.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Correlation id binding
# ---------------------------------------------------------------------------


@app.middleware("http")
async def bind_log_context(request: Request, call_next):
    """Bind ``request_id``, and ``campaign_id`` when the path carries one.

    Every log line emitted while handling the request - including from a
    repository or an adapter several layers down - then carries the campaign
    it belongs to, which is what makes a single campaign's story greppable
    across the API and worker logs.

    Two caveats worth knowing:

    * ``app.common.context`` was emptied out, so this binds through
      ``structlog.contextvars`` directly instead.
    * ``configure_logging()``'s processor chain does not currently include
      ``structlog.contextvars.merge_contextvars``, so these bound values are
      stored but not yet rendered. Adding that one processor as the first
      entry in the chain turns this on; it lives in ``app/common``, which is
      outside this module's scope.
    """
    structlog.contextvars.clear_contextvars()

    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    bindings: dict[str, str] = {"request_id": request_id}

    campaign_id = request.path_params.get("campaign_id") if request.path_params else None
    if campaign_id is None:
        campaign_id = _campaign_id_from_path(request.url.path)
    if campaign_id:
        bindings["campaign_id"] = str(campaign_id)

    structlog.contextvars.bind_contextvars(**bindings)
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()

    response.headers["x-request-id"] = request_id
    return response


def _campaign_id_from_path(path: str) -> str | None:
    """Pull the campaign id out of the raw path.

    Middleware runs before routing, so ``request.path_params`` is usually
    empty at this point; the segment after ``/campaigns`` is parsed instead.
    It is only used as a log field, and it is validated as a UUID so a
    garbage path segment cannot inject arbitrary text into the log stream.
    """
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2 or parts[0] != "campaigns":
        return None
    try:
        return str(uuid.UUID(parts[1]))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


def _error_response(exc: AppError, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_jsonable(exc.to_dict()))


def _jsonable(payload: dict) -> dict:
    """``to_dict`` returns plain types already; this is a cheap guard."""
    return {k: v for k, v in payload.items() if v is not None or k == "fields"}


@app.exception_handler(ValidationError)
async def handle_validation_error(
    request: Request, exc: ValidationError
) -> JSONResponse:
    """422. The caller sent something we could not accept; nothing was written."""
    logger.info("request_validation_failed", code=exc.code, fields=exc.fields)
    return _error_response(exc, exc.default_http_status)


@app.exception_handler(DomainError)
async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    """409 by default; 404 when the instance sets ``http_status``.

    Reading the status off the instance is what lets "campaign not found"
    and "angle already selected" share one exception type and one handler
    while still producing the right code - rather than routers raising
    ``HTTPException`` for one and ``DomainError`` for the other.
    """
    logger.info("domain_error", code=exc.code, message=exc.message)
    return _error_response(exc, exc.default_http_status)


@app.exception_handler(InfrastructureError)
async def handle_infrastructure_error(
    request: Request, exc: InfrastructureError
) -> JSONResponse:
    """502. A dependency (Temporal, the database, storage, a provider) failed.

    Logged with the full provider/operation/cause context the exception
    carries, but the response body stays the plain ``code``/``message``
    envelope - the cause's text can contain DSNs, provider request bodies and
    other things that should not leave the process.
    """
    logger.error("infrastructure_error", **exc.to_log_context(), exc_info=True)
    return JSONResponse(
        status_code=exc.default_http_status,
        content={
            "code": exc.code,
            "message": "An upstream dependency failed while handling this request.",
        },
    )


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """FastAPI's own parse/validation failures, folded into the same envelope.

    Path and query validation (a malformed UUID, ``limit=0``) is caught by
    FastAPI before a handler runs, so without this a client would see two
    different 422 shapes depending on which layer rejected the request.
    ``ValidationError.from_field_errors`` exists for exactly this translation.
    """
    translated = ValidationError.from_field_errors(list(exc.errors()))
    logger.info("request_schema_invalid", fields=translated.fields)
    return _error_response(translated, translated.default_http_status)


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """404s from unmatched routes and anything else Starlette raises itself."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": f"http_{exc.status_code}", "message": str(exc.detail)},
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """The backstop: an unhandled exception is a 500 with no internals leaked.

    A stack trace reaching a client is the failure mode the layered
    exception hierarchy exists to prevent, so this catches whatever slipped
    through, logs it in full, and returns a generic body.
    """
    logger.error("unhandled_exception", path=request.url.path, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"code": "internal_error", "message": "An unexpected error occurred."},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(campaigns_router)


@app.get("/health", tags=["ops"], summary="Liveness probe")
async def health() -> dict[str, str]:
    """Liveness only - deliberately does not touch Temporal or the database.

    A readiness check that fans out to dependencies belongs on a separate
    endpoint; conflating them makes a transient database blip look like a
    dead process to an orchestrator.
    """
    return {"status": "ok"}