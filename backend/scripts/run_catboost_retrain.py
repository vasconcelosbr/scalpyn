"""Standalone CatBoost retrain script — usa URL pública do banco.

Treina Lane 2 (CatBoost / L3_PROFILE / is_tp_4h_v1) e persiste em ml_models.
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
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:pfVYvunFISWEeWAytUNApAAbxtsNcEHM@zephyr.proxy.rlwy.net:23422/railway",
)
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
CATBOOST_SOURCES = ["L3_LAB"]


async def main():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker

    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url, pool_pre_ping=True)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    logger.info("DB: %s", db_url.split("@")[-1])
    logger.info("DRY_RUN=%s  USER_ID=%s  WIN_THRESHOLD_S=%s  SOURCES=%s",
                DRY_RUN, USER_ID, WIN_THRESHOLD_S, CATBOOST_SOURCES)

    async with AsyncSessionLocal() as db:
        from backend.app.services.ml_challenger_service import MLChallengerService

        svc = MLChallengerService()

        if DRY_RUN:
            logger.info("[DRY-RUN] Carregando dataset L3_PROFILE apenas — sem treino nem persistencia")
            all_records = await svc._load_shadow_data(db, USER_ID, lookback_days=90, source_filter=CATBOOST_SOURCES)
            eligible = [r for r in all_records if r.get("profile_id")]
            logger.info("[DRY-RUN] Records: total=%d com_profile=%d", len(all_records), len(eligible))

            from backend.app.ml.feature_extractor import FEATURE_COLUMNS
            X, y, cols, cat_idx, *_ = svc._build_l3_dataset(eligible, list(FEATURE_COLUMNS), WIN_THRESHOLD_S)
            pos_rate = float(y.mean()) if len(y) else 0
            logger.info("[DRY-RUN] Dataset: rows=%d features=%d cat_idx=%s positive_rate=%.2f%%",
                        len(y), len(cols), cat_idx, pos_rate * 100)
            return {"dry_run": True, "eligible": len(eligible), "rows": len(y),
                    "features": len(cols), "pos_rate": round(pos_rate, 4)}

        logger.info("Iniciando train_challengers (CatBoost only)...")
        result = await svc.train_challengers(
            db=db,
            user_id=USER_ID,
            enable_lightgbm=False,
            enable_catboost=True,
            catboost_source_filter=CATBOOST_SOURCES,
            allow_mixed_source=False,
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
