"""Named Temporal RetryPolicy configurations for app/workflows.

Centralising policies here achieves two things:
  1. DRY — the same policy is referenced in both CampaignWorkflow and
     RetryAssetWorkflow without copy-pasting raw RetryPolicy() calls.
  2. Readability — the names communicate *why* each policy is sized the
     way it is, making it easy to reason about them together.

Three policy families:

RESEARCH
    Generous but bounded.  Research is the most expensive stage to redo
    (LLM + web fetches). maximum_attempts=2 gives one retry before the
    workflow declares research permanently failed and aborts.  The
    activity's internal start_to_close_timeout is derived from
    ``settings.research_timeout_seconds`` at workflow startup.

SPEC
    Small, infrastructure-failure-only.  The activity already performs
    up to MAX_CORRECTIVE_ATTEMPTS=3 LLM calls internally for schema-
    validation failures. This policy only covers infra failures (provider
    down, DB connectivity) — a DomainError (corrective retries exhausted)
    is already non-retryable at the Temporal level and should not be
    retried by the workflow.

IMAGE / COMPOSE / VIDEO
    Provider-sized.  Each covers one class of provider SLA:
    - IMAGE: external image-generation provider (OpenAI), slow.
    - COMPOSE: local Pillow compositing, fast; policy is a safety net.
    - VIDEO: FFmpeg render, longest wall-clock time.

CAMPAIGN_ACTIVITY
    Lightweight DB operations (set_campaign_status, record_stage_event,
    record_provider_usage, recompute_campaign_status). Short window, more
    retries — these should almost always succeed within seconds.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio.common import RetryPolicy

# ---------------------------------------------------------------------------
# Research — generous, bounded
# ---------------------------------------------------------------------------

RESEARCH_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    maximum_interval=timedelta(seconds=60),
    backoff_coefficient=2.0,
    # 2 total attempts = 1 original + 1 retry.  Research is expensive; a
    # second attempt is warranted for transient infra failures, but a third
    # would triple the wall-clock cost with diminishing returns.
    maximum_attempts=2,
)


def research_start_to_close(research_timeout_seconds: int) -> timedelta:
    """Return the start_to_close_timeout for the research activity.

    Adds a 120-second buffer on top of the loop's own internal budget to
    cover DB writes, adapter initialisation, and Temporal overhead.

    Call once in ``@workflow.run`` and store the result in a local variable
    so the value is stable across Temporal's deterministic replay (safe
    because settings are constant for the lifetime of the worker process).
    """
    return timedelta(seconds=research_timeout_seconds + 120)


# ---------------------------------------------------------------------------
# Spec generation — small, infra-failure-only
# ---------------------------------------------------------------------------

SPEC_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    # 2 total attempts = 1 original + 1 retry.  Corrective (schema-
    # validation) retries are handled inside the activity (Module 5).
    # The workflow only retries on infrastructure-level failures.
    maximum_attempts=2,
)
# 3 × LLM calls (MAX_CORRECTIVE_ATTEMPTS) + DB write + overhead
SPEC_START_TO_CLOSE: timedelta = timedelta(seconds=180)

# ---------------------------------------------------------------------------
# Image generation — provider-sized (OpenAI)
# ---------------------------------------------------------------------------

IMAGE_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=15),
    maximum_interval=timedelta(seconds=90),
    backoff_coefficient=2.0,
    # 3 total attempts = 1 original + 2 retries.  Image generation is
    # moderately expensive; two retries balance reliability against cost.
    maximum_attempts=3,
)
IMAGE_START_TO_CLOSE: timedelta = timedelta(minutes=6)

# ---------------------------------------------------------------------------
# Ad compositing — fast Pillow operations (local CPU)
# ---------------------------------------------------------------------------

COMPOSE_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    # 3 total attempts = 1 original + 2 retries.  Compositing is CPU-local
    # and cheap; retries here are purely a safety net for transient DB or
    # storage failures, not for the compositing step itself.
    maximum_attempts=3,
)
COMPOSE_START_TO_CLOSE: timedelta = timedelta(minutes=3)

# ---------------------------------------------------------------------------
# Video rendering — FFmpeg pipeline (longest wall-clock time)
# ---------------------------------------------------------------------------

VIDEO_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=15),
    maximum_interval=timedelta(seconds=90),
    backoff_coefficient=2.0,
    # 3 total attempts = 1 original + 2 retries.  Same philosophy as IMAGE
    # but with a larger start_to_close_timeout to accommodate render time.
    maximum_attempts=3,
)
VIDEO_START_TO_CLOSE: timedelta = timedelta(minutes=12)

# ---------------------------------------------------------------------------
# Campaign housekeeping — lightweight DB operations
# ---------------------------------------------------------------------------

CAMPAIGN_ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=15),
    backoff_coefficient=2.0,
    # 5 total attempts.  DB operations should rarely fail; if they do for
    # 5 consecutive attempts the database is likely seriously unhealthy.
    maximum_attempts=5,
)
CAMPAIGN_ACTIVITY_START_TO_CLOSE: timedelta = timedelta(seconds=30)
