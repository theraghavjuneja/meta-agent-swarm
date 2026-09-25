# Campaign Studio — Backend Architecture

Research an ad campaign on the open web, turn one AI-selected creative angle into a
shared creative specification, and generate two coordinated image ads and a short
video from it — orchestrated end-to-end by Temporal, with every stage persisted so
the pipeline survives a restart mid-flight.

This document describes **what exists in the codebase today**: every module, every
database table, every Temporal workflow/activity, and how they connect. Where the
implementation falls short of what it's meant to do, that's called out explicitly
rather than glossed over.

---

## 1. High-level shape

Two long-running processes share one codebase and one Postgres database:

| Process | Entry point | Responsibility |
|---|---|---|
| **API** | `uvicorn app.api.main:app` | FastAPI. Validates input, reads the database, **starts and signals** Temporal workflows. Never executes an activity itself. |
| **Worker** | `python -m app.worker` | Polls the `campaign-studio` Temporal task queue. **Every activity** (research, spec generation, image generation, compositing, video rendering, all DB bookkeeping) executes here. |

They talk to each other **only through Temporal** — the API never calls worker code
directly, and the worker never serves HTTP. Both connect to the same Postgres
database directly for reads/writes (Temporal itself doesn't proxy DB access; it just
orchestrates *when* each activity runs and retries it on failure).

```
                    ┌─────────────┐
   HTTP requests →  │   API (FastAPI)  │──start/signal──►  Temporal Server
                    └─────────────┘                              │
                          │                                       │ dispatches
                          │ reads                                 ▼
                          ▼                              ┌─────────────────┐
                    ┌───────────┐                        │  Worker process  │
                    │ Postgres  │◄───────reads/writes─────┤ (executes every  │
                    │(app data) │                        │    activity)     │
                    └───────────┘                        └─────────────────┘
                                                                   │
                                                        calls external providers:
                                                        OpenAI (LLM + image gen),
                                                        Tavily (web search),
                                                        arbitrary URLs (page reads),
                                                        ffmpeg (local subprocess),
                                                        S3 or local disk (storage)
```

Temporal itself runs as a separate stack (its own server + its own Postgres +
Elasticsearch + Web UI), brought up via `docker/temporal/docker-compose-temporal.yml`
and merged into the main compose file via `include:`. It is **not** the app's data
store — it stores *workflow execution history* (for replay/recovery), not campaign
data. All campaign, research, spec and asset data lives in the app's own Postgres.

---

## 2. Package layout

```
app/
├── api/            FastAPI app, routers, Pydantic response schemas, Temporal client wiring
├── campaigns/       Campaign lifecycle: brief, status, stage events, usage ledger
├── research/         The bounded ReAct research agent + its persistence
├── creative/          LLM → structured creative-spec generation
├── assets/            Image generation, deterministic compositing, ffmpeg video, storage adapters
├── workflows/          Temporal workflow definitions + retry policies (the orchestration layer)
├── common/             Cross-cutting: exception hierarchy, logging, retry helpers, request context
├── config/             Pydantic Settings (env-driven configuration)
├── db/                 Async SQLAlchemy engine/session plumbing, declarative base + mixins
└── worker.py           Temporal worker entrypoint — registers every workflow & activity
```

Each domain package (`campaigns`, `research`, `creative`, `assets`) follows the same
internal shape:

```
<package>/
├── models.py        SQLAlchemy ORM tables
├── dto.py            Pydantic DTOs — activity inputs/outputs, and (for research/creative)
│                      the exact JSON schema requested from the LLM
├── repository.py      All raw persistence (SQL), nothing else
├── service.py          Business rules, built on top of repository.py
├── activities.py        @activity.defn wrappers — the only things Temporal calls directly
└── adapters/            (research, assets only) — pluggable external-provider implementations
```

This is a deliberate layering: **workflows never touch the database or an SDK
directly** — everything crosses that boundary through an `@activity.defn` function,
so Temporal can safely retry it and replay the workflow deterministically.

---

## 3. Database schema

All tables use `Base` (`app/db/base.py`) with a shared naming convention for
constraints/indexes, and one of three mixins:

- `IdMixin` — UUID primary key, generated client-side (`uuid.uuid4()`), not
  `gen_random_uuid()` — avoids a hard dependency on Postgres' `pgcrypto` extension.
