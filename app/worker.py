"""Temporal worker entrypoint: ``python -m app.worker``.

This is the process that actually does the work. ``app.api`` starts and
signals workflows and reads the database; every activity - research, spec
generation, image generation, compositing, video rendering, and the
campaigns bookkeeping activities - executes here.

That split is why this file calls ``configure_logging()`` of its own: it is
a separate process from the API and inherits none of its setup.

Running two of these is fine and is the intended way to scale: Temporal
distributes tasks across every worker polling the same task queue. Killing
one mid-pipeline and starting another resumes the campaign rather than
restarting it - the workflow's history lives in Temporal, the asset rows are
updated in place, and each activity is idempotent on its own key. Nothing in
this file makes that true; it just does not get in the way.
"""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

from app.assets.activities import compose_ad, generate_hero_image, render_video
from app.campaigns.activities import (
    recompute_campaign_status,
    record_provider_usage,
    record_stage_event,
    set_campaign_status,
    set_workflow_ids,
)
from app.common.exceptions import InfrastructureError
from app.common.logging import configure_logging, get_logger
from app.config import get_settings
from app.creative.activities import generate_creative_spec
from app.research.activities import run_research_agent
from app.workflows import CampaignWorkflow, RetryAssetWorkflow

logger = get_logger(__name__)

# Every activity in the project, in one list, so a newly added activity that
# nobody registered shows up as an obviously missing entry here rather than
# as a workflow task that times out with no explanation.
ACTIVITIES = [
    # app.research
    run_research_agent,
    # app.creative
    generate_creative_spec,
    # app.assets
    generate_hero_image,
    compose_ad,
    render_video,
    # app.campaigns
    set_campaign_status,
    recompute_campaign_status,
    record_stage_event,
    record_provider_usage,
    set_workflow_ids,
]

WORKFLOWS = [CampaignWorkflow, RetryAssetWorkflow]

# The workflow sandbox reimports modules to enforce determinism. Every
# workflow-reachable module in this project transitively pulls in SQLAlchemy,
# pydantic and the provider SDKs, which are both expensive to reimport and
# full of module-level state the sandbox will flag. The workflow files
# already import their dependencies inside
# ``workflow.unsafe.imports_passed_through()``; passing the same modules
# through at the runner level covers anything reached indirectly.
_PASSTHROUGH_MODULES = (
    "app",
    "pydantic",
    "pydantic_settings",
    "sqlalchemy",
    "structlog",
)


def _build_workflow_runner() -> SandboxedWorkflowRunner:
    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules(
            *_PASSTHROUGH_MODULES
        )
    )


async def _connect(settings) -> Client:
    try:
        return await Client.connect(
            settings.temporal_host,
            namespace=settings.temporal_namespace,
            # Must match app.api.temporal_client exactly. Activity inputs and
            # outputs are pydantic models and dataclasses carrying UUID and
            # Decimal fields; the default JSON converter does not round-trip
            # them, and a mismatch between the two processes shows up as
            # deserialization errors inside activities rather than at connect
            # time.
            data_converter=pydantic_data_converter,
        )
    except Exception as exc:
        raise InfrastructureError.wrap(
            exc,
            provider="temporal",
            operation="worker_connect",
            message=f"Worker could not connect to Temporal at {settings.temporal_host}",
        ) from exc


async def main() -> None:
    settings = get_settings()
    configure_logging(level=getattr(settings, "log_level", "INFO"))

    logger.info(
        "worker_starting",
        environment=settings.environment,
        provider_mode=settings.provider_mode,
        temporal_host=settings.temporal_host,
        namespace=settings.temporal_namespace,
        task_queue=settings.temporal_task_queue,
        workflows=[w.__name__ for w in WORKFLOWS],
        activity_count=len(ACTIVITIES),
    )

    client = await _connect(settings)

    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=WORKFLOWS,
        activities=ACTIVITIES,
        workflow_runner=_build_workflow_runner(),
        # No activity_executor: every activity in this project is `async def`,
        # so they run on the worker's event loop. A thread pool would only be
        # needed for blocking activities, and adding one pre-emptively would
        # hide the fact that a future blocking activity is blocking.
    )

    logger.info("worker_started", task_queue=settings.temporal_task_queue)
    try:
        await worker.run()
    finally:
        logger.info("worker_stopped")


def run() -> None:
    """Console entrypoint.

    ``KeyboardInterrupt`` is swallowed on purpose: Ctrl-C is a normal way to
    stop a worker, and Temporal's shutdown is already graceful, so a
    traceback on every local stop would be noise.
    """
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("worker_interrupted")


if __name__ == "__main__":
    run()