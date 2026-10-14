"""Temporal client wiring for the API process.

One cached ``Client`` for the process lifetime, opened by the FastAPI
lifespan and closed on shutdown. ``settings.temporal_host`` /
``settings.temporal_namespace`` are read once here, never re-parsed per
request.

This module is also the single place that knows the workflow-id
conventions::

    CampaignWorkflow    -> campaign-{campaign_id}
    RetryAssetWorkflow  -> retry-asset-{asset_id}-{attempt}

Routers must never string-format those themselves; they call
``campaign_workflow_id()`` / ``retry_asset_workflow_id()`` or, better, the
``start_*`` / ``signal_*`` helpers below.

Every Temporal SDK exception is wrapped before it leaves this module, so a
router never sees ``temporalio.*`` types. Connection and RPC transport
failures become ``InfrastructureError`` (-> 502). The two cases that are
genuinely *state* problems rather than infrastructure problems - starting a
workflow that already exists, and signalling a workflow that is not running -
become ``DomainError`` (-> 409), because "you already did this" and "there is
nothing to signal" are things the caller can understand and act on, not
transient backend faults.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from temporalio.client import Client, WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError, RPCStatusCode

from app.common.exceptions import DomainError, InfrastructureError
from app.common.logging import get_logger
from app.config import get_settings
from app.workflows import (
    CampaignWorkflow,
    CampaignWorkflowInput,
    RetryAssetWorkflow,
    RetryAssetWorkflowInput,
)

__all__ = [
    "campaign_workflow_id",
    "close_temporal_client",
    "get_temporal_client",
    "retry_asset_workflow_id",
    "signal_select_angle",
    "start_campaign_workflow",
    "start_retry_asset_workflow",
]

logger = get_logger(__name__)

_client: Client | None = None
_client_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Workflow id convention - defined once, here
# ---------------------------------------------------------------------------


def campaign_workflow_id(campaign_id: UUID | str) -> str:
    """The one definition of the CampaignWorkflow id convention."""
    return f"campaign-{campaign_id}"


def retry_asset_workflow_id(asset_id: UUID | str, attempt: int) -> str:
    """The one definition of the RetryAssetWorkflow id convention.

    ``attempt`` is included so a second manual retry of the same asset is a
    distinct workflow rather than colliding with the first one's id. The
    caller passes the asset row's current ``attempt_count``.
    """
    return f"retry-asset-{asset_id}-{attempt}"


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


async def get_temporal_client() -> Client:
    """Return the process-wide client, connecting on first use.

    The lock makes the first-use connect safe under concurrent requests: two
    simultaneous POSTs at cold start open one connection, not two.
    """
    global _client

    if _client is not None:
        return _client

    async with _client_lock:
        if _client is not None:
            return _client

        settings = get_settings()
        try:
            _client = await Client.connect(
                settings.temporal_host,
                namespace=settings.temporal_namespace,
                # Must match app.worker exactly. The domain DTOs crossing the
                # workflow boundary are pydantic models carrying UUID and
                # Decimal fields, which the SDK's default JSON converter does
                # not round-trip.
                data_converter=pydantic_data_converter,
            )
        except Exception as exc:
            raise InfrastructureError.wrap(
                exc,
                provider="temporal",
                operation="connect",
                message=f"Could not connect to Temporal at {settings.temporal_host}",
            ) from exc

        logger.info(
            "temporal_client_connected",
            host=settings.temporal_host,
            namespace=settings.temporal_namespace,
        )
        return _client


async def close_temporal_client() -> None:
    """Close the cached client, if one was ever opened.

    The Python SDK does not expose a stable public ``close()`` on ``Client``
    across versions, so this reaches for the underlying service client's
    close if it exists and otherwise just drops the reference and lets the
    connection be reclaimed.
    """
    global _client

    if _client is None:
        return

    service_client = getattr(_client, "service_client", None)
    close = getattr(service_client, "close", None)
    if callable(close):
        try:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.warning("temporal_client_close_failed", exc_info=True)

    _client = None
    logger.info("temporal_client_closed")


# ---------------------------------------------------------------------------
# Start / signal helpers
# ---------------------------------------------------------------------------


async def start_campaign_workflow(
    campaign_id: UUID, research_input: CampaignWorkflowInput
) -> tuple[str, str]:
    """Start ``CampaignWorkflow`` for a campaign. Returns (workflow_id, run_id)."""
    client = await get_temporal_client()
    settings = get_settings()
    workflow_id = campaign_workflow_id(campaign_id)

    try:
        handle = await client.start_workflow(
            CampaignWorkflow.run,
            research_input,
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
    except Exception as exc:
        if _is_already_started(exc):
            raise DomainError(
                f"A workflow is already running for campaign {campaign_id}",
                code="workflow_already_started",
            ) from exc
        raise InfrastructureError.wrap(
            exc,
            provider="temporal",
            operation="start_campaign_workflow",
        ) from exc

    logger.info(
        "campaign_workflow_started",
        campaign_id=str(campaign_id),
        workflow_id=workflow_id,
        run_id=handle.result_run_id,
    )
    return workflow_id, handle.result_run_id or ""


async def signal_select_angle(campaign_id: UUID, angle_id: UUID) -> None:
    """Signal the running ``CampaignWorkflow`` with the user's angle choice.

    Called *after* the database write, never before - see the router. A
    failure here means the DB and the workflow have diverged, which is
    exactly why this raises loudly rather than logging and returning.
    """
    client = await get_temporal_client()
    workflow_id = campaign_workflow_id(campaign_id)

    try:
        handle = client.get_workflow_handle(workflow_id)
        # The signal is declared as ``select_angle(self, angle_id: str)``, so
        # the UUID is stringified here rather than at the call site.
        await handle.signal(CampaignWorkflow.select_angle, str(angle_id))
    except RPCError as exc:
        if exc.status is RPCStatusCode.NOT_FOUND:
            raise DomainError(
                f"No running workflow for campaign {campaign_id}; "
                "it may have already completed or failed",
                code="workflow_not_running",
            ) from exc
        raise InfrastructureError.wrap(
            exc, provider="temporal", operation="signal_select_angle"
        ) from exc
    except WorkflowFailureError as exc:
        raise InfrastructureError.wrap(
            exc, provider="temporal", operation="signal_select_angle"
        ) from exc
    except Exception as exc:
        raise InfrastructureError.wrap(
            exc, provider="temporal", operation="signal_select_angle"
        ) from exc

    logger.info(
        "select_angle_signalled",
        campaign_id=str(campaign_id),
        angle_id=str(angle_id),
        workflow_id=workflow_id,
    )


async def start_retry_asset_workflow(
    retry_input: RetryAssetWorkflowInput, *, attempt: int
) -> tuple[str, str]:
    """Start ``RetryAssetWorkflow`` for one asset. Returns (workflow_id, run_id).

    Takes the full ``RetryAssetWorkflowInput`` rather than the
    ``(campaign_id, asset_id_or_type)`` pair sketched in the module brief,
    because the workflow genuinely requires ``creative_spec_id`` (and
    ``hero_asset_id`` for the compose/video stages) and neither can be
    derived here without a database read. Resolving those is the router's
    job; this function stays purely about Temporal.
    """
    client = await get_temporal_client()
    settings = get_settings()
    workflow_id = retry_asset_workflow_id(retry_input.asset_id, attempt)

    try:
        handle = await client.start_workflow(
            RetryAssetWorkflow.run,
            retry_input,
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
    except Exception as exc:
        if _is_already_started(exc):
            raise DomainError(
                f"A retry is already in flight for asset {retry_input.asset_id}",
                code="retry_already_started",
            ) from exc
        raise InfrastructureError.wrap(
            exc,
            provider="temporal",
            operation="start_retry_asset_workflow",
        ) from exc

    logger.info(
        "retry_asset_workflow_started",
        campaign_id=str(retry_input.campaign_id),
        asset_id=str(retry_input.asset_id),
        asset_type=retry_input.asset_type,
        workflow_id=workflow_id,
        run_id=handle.result_run_id,
    )
    return workflow_id, handle.result_run_id or ""


def _is_already_started(exc: BaseException) -> bool:
    """Detect "workflow already started" across SDK versions.

    The SDK has moved this exception between modules over time, so matching
    on the class name plus the gRPC status is more durable than importing a
    specific symbol.
    """
    if type(exc).__name__ == "WorkflowAlreadyStartedError":
        return True
    return isinstance(exc, RPCError) and exc.status is RPCStatusCode.ALREADY_EXISTS