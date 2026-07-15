"""Standalone CatBoost retrain script — usa URL pública do banco.

Treina Lane 2 (CatBoost / L3_PROFILE / is_tp_4h_v2_sim_outcome) e persiste em ml_models.
Não requer rede interna Railway. Não promove modelo automaticamente.
Não altera outcomes, shadow trades, estratégias ou Auto-Pilot.

Uso:
    python backend/scripts/run_catboost_retrain.py [--dry-run]

Flags:
    --dry-run   Roda análise do dataset mas não treina nem persiste (default: False)

Precondições (verificadas antes de rodar):
    - Label v2 ativo: outcome='TP_HIT' AND holding_seconds <= T
    - Fase 3 (1m fallback): shadow_timeout_analyzer usa IN ('1m','5m')
    - Fase 4 (features_snapshot): prediction_service retorna features_snapshot
    - Drift L3+L3_LAB: < 15pp (verificar antes de promover o modelo resultante)

Dataset policy (L3_PROFILE_STRICT):
    - Fontes: L3 + L3_LAB, profile_id IS NOT NULL
    - Label: outcome='TP_HIT' AND holding_seconds <= T
    - allow_mixed_source=False bloqueado por default (L3+L3_LAB combinados causou
      colapso de AUC em v42 — treinar L3 ou L3_LAB separado, ou fonte única)
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("OPTUNA_VERBOSITY", "WARNING")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("catboost_retrain")

DRY_RUN = "--dry-run" in sys.argv

USER_ID = UUID("8080110c-ee9d-4a2b-a53f-6bef86dd8867")
WIN_THRESHOLD_S = 14400.0  # is_tp_4h_v2_sim_outcome

# Treinar sobre L3_LAB (maior volume, 15.7% pos rate) — L3 tem 66% NULL profile_id
# Para treinar sobre L3 estrito: mudar para ["L3"] (aplica L3_PROFILE_STRICT automaticamente)
CATBOOST_SOURCES = [s for s in os.getenv("CATBOOST_SOURCES", "L3").split(",") if s]
ADVISORY_INTELLIGENCE = os.getenv("ADVISORY_INTELLIGENCE", "false").strip().lower() in {
    "1", "true", "yes", "on",
}


def _dry_run_gate_payload(
    *,
    records: int,
    min_required: int,
    dataset_query_cutoff: datetime,
    maturity_margin: int,
    sources,
    gate_meta,
    split_readiness=None,
):
    diagnostics = gate_meta["maturity_diagnostics"]
    barrier = gate_meta["barrier_contract"]
    split_readiness = split_readiness or {}
    holdout_ready = split_readiness.get("has_test", True)
    records_ready = records >= min_required
    reason = None
    if not records_ready:
        reason = "insufficient_retrain_eligible_rows"
    elif not holdout_ready:
        reason = (
            split_readiness.get("diagnostics", {}).get("block_reason")
            or "insufficient_promotion_holdout"
        )
    return {
        "dry_run": True,
        "status": "ready" if records_ready and holdout_ready else "skipped",
        "reason": reason,
        "sources": sources,
        "dataset_query_cutoff": dataset_query_cutoff.isoformat(),
        "maturity_embargo_margin_minutes": maturity_margin,
        "official_candidates": diagnostics.get("official_candidates", 0),
        "labels_unresolved_at_cutoff": diagnostics.get(
            "labels_unresolved_at_cutoff", 0
        ),
        "observations_immature_at_cutoff": diagnostics.get(
            "observations_immature_at_cutoff", 0
        ),
        "records_mature": diagnostics.get("records_mature", 0),
        "records_with_profile": gate_meta["records_with_profile"],
        **barrier,
        "records": records,
        "min_required": min_required,
        "deficit": max(0, min_required - records),
        "split_readiness": split_readiness or None,
        "l3_strict_meta": gate_meta["l3_strict_meta"],
    }


async def main():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    db_url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_PUBLIC_URL")
    if not db_url:
        raise RuntimeError("missing_DATABASE_URL")
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(db_url, pool_pre_ping=True)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    logger.info("DB: %s", db_url.split("@")[-1])
    logger.info(
        "DRY_RUN=%s  USER_ID=%s  WIN_THRESHOLD_S=%s  SOURCES=%s  ADVISORY_INTELLIGENCE=%s",
        DRY_RUN, USER_ID, WIN_THRESHOLD_S, CATBOOST_SOURCES, ADVISORY_INTELLIGENCE,
    )

    async with AsyncSessionLocal() as db:
        from backend.app.services.ml_challenger_service import (
            MLChallengerService,
            _require_positive_int_config,
        )

        svc = MLChallengerService()

        if DRY_RUN:
            logger.info("[DRY-RUN] Carregando dataset L3_PROFILE apenas — sem treino nem persistencia")
            ml_config = await svc._load_ml_config(db)
            min_required = _require_positive_int_config(
                ml_config, "ml_catboost_retrain_min_eligible_rows"
            )
            dataset_query_cutoff = datetime.now(timezone.utc)
            maturity_margin = ml_config.get("ml_maturity_embargo_margin_minutes")
            blocked_reason = svc._check_mixed_source_gate(CATBOOST_SOURCES)
            if blocked_reason:
                return {
                    "dry_run": True,
                    "status": "blocked",
                    "reason": blocked_reason,
                    "sources": CATBOOST_SOURCES,
                    "dataset_query_cutoff": dataset_query_cutoff.isoformat(),
                }
            records, gate_meta = await svc._prepare_catboost_gate_records(
                db,
                USER_ID,
                lookback_days=90,
                cb_sources=CATBOOST_SOURCES,
                dataset_query_cutoff=dataset_query_cutoff,
                ml_config=ml_config,
                advisory_intelligence=ADVISORY_INTELLIGENCE,
                collect_diagnostics=True,
            )
            logger.info(
                "[DRY-RUN] Records apos contrato: %d (min_required=%d)",
                len(records), min_required,
            )
            from backend.app.ml.feature_extractor import FEATURE_COLUMNS

            label_objective = str(ml_config.get("ml_label_objective") or "fast_tp")
            feature_contract = ml_config.get("ml_feature_contract", {}).get(
                gate_meta["lane"]
            )
            built = svc._build_l3_dataset(
                records,
                list(FEATURE_COLUMNS),
                WIN_THRESHOLD_S,
                lane_name=gate_meta["lane"],
                lane_contract=feature_contract,
                feature_ranges=ml_config.get("ml_feature_ranges"),
                backfilled_feature_names=ml_config.get("ml_backfilled_feature_names"),
                backfill_marker_key=ml_config.get("ml_backfill_marker_key"),
                label_objective=label_objective,
                fee_roundtrip_pct=float(ml_config["ml_fee_roundtrip_pct"]),
            )
            X, y = built[0], built[1]
            split = svc._chronological_split_with_embargo(
                X,
                y,
                metadata=[built[4], built[5], built[6], built[8]],
                created_at=built[5],
                holding_seconds=built[7],
                group_ids=built[8],
                embargo_seconds=int(ml_config["ml_split_embargo_seconds"]),
                min_train_size=min_required,
                min_validation_size=_require_positive_int_config(
                    ml_config, "ml_threshold_min_positives"
                ),
                min_test_size=_require_positive_int_config(
                    ml_config, "ml_promotion_min_test_samples"
                ),
            )
            split_readiness = {
                "has_test": split["has_test"],
                "train_samples": len(split["y_tr"]),
                "validation_samples": len(split["y_va"]),
                "test_samples": len(split["y_te"]) if split["y_te"] is not None else 0,
                "diagnostics": split["split_diagnostics"],
            }
            return _dry_run_gate_payload(
                records=len(records),
                min_required=min_required,
                dataset_query_cutoff=dataset_query_cutoff,
                maturity_margin=maturity_margin,
                sources=CATBOOST_SOURCES,
                gate_meta=gate_meta,
                split_readiness=split_readiness,
            )

        logger.info("Iniciando train_challengers (CatBoost only)...")
        result = await svc.train_challengers(
            db=db,
            user_id=USER_ID,
            enable_lightgbm=False,
            enable_catboost=True,
            catboost_source_filter=CATBOOST_SOURCES,
            allow_mixed_source=False,
            advisory_intelligence=ADVISORY_INTELLIGENCE,
            win_fast_threshold_s=WIN_THRESHOLD_S,
            lookback_days=90,
        )

        logger.info("Resultado: %s", result)
        return result

    await engine.dispose()


if __name__ == "__main__":
    result = asyncio.run(main())
    import json
    print("\n=== RESULTADO FINAL ===")
    print(json.dumps(result, indent=2, default=str))
