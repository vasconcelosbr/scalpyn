"""Public contracts for social-intelligence ingestion and scoring config."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator


class SocialSourceInput(BaseModel):
    platform: str = Field(min_length=1, max_length=64)
    url: AnyHttpUrl
    title: Optional[str] = Field(default=None, max_length=500)
    published_at: Optional[datetime] = None

    @field_validator("published_at")
    @classmethod
    def require_source_timezone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("published_at must include a timezone")
        return value


class SocialAssetInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    attention_score: float = Field(ge=0, le=100)
    sentiment_score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    sentiment_label: str = Field(min_length=1, max_length=32)
    recommendation: str = Field(min_length=1, max_length=32)
    summary: str = Field(min_length=1, max_length=4000)
    narratives: List[str] = Field(default_factory=list, max_length=50)
    anomalies: List[str] = Field(default_factory=list, max_length=50)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    sources: List[SocialSourceInput] = Field(min_length=1, max_length=100)

    @field_validator("narratives", "anomalies")
    @classmethod
    def validate_text_items(cls, values: List[str]) -> List[str]:
        cleaned = [value.strip() for value in values if isinstance(value, str) and value.strip()]
        if any(len(value) > 1000 for value in cleaned):
            raise ValueError("items must contain at most 1000 characters")
        return cleaned


class SocialRunInput(BaseModel):
    contract_version: str = Field(min_length=1, max_length=64)
    external_run_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=128)
    window_start: datetime
    window_end: datetime
    collected_at: datetime
    assets: List[Any] = Field(min_length=1, max_length=500)

    @field_validator("window_start", "window_end", "collected_at")
    @classmethod
    def require_run_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_window(self):
        if self.window_start >= self.window_end:
            raise ValueError("window_start must be before window_end")
        if self.window_end > self.collected_at:
            raise ValueError("window_end must be at or before collected_at")
        return self


class SocialRejectedItem(BaseModel):
    index: int
    symbol: Optional[str] = None
    reasons: List[str]


class SocialIngestionResponse(BaseModel):
    run_id: UUID
    status: Literal["ACCEPTED", "PARTIAL", "REJECTED", "DUPLICATE"]
    accepted_symbols: List[str]
    rejected_items: List[SocialRejectedItem]
    payload_hash: str


class SocialScoreConfig(BaseModel):
    enabled: bool = False
    spot_weight: float = Field(default=0.20, ge=0, le=1)
    futures_weight: float = Field(default=0.20, ge=0, le=1)
    max_age_seconds: int = Field(default=86_400, gt=0, le=604_800)
    mode: Literal["symmetric"] = "symmetric"
    formula_version: Literal["confidence_adjusted_v1"] = "confidence_adjusted_v1"


class SocialObservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    symbol: str
    attention_score: float
    sentiment_score: float
    confidence: float
    sentiment_label: str
    recommendation: str
    summary: str
    narratives: List[str]
    anomalies: List[str]
    metrics: Dict[str, Any]
    sources: List[Dict[str, Any]]
    contract_version: str
    window_start: datetime
    window_end: datetime
    collected_at: datetime
    age_seconds: Optional[float] = None
    eligible: Optional[bool] = None
