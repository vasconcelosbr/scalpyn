from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .hashing import canonical_hash


class ContextFingerprint(BaseModel):
    model_config = ConfigDict(frozen=True)
    profile_family: str | None
    timeframe: str | None
    market_regime: str | None
    social_regime: str | None
    risk_policy_version: str | None
    strategy_exit_policy: str | None
    feature_contract: str | None
    label_contract: str | None
    model_lane: str | None

    @property
    def digest(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


def mutation_fingerprint(payload: dict) -> str:
    return canonical_hash(payload)


def memory_matches(expected: ContextFingerprint, candidate: ContextFingerprint) -> bool:
    return expected.digest == candidate.digest
