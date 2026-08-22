"""Fail-closed configuration for observational entry-risk capture."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class EntryRiskObservationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["entry_risk_observation_v1"] = "entry_risk_observation_v1"
    capture_enabled: bool = True
    legacy_enabled: bool = True
    momentum_enabled: bool = True
    momentum_operational: Literal[False] = False
    exhaustion_enabled: bool = True
    exhaustion_operational: Literal[False] = False
    source_timeframe: Literal["5m"] = "5m"
    source_stale_seconds: Literal[300] = 300


DEFAULT_ENTRY_RISK_OBSERVATION = EntryRiskObservationConfig().model_dump()
