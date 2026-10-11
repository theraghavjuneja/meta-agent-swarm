"""Activity input/output DTOs for app/assets.

`provider_usage` doesn't exist as a table until Module 7. Mirroring Module
5's approach, every activity Output here carries a `UsageSummary` so Module
8's workflows can persist it later without these activities changing.

Plain dataclasses (not pydantic) so Temporal's default data converter can
serialize them without extra configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class UsageSummary:
    provider: str
    operation: str
    units: float
    unit_type: str
    estimated_cost_usd: float
    is_estimated: bool = True


@dataclass(frozen=True)
class GenerateHeroImageInput:
    campaign_id: UUID
    creative_spec_id: UUID


@dataclass(frozen=True)
class GenerateHeroImageOutput:
    asset_id: UUID
    storage_url: str
    width: int
    height: int
    usage: UsageSummary


@dataclass(frozen=True)
class ComposeAdInput:
    campaign_id: UUID
    creative_spec_id: UUID
    hero_asset_id: UUID
    asset_type: str  # "ad_1x1" | "ad_9x16" -- validated inside the activity


@dataclass(frozen=True)
class ComposeAdOutput:
    asset_id: UUID
    storage_url: str
    width: int
    height: int
    usage: UsageSummary


@dataclass(frozen=True)
class RenderVideoInput:
    campaign_id: UUID
    creative_spec_id: UUID
    hero_asset_id: UUID


@dataclass(frozen=True)
class RenderVideoOutput:
    asset_id: UUID
    storage_url: str
    width: int
    height: int
    duration_seconds: float
    usage: UsageSummary
