"""Operational settings of the L3 continuation sweep.

Kept apart from ``shadow_l3_exit_policy`` on purpose: that policy is frozen
into every shadow snapshot and its digest is part of the managed-label
contract, so an operational knob there would change the hash of every open
position. Nothing here changes an exit rule; it only decides how long the
sweep waits for a closed 1m candle that is due but has not arrived before it
gives up on that minute (which then stays an explicit, logged gap).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_TYPE = "shadow_l3_exit_runtime"
VERSION = "shadow_l3_exit_runtime_v1"


class ShadowL3ExitRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    version: Literal["shadow_l3_exit_runtime_v1"]
    # No default: activation must state the wait explicitly.
    missing_candle_wait_seconds: int = Field(gt=0, le=86400)

    @model_validator(mode="before")
    @classmethod
    def reject_boolean_numbers(cls, data):
        if isinstance(data, dict) and any(isinstance(v, bool) for v in data.values()):
            raise ValueError("Boolean values cannot be used as runtime parameters")
        return data
