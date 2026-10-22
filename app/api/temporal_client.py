
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



def campaign_workflow_id(campaign_id: UUID | str) -> str:
    """The one definition of the CampaignWorkflow id convention."""
    return f"campaign-{campaign_id}"


def retry_asset_workflow_id(asset_id: UUID | str, attempt: int) -> str:
    
    return f"retry-asset-{asset_id}-{attempt}"




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
    
    if type(exc).__name__ == "WorkflowAlreadyStartedError":
        return True
    return isinstance(exc, RPCError) and exc.status is RPCStatusCode.ALREADY_EXISTS