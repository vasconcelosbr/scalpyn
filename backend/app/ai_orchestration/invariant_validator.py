from __future__ import annotations

from dataclasses import dataclass

from .contracts import AIRequest, Authority
from .dataset_service import CanonicalAnalysisDataset, QUALITY_BLOCKS
from .configuration_bundle_service import ConfigurationBundle
from .errors import AIErrorCode, fail


@dataclass(frozen=True)
class RuntimeInvariantState:
    spot_never_sell_at_loss_config: bool | None
    live_trading_authority: bool = False
    model_promotion_authority: bool = False
    real_risk_mutation_requested: bool = False


class InvariantValidator:
    def validate(self, request: AIRequest, *, bundle: ConfigurationBundle,
                 dataset: CanonicalAnalysisDataset, state: RuntimeInvariantState) -> None:
        if state.live_trading_authority or state.model_promotion_authority or state.real_risk_mutation_requested:
            raise fail(AIErrorCode.INVARIANT_VIOLATION, "Live trading, model promotion, and real risk mutation are not authorized", http_status=403)
        if request.configuration_scope.require_complete_bundle and request.authority != Authority.ANALYSIS_ONLY and bundle.lineage_status != "COMPLETE":
            raise fail(AIErrorCode.CONFIGURATION_BUNDLE_INCOMPLETE, "A complete configuration bundle is required")
        if request.authority != Authority.ANALYSIS_ONLY and dataset.quality_status in QUALITY_BLOCKS:
            raise fail(AIErrorCode.DATASET_CONTRACT_INVALID, "Unresolved dataset conflicts block proposal authority")
        spot_scope = request.origin_module.upper().startswith("SPOT") or "spot" in request.question.lower()
        if spot_scope and state.spot_never_sell_at_loss_config is False:
            raise fail(AIErrorCode.INVARIANT_VIOLATION, "INVARIANT_CONFLICT_BLOCKED: Spot exit authority is disabled", http_status=409)
