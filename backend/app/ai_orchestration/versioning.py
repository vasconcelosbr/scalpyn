from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from .hashing import canonical_hash


@dataclass(frozen=True)
class CandidateVersion:
    id: UUID
    parent_id: UUID | None
    profile_id: UUID
    config: dict[str, Any]
    config_hash: str
    status: str
    rollback_to_version_id: UUID | None = None


class VersionOnChangePolicy:
    @staticmethod
    def create_candidate(*, profile_id: UUID, base_version_id: UUID | None,
                         config: dict[str, Any], bundle_complete: bool) -> CandidateVersion:
        if not bundle_complete:
            from .errors import AIErrorCode, fail
            raise fail(AIErrorCode.CONFIGURATION_BUNDLE_INCOMPLETE, "A complete bundle is required for a change set")
        return CandidateVersion(uuid4(), base_version_id, profile_id, config, canonical_hash(config), "CANDIDATE")

    @staticmethod
    def rollback(*, current_candidate: CandidateVersion, restored_config: dict[str, Any]) -> CandidateVersion:
        return CandidateVersion(uuid4(), current_candidate.id, current_candidate.profile_id, restored_config,
                                canonical_hash(restored_config), "CANDIDATE", current_candidate.id)
