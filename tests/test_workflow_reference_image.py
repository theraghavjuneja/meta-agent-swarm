"""Problem 2, end to end through Temporal: the brief's reference image URL
reaches the hero-image activity, on the main path and on a hero retry.

Activities are replaced with in-memory mocks (same names), so this needs no
database or providers -- only Temporal's local test server, which the SDK
downloads on first use. Set TEMPORAL_TEST_SERVER_PATH to a pre-downloaded
`temporal-test-server` binary where that download is blocked. Skipped if no
server can be started.
"""
import asyncio
import os
import uuid

import pytest
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from app.assets.dto import (
    ComposeAdInput,
    ComposeAdOutput,
    GenerateHeroImageInput,
    GenerateHeroImageOutput,
    RenderVideoInput,
    RenderVideoOutput,
    UsageSummary,
)
from app.campaigns.dto import (
    CampaignIdInput,
    RecordStageEventInput,
    RecordUsageInput,
    SetCampaignStatusInput,
)
from app.creative.dto import GenerateSpecInput, GenerateSpecOutput
from app.creative.dto import UsageSummary as SpecUsage
from app.research.dto import (
    ResearchOutcome,
    ResearchRequest,
    ResearchStatus,
    ResearchSummary,
    StopReason,
)
from app.workflows import (
    CampaignWorkflow,
    CampaignWorkflowInput,
    RetryAssetWorkflow,
    RetryAssetWorkflowInput,
)

REFERENCE_URL = "http://localhost:8000/assets/reference-images/packshot.png"
_USAGE = UsageSummary(provider="mock", operation="op", units=1, unit_type="x", estimated_cost_usd=0.0)


def _mock_activities(seen_hero_inputs: list[GenerateHeroImageInput]):
    @activity.defn(name="run_research_agent")
    async def run_research_agent(req: ResearchRequest) -> ResearchOutcome:
        return ResearchOutcome(
            status=ResearchStatus.COMPLETED,
            research_run_id=uuid.uuid4(),
            campaign_id=req.campaign_id,
            summary=ResearchSummary(stop_reason=StopReason.MODEL_FINISHED, model_used="mock"),
        )

    @activity.defn(name="generate_creative_spec")
    async def generate_creative_spec(inp: GenerateSpecInput) -> GenerateSpecOutput:
        return GenerateSpecOutput(id=uuid.uuid4(), version=1, is_valid=True, usage=SpecUsage(provider="mock"))

    @activity.defn(name="generate_hero_image")
    async def generate_hero_image(inp: GenerateHeroImageInput) -> GenerateHeroImageOutput:
        seen_hero_inputs.append(inp)
        return GenerateHeroImageOutput(asset_id=uuid.uuid4(), storage_url="x", width=1, height=1, usage=_USAGE)

    @activity.defn(name="compose_ad")
    async def compose_ad(inp: ComposeAdInput) -> ComposeAdOutput:
        return ComposeAdOutput(asset_id=uuid.uuid4(), storage_url="x", width=1, height=1, usage=_USAGE)

    @activity.defn(name="render_video")
    async def render_video(inp: RenderVideoInput) -> RenderVideoOutput:
        return RenderVideoOutput(
            asset_id=uuid.uuid4(), storage_url="x", width=1, height=1, duration_seconds=8, usage=_USAGE
        )

    @activity.defn(name="campaigns.set_campaign_status")
    async def set_campaign_status(inp: SetCampaignStatusInput) -> None:
        return None

    @activity.defn(name="campaigns.record_stage_event")
    async def record_stage_event(inp: RecordStageEventInput) -> None:
        return None

    @activity.defn(name="campaigns.record_provider_usage")
    async def record_provider_usage(inp: RecordUsageInput) -> None:
        return None

    @activity.defn(name="campaigns.recompute_campaign_status")
    async def recompute_campaign_status(inp: CampaignIdInput) -> None:
        return None

    return [
        run_research_agent, generate_creative_spec, generate_hero_image, compose_ad,
        render_video, set_campaign_status, record_stage_event, record_provider_usage,
        recompute_campaign_status,
    ]


async def _run(seen: list[GenerateHeroImageInput]) -> None:
    from temporalio.testing import WorkflowEnvironment

    try:
        env = await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter,
            test_server_existing_path=os.environ.get("TEMPORAL_TEST_SERVER_PATH"),
        )
    except Exception as exc:  # noqa: BLE001 - download/launch failure
        pytest.skip(f"Temporal test server unavailable: {exc}")

    async with env:
        async with Worker(
            env.client,
            task_queue="test",
            workflows=[CampaignWorkflow, RetryAssetWorkflow],
            activities=_mock_activities(seen),
        ):
            campaign_id = uuid.uuid4()
            handle = await env.client.start_workflow(
                CampaignWorkflow.run,
                CampaignWorkflowInput(
                    campaign_id=campaign_id, product_name="Beast Whey", product_description="d",
                    target_audience="a", objective="o", tone="t", cta="Explore the range",
                    reference_image_url=REFERENCE_URL,
                ),
                id=f"campaign-{campaign_id}",
                task_queue="test",
            )
            await asyncio.sleep(0.5)
            await handle.signal(CampaignWorkflow.select_angle, str(uuid.uuid4()))
            await handle.result()

            await env.client.execute_workflow(
                RetryAssetWorkflow.run,
                RetryAssetWorkflowInput(
                    campaign_id=campaign_id, asset_id=uuid.uuid4(), asset_type="hero_image",
                    creative_spec_id=uuid.uuid4(), reference_image_url=REFERENCE_URL,
                ),
                id=f"retry-{uuid.uuid4()}",
                task_queue="test",
            )


def test_reference_image_url_reaches_hero_activity():
    seen: list[GenerateHeroImageInput] = []
    asyncio.run(asyncio.wait_for(_run(seen), timeout=60))
    assert [i.reference_image_url for i in seen] == [REFERENCE_URL, REFERENCE_URL]
