"""Fail-closed adapter around the repository's replay capability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ReplayResult:
    status: str
    supported: bool
    metrics: Mapping[str, Any]
    reason: str
    operational_mutation: bool = False
    orders_created: int = 0


class ProfileReplayAdapter:
    """Never delegates to the current `/backoffice/replay/run` stub.

    The repository does not expose a trustworthy general profile replay engine.
    Returning an explicit unsupported result prevents a fake validation from
    advancing a candidate state.
    """

    async def run(
        self,
        *,
        base_profile_config: Mapping[str, Any],
        candidate_config: Mapping[str, Any],
        dataset_hash: str,
    ) -> ReplayResult:
        del base_profile_config, candidate_config, dataset_hash
        return ReplayResult(
            status="REPLAY_FAILED",
            supported=False,
            metrics={},
            reason="existing_profile_replay_engine_is_stub",
        )