- `TimestampMixin` — `created_at` + `updated_at` (auto-bumped `onupdate`), for rows
  that are updated in place.
- Append-only tables skip `TimestampMixin` and declare `created_at` (or a
  domain-specific name like `occurred_at`/`accessed_at`) directly — there's no
  `updated_at` because the row is never touched again after insert.

### 3.1 `campaigns` package

| Table | Mutability | Purpose |
|---|---|---|
| **`campaigns`** | updated in place | One row per campaign: the brief, current `status`, and the Temporal workflow/run id that owns it. |
| **`stage_events`** | append-only | One row per `(stage, started\|completed\|failed\|retried)` transition. This is what reconstructs "what happened and when" for the UI/history view without replaying Temporal history. |
| **`provider_usage`** | append-only | One row per billable provider call (LLM tokens, image gen, video render), with `estimated_cost_usd` and an `is_estimated` flag. |

`campaigns` columns: `product_name`, `product_description`, `target_audience`,
`objective`, `tone`, `cta`, `reference_image_url` (nullable), `verified_claims`
(JSONB list), `status` (enum, see below), `temporal_workflow_id` (unique),
`temporal_run_id`.

```python
class CampaignStatus(str, enum.Enum):
    DRAFT, RESEARCHING, AWAITING_ANGLE_SELECTION,
    GENERATING_SPEC, GENERATING_ASSETS,
    COMPLETED, COMPLETED_WITH_ERRORS, FAILED
```

`status` is **never set optimistically by a "happy path" step**. Intermediate
statuses (`researching` → `awaiting_angle_selection` → `generating_spec` →
`generating_assets`) are set directly by the workflow as it enters each stage. The
two *terminal* statuses (`completed` / `completed_with_errors`) are only ever
produced by `recompute_campaign_status`, which derives the true state from the
`assets` table itself — the workflow is never allowed to just declare "done".

`StageName` enum: `research`, `angle_selection`, `spec_generation`, `hero_image`,
`compose_1x1`, `compose_9x16`, `render_video`.

### 3.2 `research` package — 5 tables

