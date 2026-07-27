"""Compatibility entrypoint for the governed L1 retrain.

The filename is retained for operators with old runbooks. The active algorithm
and split pattern come from ``ml_l1_training_pattern`` in the ML configuration.
Não requer rede interna Railway. Não promove modelo automaticamente.
Não altera outcomes, shadow trades, estratégias ou Auto-Pilot.

Uso:
    $env:DATABASE_URL="postgresql+asyncpg://..."
    python backend/scripts/run_lgbm_retrain.py [--dry-run]

Flags:
    --dry-run   Roda treino mas não persiste no banco (default: False)
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

# ---------------------------------------------------------------------------
# Path setup — permite imports relativos do backend
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

# Optuna silencioso
os.environ.setdefault("OPTUNA_VERBOSITY", "WARNING")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("xgboost_l1_retrain")

DRY_RUN = "--dry-run" in sys.argv

USER_ID = UUID("8080110c-ee9d-4a2b-a53f-6bef86dd8867")
# Fase 1 B.3 — win threshold vem EXCLUSIVAMENTE da config ml ativa
# (ml_win_fast_threshold_seconds). Valor hardcoded aqui causou o v80 (14400
# contra contrato canônico 1800).
LOOKBACK_DAYS = int(os.getenv("ML_CHALLENGER_LOOKBACK_DAYS", "60"))


def _resolve_database_url() -> str:
    db_url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_PUBLIC_URL")
    if not db_url:
        raise RuntimeError(
            "missing_DATABASE_URL: defina DATABASE_URL ou DATABASE_PUBLIC_URL antes do retreino"
        )
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return db_url


async def main():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    db_url = _resolve_database_url()
    engine = create_async_engine(db_url, pool_pre_ping=True)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    logger.info("Connecting to DB: %s", db_url.split("@")[-1])
    logger.info("DRY_RUN=%s  USER_ID=%s  (win threshold: config ml_win_fast_threshold_seconds)", DRY_RUN, USER_ID)

    async with AsyncSessionLocal() as db:
        from backend.app.services.ml_challenger_service import (
            MLChallengerService,
            _filter_l3_barrier_contract,
        )
        from backend.app.ml.dataset_config import parse_required_ml_dataset_valid_from

        svc = MLChallengerService()

        if DRY_RUN:
            logger.info("[DRY-RUN] Carregando dataset L1_SPECTRUM apenas — sem treino nem persistência")
            ml_config = await svc._load_ml_config(db)
            dataset_valid_from = parse_required_ml_dataset_valid_from(ml_config)
            min_required = int(
                ml_config["ml_xgboost_l1_retrain_min_eligible_rows"]
            )
            dataset_query_cutoff = datetime.now(timezone.utc)
            maturity_embargo_margin_minutes = ml_config.get(
                "ml_maturity_embargo_margin_minutes"
            )
            records = await svc._load_shadow_data(
                db,
                USER_ID,
                lookback_days=LOOKBACK_DAYS,
                source_filter=["L1_SPECTRUM"],
                dataset_valid_from=dataset_valid_from,
                dataset_query_cutoff=dataset_query_cutoff,
                maturity_embargo_margin_minutes=maturity_embargo_margin_minutes,
                collect_diagnostics=True,
            )
            strategy_tp_pct = await svc._load_strategy_tp_pct(db, USER_ID)
            records, barrier_meta = _filter_l3_barrier_contract(
                records,
                expected_mode=str(ml_config.get("shadow_barrier_mode") or "FIXED"),
                expected_tp_pct=strategy_tp_pct,
            )
            logger.info("[DRY-RUN] Records carregados: %d", len(records))
            if len(records) < min_required:
                logger.info("[DRY-RUN] Marco nÃ£o atingido: %d < %d", len(records), min_required)
                return {
                    "dry_run": True,
                    "status": "skipped",
                    "reason": "insufficient_retrain_eligible_rows",
                    "records": len(records),
                    "min_required": min_required,
                    "dataset_valid_from": str(dataset_valid_from),
                    "dataset_query_cutoff": dataset_query_cutoff.isoformat(),
                    "maturity_embargo_margin_minutes": maturity_embargo_margin_minutes,
                    "maturity_diagnostics": svc._last_shadow_load_diagnostics,
                    "barrier_contract": barrier_meta,
                }
            from backend.app.ml.feature_extractor import FEATURE_COLUMNS
            _win_threshold_s = float(ml_config["ml_win_fast_threshold_seconds"])
            _label_objective = str(
                ml_config.get("ml_label_objective") or "fast_tp"
            )
            _training_pattern = str(
                ml_config.get("ml_l1_training_pattern") or "xgboost"
            ).strip().lower()
            if _training_pattern not in {"xgboost", "catboost_l3"}:
                raise ValueError(
                    "unsupported_ml_l1_training_pattern:"
                    f"{_training_pattern}"
                )
            feature_columns = list(FEATURE_COLUMNS)
            if bool(ml_config.get("ml_feature_exclusion_apply")):
                exclusions = {
                    str(item)
                    for item in (
                        ml_config.get(
                            "ml_feature_exclusion_candidates_proposed"
                        )
                        or []
                    )
                }
                feature_columns = [
                    column
                    for column in feature_columns
                    if column not in exclusions
                ]
            lane_contract = (
                ml_config.get("ml_feature_contract") or {}
            ).get("L1_SPECTRUM")
            (
                X,
                y,
                cols,
                returns,
                created_at,
                shadow_ids,
                holding_seconds,
                snapshot_keys,
            ) = svc._build_dataset(
                records,
                feature_columns,
                _win_threshold_s,
                lane_contract=lane_contract,
                feature_ranges=ml_config.get("ml_feature_ranges"),
                backfilled_feature_names=ml_config.get(
                    "ml_backfilled_feature_names"
                ),
                backfill_marker_key=ml_config.get(
                    "ml_backfill_marker_key"
                ),
                label_objective=_label_objective,
            )
            pos_rate = float(y.mean()) if len(y) else 0
            logger.info("[DRY-RUN] Dataset: rows=%d features=%d positive_rate=%.2f%%", len(y), len(cols), pos_rate * 100)
            split_diagnostics = None
            if _training_pattern == "catboost_l3":
                split = svc._chronological_split_with_embargo(
                    X,
                    y,
                    metadata=[
                        returns,
                        created_at,
                        shadow_ids,
                        snapshot_keys,
                    ],
                    created_at=created_at,
                    holding_seconds=holding_seconds,
                    group_ids=snapshot_keys,
                    embargo_seconds=int(
                        ml_config["ml_split_embargo_seconds"]
                    ),
                    min_train_size=min_required,
                    min_validation_size=int(
                        ml_config["ml_threshold_min_positives"]
                    ),
                    min_test_size=int(
                        ml_config["ml_promotion_min_test_samples"]
                    ),
                    max_boundary_candidates=int(
                        ml_config[
                            "ml_l1_split_max_boundary_candidates"
                        ]
                    ),
                )
                split_diagnostics = split["split_diagnostics"]
            return {
                "dry_run": True,
                "records": len(records),
                "rows": len(y),
                "pos_rate": round(pos_rate, 4),
                "training_pattern": _training_pattern,
                "split_diagnostics": split_diagnostics,
                "dataset_query_cutoff": dataset_query_cutoff.isoformat(),
                "maturity_embargo_margin_minutes": maturity_embargo_margin_minutes,
                "maturity_diagnostics": svc._last_shadow_load_diagnostics,
                "barrier_contract": barrier_meta,
            }

        logger.info(
            "Iniciando train_challengers (L1 only; pattern from "
            "config ml_l1_training_pattern)..."
        )
        result = await svc.train_challengers(
            db=db,
            user_id=USER_ID,
            enable_xgboost_l1=True,
            enable_xgboost_l3=False,
        )

        logger.info("Resultado: %s", result)
        return result

    await engine.dispose()


if __name__ == "__main__":
    result = asyncio.run(main())
    import json
    print("\n=== RESULTADO FINAL ===")
    print(json.dumps(result, indent=2, default=str))
