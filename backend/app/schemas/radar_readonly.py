"""Versioned export, intentionally independent of the Watchlist UI schema."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IndicatorReading(ExportModel):
    selection: Literal["latest_per_group_timeframe"] = "latest_per_group_timeframe"
    source_table: Literal["indicators"] = "indicators"
    scheduler_group: str | None
    timeframe: str | None
    timestamp: datetime | None
    age_seconds: float | None
    # No invented freshness threshold. Envelope status remains in values.
    freshness: Literal["not_assessed"] = "not_assessed"
    values: dict[str, Any] | None


class RadarAsset(ExportModel):
    id: UUID
    symbol: str
    current_price: float | None
    price_change_24h: float | None
    volume_24h: float | None
    market_cap: float | None
    alpha_score: float | None
    entered_at: datetime | None
    refreshed_at: datetime | None
    analysis_snapshot: dict[str, Any] | None
    indicator_readings: list[IndicatorReading] = Field(default_factory=list)


class RadarWatchlistResponse(ExportModel):
    contract_version: Literal["radar_watchlist_v1"] = "radar_watchlist_v1"
    served_by: Literal["service_readonly"] = "service_readonly"
    read_mode: Literal["persisted-only"] = "persisted-only"
    membership_basis: Literal["persisted_active_rows"] = "persisted_active_rows"
    queried_at: datetime
    watchlist_id: UUID
    watchlist_name: str
    level: str
    market_mode: str
    source_pool_id: UUID | None
    source_watchlist_id: UUID | None
    last_scanned_at: datetime | None
    asset_count: int
    assets: list[RadarAsset]


class RadarPoolCoin(ExportModel):
    id: UUID
    symbol: str
    market_type: str
    is_active: bool
    is_approved: bool | None
    is_tradable: bool | None
    held_for_open_position: bool
    added_at: datetime | None
    origin: str | None
    discovered_at: datetime | None
    radar_last_seen_at: datetime | None
    indicator_readings: list[IndicatorReading]

class RadarPoolIdentity(ExportModel):
    id: UUID
    name: str
    market_type: str
    is_active: bool
    updated_at: datetime | None

class RadarPoolContext(ExportModel):
    contract_version: Literal['radar_pool_context_v1'] = 'radar_pool_context_v1'
    served_by: Literal['service_readonly'] = 'service_readonly'
    read_mode: Literal['persisted-only'] = 'persisted-only'
    queried_at: datetime
    pool: RadarPoolIdentity
    membership_basis: Literal['all_persisted_pool_rows']
    membership_complete: Literal[True]
    row_count: int
    active_count: int
    coins: list[RadarPoolCoin]
    watchlist: RadarWatchlistResponse