| Table | Mutability | Purpose |
|---|---|---|
| **`research_runs`** | updated in place | One row per research attempt (re-running research inserts a *new* row, never overwrites). Tracks `status` (`running`/`completed`/`failed`), `tool_call_budget` vs. `tool_calls_used`, `model_used`, `estimated_cost_usd`, `mock_mode`. |
| **`research_steps`** | append-only | The literal, inspectable trace: one row per tool call (`search` / `read_page`) or model decision (`decide`), with `input`, `output` (both JSONB) and `decision_summary` (the model's one-sentence rationale). **Committed individually, in its own DB session, the instant it happens** — this is what makes the trace survive a mid-run crash and what makes polling `GET /campaigns/{id}/research` show live progress. |
| **`research_sources`** | append-only | One row per URL successfully read via `read_page`, unique per `(run, url)`: `title`, `accessed_at`, `excerpt`. |
| **`creative_angles`** | updated in place (`is_selected` flag) | Up to 3 candidate angles per run: `audience_insight`, `hook`, `visual_direction`, `rationale`. A partial unique index guarantees at most one `is_selected = true` row per run. |
| **`angle_sources`** | join table | Many-to-many between `creative_angles` and `research_sources` — an angle links only to sources it actually cites *and* that were actually read (never a hallucinated citation). |

`research_runs.campaign_id` and `creative_specs.campaign_id` / `assets.campaign_id`
are plain indexed UUID columns **without a foreign key** to `campaigns` — a
consequence of these modules having been built before the `campaigns` table existed
in the build sequence. This is a real gap worth closing with a migration (`ALTER
TABLE ... ADD CONSTRAINT ... REFERENCES campaigns(id) ON DELETE CASCADE`) if
referential integrity across the whole pipeline matters for your submission.

### 3.3 `creative` package — 1 table

**`creative_specs`** — append-only, one new row (`version` = N+1) per spec
generation for a campaign, never updated. Unique on `(campaign_id, version)`.

| Column | Type | Origin |
|---|---|---|
| `angle_id` | FK → `creative_angles.id` | The selected angle it was generated from |
| `hook` | text | LLM output |
| `approved_copy` | text | LLM output — long-form ad body copy |
| `cta` | text | LLM output |
| `product_identity` | JSONB `{name, key_visual_traits[]}` | LLM output |
| `scene_description` | text | LLM output — the scene part of the image prompt (wrapped by `app/assets/prompts.py`) |
| `palette` | JSONB `list[str]` (hex colors, validated) | LLM output |
| `composition_guidance` | text | LLM output |
| `video_outline` | JSONB `{beats: [{label, description}]}` | LLM output |
| `is_valid` | bool | Set once schema validation passes |

### 3.4 `assets` package — 2 tables

| Table | Mutability | Purpose |
|---|---|---|
| **`assets`** | updated in place | One row per `(campaign_id, asset_type)` — unique constraint enforces this, so `ensure_pending` is a genuine get-or-create. Moves `pending → generating → completed/failed`. |
| **`asset_generation_attempts`** | append-only | One row per attempt at generating a given asset, `succeeded`/`failed`, with `provider_request_id`/`error`. This is the audit trail behind "retry without rerunning successful stages." |

`AssetType` enum: `hero_image`, `ad_1x1`, `ad_9x16`, `video`.
`AssetStatus` enum: `pending`, `generating`, `completed`, `failed`.

`assets` columns of note: `storage_url`, `width`, `height`, `duration_seconds`,
`generation_prompt` (see §5 — meaning differs by asset type), `provider`,
`idempotency_key` (unique — see §7), `attempt_count`, `last_error`.

### 3.5 Entity-relationship summary

```
campaigns (1) ──< research_runs (1) ──< research_steps
    │                    │        ├──< research_sources ──< angle_sources >── creative_angles
    │                    └──────────────────────────────────────┘  (1 selected)
    │
    ├──< creative_specs (versioned, points at the selected angle)
    │         │
    │         └──< assets (one per asset_type, points at the spec it came from)
    │                   └──< asset_generation_attempts
    │
    ├──< stage_events
    └──< provider_usage
```

---

## 4. Temporal orchestration

### 4.1 `CampaignWorkflow` — the main pipeline

ID convention: `campaign-{campaign_id}` (defined once, in
`app/api/temporal_client.py::campaign_workflow_id`, never string-formatted
elsewhere). Started by `POST /campaigns`.

```
1. RESEARCH          run_research_agent            (up to 3 sourced angles)
                      ↓ status → researching
2. ANGLE SELECTION    workflow.wait_condition() blocks on a @workflow.signal
                       — nothing downstream can start without a human picking one
                      ↓ status → awaiting_angle_selection, then generating_spec
3. SPEC               generate_creative_spec        (validated CreativeSpecSchema)
                      ↓ status → generating_assets
4. HERO IMAGE         generate_hero_image           (the one real image-gen call;
                                                       everything else derives from it)
5. CONCURRENT         asyncio.gather(compose_ad(1:1), compose_ad(9:16), render_video,
                                      return_exceptions=True)
                       — per-task error isolation: one failing branch is recorded as
                         FAILED and does not cancel or fail its siblings
6. TERMINAL           recompute_campaign_status      (the ONLY path to a terminal status —
                                                        derived from the assets table, never
                                                        set optimistically by this workflow)
```

Structural guarantees, enforced by the code, not just documented:

- **No direct DB/HTTP/SDK calls anywhere in the workflow file.** Every side effect
  goes through `workflow.execute_activity(...)`, which is what lets Temporal replay
  the workflow deterministically after a worker restart.
- **Generation cannot begin without a human.** `select_angle` is a
  `@workflow.signal`; the main coroutine is genuinely blocked on
  `workflow.wait_condition(lambda: self._angle_selected)` until `POST
  /campaigns/{id}/select-angle` signals it.
- **One failing asset branch never takes down the others.** Each of the three stage
  helpers (`_run_compose_stage`, `_run_video_stage`) catches `ActivityError`
  internally, records a `FAILED` stage event, and returns — it never re-raises into
  the `asyncio.gather`.

### 4.2 `RetryAssetWorkflow` — targeted single-asset regeneration

ID convention: `retry-asset-{asset_id}-{attempt}` (the attempt number is included so
a second manual retry of the same asset is a *distinct* workflow execution, not a
collision). Started by `POST /campaigns/{id}/assets/{asset_id}/retry`.

Dispatches to exactly one of `generate_hero_image` / `compose_ad` / `render_video`
based on `asset_type`, records the stage event, then calls
`recompute_campaign_status` so the campaign can transition out of
`completed_with_errors` once the retry succeeds.

**By design, it does not cascade.** Retrying `hero_image` does **not**
automatically re-run `compose_ad`/`render_video` from the new hero — the caller
must explicitly retry those too if they want the downstream assets refreshed from a
new hero image. This keeps each retry atomic and its blast radius predictable, at
the cost of the caller needing to know the dependency graph.

### 4.3 Retry policies (`app/workflows/retry_policies.py`)

One named `RetryPolicy` + `start_to_close_timeout` pair per activity class, sized to
that provider's actual failure/latency profile — not one blanket policy for
everything:

| Policy | Max attempts | Timeout | Rationale |
|---|---|---|---|
| `RESEARCH_RETRY_POLICY` | 2 | `research_timeout_seconds + 120s` | Research is the most expensive stage to redo (LLM + many web fetches); one retry only. |
| `SPEC_RETRY_POLICY` | 2 | 180s | Covers **infra** failures only — schema-validation corrective retries already happen *inside* the activity (up to `MAX_CORRECTIVE_ATTEMPTS = 3`), and a `DomainError` from exhausting those is explicitly non-retryable at this level. |
| `IMAGE_RETRY_POLICY` | 3 | 6 min | Provider-sized for the image-gen call. |
| `COMPOSE_RETRY_POLICY` | 3 | 3 min | Local Pillow work is cheap/fast; this is a safety net for transient DB/storage hiccups, not the compositing itself. |
| `VIDEO_RETRY_POLICY` | 3 | 12 min | Longest wall-clock stage (ffmpeg). |
| `CAMPAIGN_ACTIVITY_RETRY_POLICY` | 5 | 30s | Lightweight DB bookkeeping (`set_campaign_status`, `record_stage_event`, etc.) — short window, more attempts, since these should essentially never fail. |

### 4.4 Every activity, in one place (`app/worker.py::ACTIVITIES`)

```python
ACTIVITIES = [
    run_research_agent,        # app.research
    generate_creative_spec,    # app.creative
    generate_hero_image, compose_ad, render_video,   # app.assets
    set_campaign_status, recompute_campaign_status,  # app.campaigns
    record_stage_event, record_provider_usage, set_workflow_ids,
]
WORKFLOWS = [CampaignWorkflow, RetryAssetWorkflow]
```

A single flat list, deliberately — a newly written activity that nobody registers
here shows up as an activity that times out with no explanation, rather than
failing silently. The worker also configures a `SandboxedWorkflowRunner` with
passthrough for `app`, `pydantic`, `pydantic_settings`, `sqlalchemy`, `structlog` —
Temporal's workflow sandbox re-imports modules to enforce determinism, and without
the passthrough it would try (and fail) to sandbox heavy libraries the workflow
files only reach indirectly.

**Both the API process and the worker process construct a Temporal `Client` with
the identical `pydantic_data_converter`** (`app/api/temporal_client.py` and
`app/worker.py`, independently but consistently). This has to match exactly —
activity inputs/outputs are Pydantic models and dataclasses carrying `UUID` and
`Decimal` fields that Temporal's default JSON converter cannot round-trip.

---

## 5. What's actually AI-generated vs. deterministic

This trips people up, so it's worth being explicit:

| Stage | Who/what produces it | Consumes |
|---|---|---|
| **3 creative angles** | LLM, via a bounded ReAct loop (`app/research/loop.py`) with real `web_search`/`read_page` tool calls | Product brief |
| **Creative spec** (`hook`, `approved_copy`, `cta`, `product_identity`, `scene_description`, `palette`, `composition_guidance`, `video_outline`) | **One structured-output LLM call** (`app/creative/service.py`), grounded in the selected angle + brief, validated against `CreativeSpecSchema` (`extra="forbid"`) with up to 3 corrective retries on validation failure | Selected angle, brief |
| **Hero image** | OpenAI image API. Text-to-image (`images.generate`) — or, when the brief has a reference packshot, `images.edit` with that packshot as the **image input** so the model reproduces the real product. Portrait `1024x1536`, `quality=high` (both configurable). | The full prompt from `app/assets/prompts.py`: product identity + `scene_description` + `composition_guidance` + `palette`, wrapped in a fixed house style and hard rules (see below) |
| **1:1 and 9:16 ad images** | **Not separately generated.** Deterministic Pillow layout engine (`app/assets/compositing.py`): subject-aware crop of the hero, copy placed in the calmer zone inside Meta's safe zones, measured-contrast ink + soft gradient, bundled Inter Display type (eyebrow, auto-fitted/line-balanced headline, CTA pill in the palette accent) | Hero image bytes + `hook`/`cta`/`palette`/`product_identity.name` |
| **Video** | **Not AI-generated.** `app/assets/adapters/video_ffmpeg.py`: Pillow renders every frame (hook push-in with headline reveal → product push-in → end card with CTA pop, crossfades), FFmpeg encodes H.264 High / BT.709 / 30 fps / `+faststart` with a silent AAC track | Hero bytes + the **same** 9:16 design layers the still uses (`render_video_layers`) + `VIDEO_*` settings |

**Consistency strategy:** one generated hero image is the shared visual anchor;
everything else (both ad formats and the video) is deterministically derived from
those exact same bytes *and the same layout engine*, which keeps product identity,
palette, typography and treatment coherent across all four deliverables without
independent (and inconsistent) model calls.

**Why the hero prompt is so prescriptive.** Passing the LLM's `scene_description`
through bare let the image model fill every gap with "ad-like" output — collages,
split-screen storyboards and its own typography ("PRE-ORDER NOW", "100% vegan"
badges). That text collided with the overlay and invented claims the brief never
made. `app/assets/prompts.py` wraps the campaign material in fixed rules: one
continuous photograph, **no rendered text/badges/logos** beyond the packaging's own
label, product in the middle band of a portrait frame with calm negative space
above (headline) and below (CTA). The creative-spec schema carries the same
guidance in its field descriptions, and caps `hook` (90 chars) and `cta` (40).

**Reference images.** `reference_image_url` travels brief → `CampaignWorkflowInput`
→ `GenerateHeroImageInput` (and `RetryAssetWorkflowInput` for hero retries). The
activity fetches the bytes, verifies they decode as an image, normalises to PNG
(≤1536 px), and passes them to `ImageGenerationPort.generate(reference_image=...)`.
The OpenAI adapter sends them to `images.edit` with `input_fidelity=high` (via
`extra_body`, blank `OPENAI_IMAGE_INPUT_FIDELITY` omits it). In fixture mode the
fixture adapter pastes the reference into the placeholder hero, so the wiring is
visible without a provider call.

**Remaining notes:**

- `approved_copy` isn't meant for the image pipeline at all — it's the post's
  primary text, distinct from the short `hook`/`cta` that get overlaid.
- `video_outline.beats` is carried to the adapter in `VideoRenderSpec.extra`, but
  the render follows a fixed hook → product → CTA structure rather than one scene
  per beat (beats are prose storyboard notes, not display copy).
- The reference image is used by the image model only; the spec LLM does not see
  it (text-only port), so `product_identity` is still derived from the brief text.

---

## 6. External providers & fixture mode

One switch, `settings.provider_mode` (`"fixture" | "real"`), governs every
provider port at once (research LLM, web search, page reading, image generation,
video rendering, storage). Each domain package has exactly one factory function that
is the *only* place that branches on it:

- `app.research.adapters.build_research_adapters()` → LLM, web search, page reader
- `app.assets.adapters.get_image_adapter()` / `get_video_adapter()` /
  `get_storage_adapter()`

| Port | Fixture adapter | Real adapter |
|---|---|---|
| LLM (research + creative spec) | `FixtureLLMAdapter` — deterministic canned angles/spec, `[FIXTURE]`-prefixed, no network | `AnthropicLLMAdapter`* — OpenAI SDK under the hood |
| Web search | `FixtureSearchAdapter` | `TavilySearchAdapter` |
| Page reader | `FixturePageReaderAdapter` | `HttpPageReaderAdapter` |
| Image generation | `FixtureImageGenerationAdapter` — bundled placeholder JPEG | `ReplicateImageGenerationAdapter`* — OpenAI image API under the hood |
| Video render | `FixtureVideoRenderAdapter` — tiny placeholder MP4, rendered once via ffmpeg and cached | `FfmpegVideoRenderAdapter` — real ffmpeg pipeline |
| Storage | `FixtureStorageAdapter` — writes under `adapters/fixtures/storage/`, served at `/fixtures/*` | `LocalFilesystemStorageAdapter` (served at `/assets/*`) or `S3CompatibleStorageAdapter`, chosen by `settings.storage_backend` |

\* **Naming note:** `AnthropicLLMAdapter` and `ReplicateImageGenerationAdapter`
are legacy names kept so the rest of the codebase (adapter factories, imports)
didn't need touching when the underlying provider was swapped — both classes
actually call the OpenAI SDK today. The `provider="replicate-flux"` label recorded
on hero-image asset rows is similarly stale and should read something
OpenAI-accurate if you want `provider_usage`/asset records to be trustworthy.

Every fixture adapter supports a `force_fail_once=True` mode (surfaced via env vars
like `RESEARCH_FIXTURE_FAIL_LLM=1`), which is how the "reproducible provider
failure" requirement is exercised on demand against a running system without
touching real infrastructure.

`FixtureStorageAdapter` and `LocalFilesystemStorageAdapter` both hand back
browser-facing URLs (`{api_base_url}/fixtures/...` / `{api_base_url}/assets/...`,
served by two `StaticFiles` mounts in `app/api/main.py`) rather than filesystem
paths — so any code that needs the actual bytes back (`app/assets/activities.py
::_fetch_bytes`, used by `compose_ad`/`render_video` to read the hero image) has to
special-case both prefixes and read straight off the disk they share with the API
container via the bind mount, rather than making an HTTP round-trip the worker
container can't actually complete (nothing listens on `localhost:8000` inside the
worker container — only the `api` container runs uvicorn).

