"""Standalone LightGBM retrain script — usa URL pública do banco.

Treina Lane 1 (LightGBM / L1_SPECTRUM / is_tp_4h_v1) e persiste em ml_models.
Não requer rede interna Railway. Não promove modelo automaticamente.
Não altera outcomes, shadow trades, estratégias ou Auto-Pilot.

Uso:
    python backend/scripts/run_lgbm_retrain.py [--dry-run]

Flags:
    --dry-run   Roda treino mas não persiste no banco (default: False)
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from uuid import UUID

# ---------------------------------------------------------------------------
# Path setup — permite imports relativos do backend
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:pfVYvunFISWEeWAytUNApAAbxtsNcEHM@zephyr.proxy.rlwy.net:23422/railway",
)
# Optuna silencioso
os.environ.setdefault("OPTUNA_VERBOSITY", "WARNING")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("lgbm_retrain")

DRY_RUN = "--dry-run" in sys.argv

USER_ID = UUID("8080110c-ee9d-4a2b-a53f-6bef86dd8867")
WIN_THRESHOLD_S = 14400.0  # is_tp_4h_v2_sim_outcome


async def main():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url, pool_pre_ping=True)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    logger.info("Connecting to DB: %s", db_url.split("@")[-1])
    logger.info("DRY_RUN=%s  USER_ID=%s  WIN_THRESHOLD_S=%s", DRY_RUN, USER_ID, WIN_THRESHOLD_S)

    async with AsyncSessionLocal() as db:
        from backend.app.services.ml_challenger_service import MLChallengerService

        svc = MLChallengerService()

        if DRY_RUN:
            logger.info("[DRY-RUN] Carregando dataset L1_SPECTRUM apenas — sem treino nem persistência")
            records = await svc._load_shadow_data(db, USER_ID, lookback_days=60, source_filter=["L1_SPECTRUM"])
            logger.info("[DRY-RUN] Records carregados: %d", len(records))
            from backend.app.ml.feature_extractor import FEATURE_COLUMNS
            X, y, cols, *_ = svc._build_dataset(records, list(FEATURE_COLUMNS), WIN_THRESHOLD_S)
            pos_rate = float(y.mean()) if len(y) else 0
            logger.info("[DRY-RUN] Dataset: rows=%d features=%d positive_rate=%.2f%%", len(y), len(cols), pos_rate * 100)
            return {"dry_run": True, "records": len(records), "rows": len(y), "pos_rate": round(pos_rate, 4)}

        logger.info("Iniciando train_challengers (LightGBM only)...")
        result = await svc.train_challengers(
            db=db,
            user_id=USER_ID,
            enable_lightgbm=True,
            enable_catboost=False,
            win_fast_threshold_s=WIN_THRESHOLD_S,
        )

        logger.info("Resultado: %s", result)
        return result

    await engine.dispose()


if __name__ == "__main__":
    result = asyncio.run(main())
    import json
    print("\n=== RESULTADO FINAL ===")
    print(json.dumps(result, indent=2, default=str))
