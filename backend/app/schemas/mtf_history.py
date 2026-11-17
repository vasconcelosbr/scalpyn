"""Analysis-only settings; never included in a strategy/profile engine hash."""
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, field_validator, model_validator


class HistoryConfig(BaseModel):
    enabled: bool = True
    horizons_minutes: list[int] = Field(default_factory=lambda: [15, 60, 240, 1440], min_length=1, max_length=24)

    @field_validator('horizons_minutes')
    @classmethod
    def horizons(cls, values):
        if any(v <= 0 or v % 5 or v > 10080 for v in values):
            raise ValueError('Use múltiplos de cinco minutos, até uma semana')
        return sorted(set(values))


class HistoryFilter(BaseModel):
    watchlist_id: str | None = None
    symbol: str | None = None
    profile_id: str | None = None
    status: str | None = None
    trend: str | None = None
    quality: str | None = None
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode='after')
    def dates(self):
        for value in (self.start, self.end):
            if value is not None and value.tzinfo is None:
                raise ValueError('Data deve incluir o fuso horário')
        if self.start and self.end and self.start > self.end:
            raise ValueError('Intervalo de datas inválido')
        return self


class HistoryExport(BaseModel):
    filters: HistoryFilter = Field(default_factory=HistoryFilter)
    format: Literal['xlsx', 'csv'] = 'xlsx'
    timezone: str = 'America/Sao_Paulo'

    @field_validator('timezone')
    @classmethod
    def zone(cls, value):
        ZoneInfo(value)
        return value