---

## 7. Persistence, idempotency, and recovery

- **Deterministic idempotency keys.** `derive_idempotency_key(campaign_id,
  asset_type)` (`app/assets/repository.py`) is one SHA-256 hash of
  `"assets:{campaign_id}:{asset_type}"`, reused by every asset activity — stable
  across retries and re-runs of the same logical unit of work.
- **Insert-or-get, never check-then-insert.** `ensure_pending` relies on a real
  Postgres unique constraint (`uq_assets_campaign_id_asset_type`) plus `ON CONFLICT
  DO NOTHING ... RETURNING`, so two concurrent/retried activity invocations for the
  same asset can never race into two rows.
- **Steps are written as they happen, not batched.** `DbStepRecorder.record_step`
  (`app/research/repository.py`) opens and commits its **own** session per research
  step — if the process dies mid-run, steps already written stay written; this is
  what makes `GET /campaigns/{id}/research` a genuinely live-pollable trace.
- **A row is only marked `completed` by the attempt that actually produced and
  stored the verified result** — never optimistically before the storage write
  succeeds.
- **Failed stages don't block siblings, and don't need a full restart.**
  `RetryAssetWorkflow` regenerates exactly one asset from its last-known-good
  inputs (`creative_spec_id`, and `hero_asset_id` where applicable), leaving
  successfully-completed sibling assets untouched.
