from __future__ import  annotations

from typing import  Literal


from pydantic import  Field, model_validator
from pydantic_settings import  BaseSettings, SettingsConfigDict


from app.common.exceptions import  ValidationError


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

    log_format: Literal["json", "console"] = Field(
        default="console",
        description=(
            "Format passed to app.common.logging.configure_logging(): "
            "'json' for structured production logs, 'console' for local dev."
        ),
    )

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/campaign_studio",
        description="Async Postgres DSN (SQLAlchemy 2.0 async driver, e.g. asyncpg).",
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

    nthropic_api_key: str | None = Field(
        default=None,
        description="Anthropic API key, used by the LLM provider adapter.",
    )
    anthropic_model: str = Field(
        default="claude-sonnet-5",
        description="Default Anthropic model name for the LLM provider adapter.",
    )
    tavily_api_key: str | None = Field(
        default=None,
        description="Tavily API key, used by the web search / page reader provider adapter.",
    )
    replicate_api_token: str | None = Field(
        default=None,
        description="Replicate API token, used by the image generation provider adapter.",
    )

    storage_backend: Literal["local", "s3"] = Field(
        default="local",
        description="Which StoragePort adapter to construct: local filesystem or S3-compatible.",
    )
    local_storage_path: str = Field(
        default="./data/storage",
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
        default=6,
        gt=0,
        description="Maximum number of iterations allowed in a single research loop run.",
    )
    research_timeout_seconds: int = Field(
        default=300,
        gt=0,
        description="Wall-clock timeout, in seconds, for a single research loop run.",
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

    @model_validator(mode="after")
    def _validate_cross_field_rules(self) -> "Settings":
        errors: list[str] = []

        if self.provider_mode == "real":
            required_provider_keys = {
                "ANTHROPIC_API_KEY": self.anthropic_api_key,
                "TAVILY_API_KEY": self.tavily_api_key,
                "REPLICATE_API_TOKEN": self.replicate_api_token,
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








