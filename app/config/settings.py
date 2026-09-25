from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.common.exceptions import ValidationError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["local", "test", "production"] = Field(
        default="local",
        description="Deployment environment.",
    )

    provider_mode: Literal["fixture", "real"] = Field(
        default="fixture",
        description=(
            "Central switch for every provider port (LLM, web search, page "
            "reading, image generation, video rendering, storage). "
            "'fixture' runs against canned adapters with no external calls "
            "or credentials; 'real' calls the real providers and requires "
            "their credentials to be set."
        ),
    )

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/campaign_studio",
        description="Async Postgres DSN (SQLAlchemy 2.0 async driver, e.g. asyncpg).",
    )

    api_base_url: str = Field(
        default="http://localhost:8000",
        description="The base URL of this API, used to construct absolute URLs to static assets.",
    )

    temporal_host: str = Field(
        default="localhost:7233",
        description="Temporal server address (host:port) for the worker to connect to.",
    )
    temporal_namespace: str = Field(
        default="default",
        description="Temporal namespace to use.",
    )
    temporal_task_queue: str = Field(
        default="campaign-studio",
        description="Temporal task queue the worker polls and workflows are started on.",
    )

    openai_api_key: str | None = Field(
        default=None,
        description="OpenAI API key, used by the LLM provider adapter.",
    )
    openai_model: str = Field(
        default="gpt-4o",
        description="Default OpenAI model name for the LLM provider adapter.",
    )
    openai_image_model: str | None = Field(
        default=None,
        description=(
            "OpenAI image model for hero-image generation. Unset keeps the "
            "adapter's built-in default."
        ),
    )
    openai_image_size: str = Field(
        default="1024x1536",
        description=(
            "Hero image size requested from the image model. Portrait 2:3 is "
            "deliberate: the 9:16 ad/video crop keeps ~84% of its width and "
            "the 1:1 crop keeps the full width, instead of a square hero "
            "being cropped to 56% width and upscaled ~1.9x for 9:16."
        ),
    )
    openai_image_quality: Literal["low", "medium", "high", "auto"] = Field(
        default="high",
        description=(
            "Image quality tier. 'auto' lets the provider pick per request, "
            "which is a source of run-to-run variance; ad creatives pin 'high'."
        ),
    )
    openai_image_input_fidelity: Literal["high", "low"] | None = Field(
        default="high",
        description=(
            "input_fidelity sent with reference-image edits. 'high' tells the "
            "model to preserve the packshot's logo/label detail. Set empty to "
            "omit the parameter for models that do not accept it."
        ),
    )
    tavily_api_key: str | None = Field(
        default=None,
        description="Tavily API key, used by the web search / page reader provider adapter.",
    )

    storage_backend: Literal["local", "s3"] = Field(
        default="local",
        description="Which StoragePort adapter to construct: local filesystem or S3-compatible.",
    )
    local_storage_path: str = Field(
        default="./data/assets",
        description="Base path on disk used when storage_backend='local'.",
    )
    s3_bucket: str | None = Field(
        default=None,
        description="S3 bucket name (required when storage_backend='s3').",
    )
    s3_region: str | None = Field(
        default=None,
        description="S3 region (required when storage_backend='s3').",
    )
    s3_endpoint_url: str | None = Field(
        default=None,
        description="S3-compatible endpoint URL (required when storage_backend='s3').",
    )
    s3_access_key_id: str | None = Field(
        default=None,
        description="S3 access key id (required when storage_backend='s3').",
    )
    s3_secret_access_key: str | None = Field(
        default=None,
        description="S3 secret access key (required when storage_backend='s3').",
    )
    s3_presigned_url_expiry_seconds: int = Field(
        default=3600,
        gt=0,
        description="Expiry, in seconds, for presigned URLs issued for stored objects.",
    )

    research_max_tool_calls: int = Field(
        default=20,
        gt=0,
        description="Maximum number of tool calls allowed in a single research loop run.",
    )
    research_max_iterations: int = Field(
        default=8,
        gt=0,
        description="Maximum number of iterations allowed in a single research loop run.",
    )
    research_timeout_seconds: int = Field(
        default=300,
        gt=0,
        description="Wall-clock timeout, in seconds, for a single research loop run.",
    )

    research_min_sources: int = Field(
        default=3,
        gt=0,
        description=(
            "Relevant source pages (pages with at least one verified finding) the agent "
            "is sent back for before it may stop, while budget remains."
        ),
    )
    research_max_reads_per_domain: int = Field(
        default=2,
        gt=0,
        description="Cap on pages read from one site, so 'N sources' means N viewpoints.",
    )
    research_search_depth: Literal["basic", "advanced"] = Field(
        default="advanced",
        description="Tavily search depth. 'advanced' ranks by relevance to the query more tightly.",
    )
    research_excluded_domains: list[str] = Field(
        default_factory=list,
        description=(
            "Extra domains never searched or read, on top of the built-in news / "
            "encyclopedia / social-video exclusions (JSON array)."
        ),
    )

    max_reference_image_mb: int = Field(
        default=10,
        gt=0,
        description="Maximum accepted size, in MB, for an optional reference-image upload.",
    )
    allowed_reference_image_types: list[str] = Field(
        default_factory=lambda: ["image/jpeg", "image/png"],
        description="Accepted MIME types for an optional reference-image upload.",
    )

    image_square_width_px: int = Field(
        default=1080,
        gt=0,
        description="Target width, in pixels, for the square (1:1) image ad format.",
    )
    image_square_height_px: int = Field(
        default=1080,
        gt=0,
        description="Target height, in pixels, for the square (1:1) image ad format.",
    )
    image_vertical_width_px: int = Field(
        default=1080,
        gt=0,
        description="Target width, in pixels, for the vertical (9:16) image ad format.",
    )
    image_vertical_height_px: int = Field(
        default=1920,
        gt=0,
        description="Target height, in pixels, for the vertical (9:16) image ad format.",
    )

    video_width_px: int = Field(
        default=1080,
        gt=0,
        description="Target width, in pixels, for the generated video.",
    )
    video_height_px: int = Field(
        default=1920,
        gt=0,
        description="Target height, in pixels, for the generated video.",
    )
    video_min_duration_seconds: int = Field(
        default=6,
        gt=0,
        description="Minimum accepted duration, in seconds, for the generated video.",
    )
    video_max_duration_seconds: int = Field(
        default=10,
        gt=0,
        description="Maximum accepted duration, in seconds, for the generated video.",
    )

    @field_validator("openai_image_model", "openai_image_input_fidelity", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # `OPENAI_IMAGE_INPUT_FIDELITY=` in .env means "omit it", not "".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _validate_cross_field_rules(self) -> Settings:
        errors: list[str] = []

        if self.provider_mode == "real":
            required_provider_keys = {
                "OPENAI_API_KEY": self.openai_api_key,
                "TAVILY_API_KEY": self.tavily_api_key,
            }
            errors += [
                name for name, value in required_provider_keys.items() if not value
            ]

        if self.storage_backend == "s3":
            required_s3_fields = {
                "S3_BUCKET": self.s3_bucket,
                "S3_REGION": self.s3_region,
                "S3_ENDPOINT_URL": self.s3_endpoint_url,
                "S3_ACCESS_KEY_ID": self.s3_access_key_id,
                "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
            }
            errors += [name for name, value in required_s3_fields.items() if not value]
        elif self.storage_backend == "local" and not self.local_storage_path:
            errors.append("LOCAL_STORAGE_PATH")

        if self.video_min_duration_seconds > self.video_max_duration_seconds:
            errors.append(
                "VIDEO_MIN_DURATION_SECONDS must be <= VIDEO_MAX_DURATION_SECONDS"
            )

        if errors:
            raise ValidationError(
                "Invalid configuration — missing or inconsistent variable(s): "
                + ", ".join(errors)
            )

        return self