- **Duplicate-submission risk, stated plainly:** the residual hazard is a Temporal
  activity's provider call succeeding but the ack/timeout racing after it — the
  activity has already done the work (e.g. an image was generated and billed) when
  Temporal decides to retry it. The idempotency key + upsert pattern above prevents
  a *second row*, but doesn't prevent a wasted provider call if the timeout window
  is too tight relative to real provider latency (see the retry-policy timeouts in
  §4.3, sized generously for exactly this reason).

---

## 8. HTTP API surface

All routes are under `/campaigns` (`app/api/routers/campaigns.py`):

| Method & path | What it does |
|---|---|
| `POST /campaigns` | Validates the brief (JSON or multipart with an optional `reference_image` file part), stores any reference image, creates the `campaigns` row, starts `CampaignWorkflow`. On workflow-start failure, marks the campaign `failed` rather than leaving it stuck in `draft`. |
| `GET /campaigns` | Paginated history list, optional `status` filter. |
| `GET /campaigns/{id}` | Full detail, including derived `stage_statuses` — reopenable after a refresh/restart since everything reads from Postgres. |
| `GET /campaigns/{id}/research` | The full, live-pollable research trace: steps, sources, angles. Returns `200` with `run: null` (not `404`) when research hasn't started — that's a legitimate, pollable state. |
| `POST /campaigns/{id}/select-angle` | Persists the selection (committed) **then** signals the running workflow — in that order, so the workflow can never be told about a selection the DB doesn't have. Returns `409` on a repeat selection by design (not re-signalable — see the docstring in code for the documented recovery hazard). |
| `GET /campaigns/{id}/spec` | The current (highest-version) creative spec. `404` if none exists yet — there's no meaningful "partial" spec to show. |
| `GET /campaigns/{id}/assets` | Every asset row, each resolved against the `creative_specs` version it was generated from (so a retried asset generated from a newer spec than its siblings is visibly flagged `is_current`). |
| `POST /campaigns/{id}/assets/{asset_id}/retry` | Starts `RetryAssetWorkflow` for one asset. Guards: asset must belong to the campaign, must not already be `pending`/`generating`, and hero-derived types require a *completed* hero image first. Returns `202`. |
| `GET /campaigns/{id}/usage` | Recorded provider usage/cost, with estimates explicitly labelled `is_estimated`. |

