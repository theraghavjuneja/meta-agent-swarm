

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
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

# Mount local storage directory as static files if using local storage
_settings = get_settings()
if _settings.storage_backend == "local":
    import os
    os.makedirs(_settings.local_storage_path, exist_ok=True)
    app.mount(
        "/assets", 
        StaticFiles(directory=_settings.local_storage_path), 
        name="assets"
    )

# Always mount fixtures so older fixture campaigns can still be viewed
# even if the server is currently running in 'real' mode.
import os
from app.assets.adapters.storage_fixture import _FIXTURE_STORAGE_DIR
os.makedirs(_FIXTURE_STORAGE_DIR, exist_ok=True)
app.mount(
    "/fixtures",
    StaticFiles(directory=str(_FIXTURE_STORAGE_DIR)),
    name="fixtures"
)


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
    
    logger.info("domain_error", code=exc.code, message=exc.message)
    return _error_response(exc, exc.default_http_status)


@app.exception_handler(InfrastructureError)
async def handle_infrastructure_error(
    request: Request, exc: InfrastructureError
) -> JSONResponse:
    
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

    return {"status": "ok"}