---

## 9. Configuration (`app/config/settings.py`)

Pydantic `BaseSettings`, loaded from a `.env` file (see `docker/.env` /
`.env.example`) plus real environment variables, case-insensitive. Cross-field
validation runs once at startup (`_validate_cross_field_rules`) and fails fast if:

- `provider_mode == "real"` but the required provider keys aren't set,
- `storage_backend == "s3"` but the S3 fields aren't all set,
- `storage_backend == "local"` but `local_storage_path` is empty,
- `video_min_duration_seconds > video_max_duration_seconds`.

Key groups: `provider_mode` / `environment`; `database_url`; `temporal_host` /
`temporal_namespace` / `temporal_task_queue`; per-provider API keys; `storage_backend`
/ `local_storage_path` / `s3_*`; `research_max_tool_calls` /
`research_max_iterations` / `research_timeout_seconds`; `max_reference_image_mb` /
`allowed_reference_image_types`; target pixel dimensions for both image formats and
the video, plus `video_min_duration_seconds` / `video_max_duration_seconds`;
hero-image generation: `openai_image_model` / `openai_image_size` (default
`1024x1536`) / `openai_image_quality` (default `high`) /
`openai_image_input_fidelity` (default `high`, blank to omit).



---

## 10. Local development

```
docker/
├── docker-compose-local.yml     app Postgres, api (uvicorn --reload), worker,
│                                  + includes the Temporal stack below
├── temporal/docker-compose-temporal.yml   Temporal server, its own Postgres,
│                                            Elasticsearch, Web UI
├── Dockerfile                    one image for both api and worker containers
└── Makefile                      make local / make migrate / make psql / etc.
```

`make local` (from `docker/`) brings up everything. `api` and `worker` share one
image and one bind-mounted source tree (`..:/code`) — `api` runs with
`uvicorn --reload` for hot reload; `worker` has no such mechanism for a Temporal
worker process, so a code change there needs `make restart-worker`.

### Tests

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests
```

Offline and provider-free. The ffmpeg render test is skipped when `ffmpeg` is not
on `PATH`. `tests/test_workflow_reference_image.py` runs the real workflows on
Temporal's time-skipping test server with mocked activities; the SDK downloads that
server on first use, or set `TEMPORAL_TEST_SERVER_PATH` to a pre-downloaded
`temporal-test-server` binary (from the `temporalio/sdk-java` GitHub releases). It
skips if no server can be started.

Two independent Postgres containers by design: the app's own tables, and
Temporal's internal state — different lifecycles, different migration tools
(Alembic vs. Temporal's own `auto-setup`), and mixing them is a foot-gun with no
upside.

---

## 11. Known limitations (for the write-up)



1. `research_runs.campaign_id`, `creative_specs.campaign_id`, and
   `assets.campaign_id` are unenforced UUID columns, not real foreign keys to
   `campaigns` — a migration debt from build ordering, not a design choice.
2. `video_outline.beats` reaches the video adapter but does not drive scene
   count; the render is a fixed hook → product → CTA structure.
3. Reference-image fidelity depends on the image model: `images.edit` receives
   the packshot, but small label text can still drift. There is no automated
   check that the generated product matches the reference.
4. The research loop's system prompt asks for "2–3" pages read, while the brief
   asks for "at least 3" — currently a soft gap-note, not a hard minimum.
5. Asset rows label the image-generation provider as `"replicate-flux"` even
   though the adapter underneath calls OpenAI — cosmetic, but worth fixing before
   relying on `provider_usage` for cost attribution.