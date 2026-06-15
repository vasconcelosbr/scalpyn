"""ML API endpoints for model training, evaluation, and prediction.

Heavy ML modules (train_model, evaluation_report, predict_service,
model_loader) are imported lazily inside each handler so this module's
top-level import does not transitively load xgboost / scikit-learn / joblib.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional, List
from uuid import UUID
from pydantic import BaseModel
import logging

import jwt as pyjwt

from ..config import settings
from ..database import get_db
from ..services.config_service import config_service

logger = logging.getLogger(__name__)

security = HTTPBearer()

router = APIRouter(prefix="/api/ml", tags=["Machine Learning"])


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> UUID:
    """Extract user ID from JWT token."""
    token = credentials.credentials
    try:
        payload = pyjwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type"
            )
        return UUID(payload["sub"])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )


# Request/Response schemas
class TrainRequest(BaseModel):
    """Request to train a new model."""

    min_date: Optional[str] = None
    max_date: Optional[str] = None
    model_name: str = "model.pkl"
    params: Optional[Dict[str, Any]] = None


class EvaluateRequest(BaseModel):
    """Request to evaluate a model."""

    model_path: str = "/tmp/scalpyn_models/model.pkl"
    min_date: Optional[str] = None
    max_date: Optional[str] = None
    save_report: bool = False
    output_path: Optional[str] = None


class PredictRequest(BaseModel):
    """Request for single prediction."""

    features: Dict[str, Any]
    profile_type: str = "FUTURES"


class BatchPredictRequest(BaseModel):
    """Request for batch prediction."""

    assets: List[Dict[str, Any]]
    profile_type: str = "FUTURES"


class ReloadModelRequest(BaseModel):
    """Request to reload model."""

    model_path: str = "/tmp/scalpyn_models/model.pkl"


@router.post("/train")
async def train_model(
    request: TrainRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Train a new XGBoost model.

    This endpoint starts model training in the background.
    Training can take several minutes depending on dataset size.
    """
    try:
        logger.info(f"Training model requested by user {user_id}")

        # Check AI settings
        ai_settings = await config_service.get_config(
            db=db, config_type="ai-settings", user_id=user_id
        )

        if not ai_settings.get("ml_enabled", True):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ML is disabled in settings",
            )

        # Audit Sprint 4: migrated from legacy train_model.py to production
        # WinFastTrainer pipeline (same as Cloud Run Job).
        from ..ml.feature_extractor import build_training_dataframe, FEATURE_COLUMNS
        from ..ml.trainer import WinFastTrainer
        from sqlalchemy import text as _text

        # Fetch shadow_trades
        days_lookback = 30
        rows = await db.execute(_text("""
            SELECT symbol, source, pnl_pct, net_return_pct, holding_seconds,
                   outcome, features_snapshot, created_at
            FROM shadow_trades
            WHERE outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
              AND pnl_pct IS NOT NULL
              AND features_snapshot IS NOT NULL
              AND features_snapshot::text <> '{}'
              AND created_at >= NOW() - INTERVAL :days
            ORDER BY created_at ASC
        """), {"days": f"{days_lookback} days"})
        records = [dict(r._mapping) for r in rows.fetchall()]

        if len(records) < 100:
            raise ValueError(f"Insufficient data: {len(records)} records (min 100)")

        df = build_training_dataframe(records)
        trainer = WinFastTrainer(n_trials=20)  # fewer trials for API-triggered training
        result = trainer.train(df)

        return {
            "status": "success",
            "message": "Model training completed",
            "result": result,
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.exception(f"Training failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Training failed: {str(e)}",
        )


@router.post("/evaluate")
async def evaluate_model(
    request: EvaluateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Evaluate a trained model.

    Returns comprehensive metrics including AUC, precision, recall,
    feature importance, and win rate by probability bucket.
    """
    try:
        logger.info(f"Model evaluation requested by user {user_id}")

        from ..ml.evaluation_report import generate_evaluation_report

        report = await generate_evaluation_report(
            db=db,
            model_path=request.model_path,
            min_date=request.min_date,
            max_date=request.max_date,
            output_path=request.output_path if request.save_report else None,
        )

        return {
            "status": "success",
            "report": report,
        }

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.exception(f"Evaluation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Evaluation failed: {str(e)}",
        )


@router.post("/predict")
async def predict(
    request: PredictRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Get prediction for a single asset.

    Returns probability, direction, and final score.
    """
    try:
        # Get AI settings
        ai_settings = await config_service.get_config(
            db=db, config_type="ai-settings", user_id=user_id
        )

        if not ai_settings.get("ml_enabled", True):
            return {
                "status": "disabled",
                "message": "ML is disabled",
                "probability": 1.0,
                "direction": "LONG",
            }

        from ..ml.prediction_service import predictor as _win_fast_predictor

        result = await _win_fast_predictor.predict(
            metrics=request.features,
            db=db,
        )

        return {
            "status": "success",
            "prediction": result,
        }

    except Exception as e:
        logger.exception(f"Prediction failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Prediction failed: {str(e)}",
        )


@router.post("/predict/batch")
async def predict_batch(
    request: BatchPredictRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Get predictions for multiple assets.

    Returns sorted list with probabilities and final scores.
    """
    try:
        # Get AI settings
        ai_settings = await config_service.get_config(
            db=db, config_type="ai-settings", user_id=user_id
        )

        if not ai_settings.get("ml_enabled", True):
            return {
                "status": "disabled",
                "message": "ML is disabled",
                "predictions": request.assets,
            }

        from ..ml.prediction_service import predictor as _win_fast_predictor

        ai_block_threshold = ai_settings.get("ai_block_threshold", 0.5)

        results = []
        for asset in request.assets:
            try:
                pred = await _win_fast_predictor.predict(
                    metrics=asset.get("features", asset),
                    db=db,
                    symbol=asset.get("symbol"),
                )
                results.append({**asset, "ml": pred})
            except Exception:
                results.append(asset)

        return {
            "status": "success",
            "predictions": results,
        }

    except Exception as e:
        logger.exception(f"Batch prediction failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Batch prediction failed: {str(e)}",
        )


@router.post("/reload")
async def reload_model(
    request: ReloadModelRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Reload model from disk.

    Use this after training a new model to load it into memory.
    """
    try:
        logger.info(f"Model reload requested by user {user_id}")

        # Audit Sprint 4: use production gcs_model_loader
        from ..ml.gcs_model_loader import invalidate_model_cache, get_model

        invalidate_model_cache()
        try:
            model = get_model()
            metadata = {
                "loaded": True,
                "n_features": getattr(model, 'n_features_in_', None),
            }
        except Exception as load_err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to load model: {load_err}",
            )

        return {
            "status": "success",
            "message": "Model reloaded",
            "metadata": metadata,
        }

    except Exception as e:
        logger.exception(f"Model reload failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Model reload failed: {str(e)}",
        )


@router.get("/models")
async def list_ml_models(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """List all ml_models rows ordered by version descending."""
    from sqlalchemy import text as sa_text

    rows = await db.execute(sa_text("""
        SELECT
            id, version, status,
            hyperparams,
            train_samples, val_samples, test_samples,
            precision_score, recall_score, f1_score, roc_auc,
            win_fast_capture_rate, false_positive_rate,
            ev_score, comparison_vs_previous,
            train_from, train_to,
            model_path, decision_threshold,
            activated_at, retired_at, notes
        FROM ml_models
        ORDER BY version DESC
    """))
    models = []
    for r in rows.mappings():
        models.append({
            "id":                       str(r["id"]),
            "version":                  r["version"],
            "status":                   r["status"],
            "hyperparams":              r["hyperparams"],
            "train_samples":            r["train_samples"],
            "val_samples":              r["val_samples"],
            "test_samples":             r["test_samples"],
            "precision_score":          r["precision_score"],
            "recall_score":             r["recall_score"],
            "f1_score":                 r["f1_score"],
            "roc_auc":                  r["roc_auc"],
            "win_fast_capture_rate":    r["win_fast_capture_rate"],
            "false_positive_rate":      r["false_positive_rate"],
            "ev_score":                 r["ev_score"],
            "comparison_vs_previous":   r["comparison_vs_previous"],
            "train_from":               r["train_from"].isoformat() if r["train_from"] else None,
            "train_to":                 r["train_to"].isoformat() if r["train_to"] else None,
            "model_path":               r["model_path"],
            "decision_threshold":       r["decision_threshold"],
            "activated_at":             r["activated_at"].isoformat() if r["activated_at"] else None,
            "retired_at":               r["retired_at"].isoformat() if r["retired_at"] else None,
            "notes":                    r["notes"],
        })
    return {"models": models}


@router.get("/status")
async def get_ml_status(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """
    Get ML system status.

    Returns whether ML is enabled, model loaded, and metadata.
    """
    try:
        # Get AI settings
        ai_settings = await config_service.get_config(
            db=db, config_type="ai-settings", user_id=user_id
        )

        ml_enabled = ai_settings.get("ml_enabled", True)

        from ..ml.gcs_model_loader import get_model as _get_gcs_model
        from sqlalchemy import text as _text

        # Check active model registered by Cloud Run Job
        active_model = None
        try:
            row = (await db.execute(_text(
                "SELECT id, version, decision_threshold, model_path, activated_at "
                "FROM ml_models WHERE status = 'active' LIMIT 1"
            ))).fetchone()
            if row:
                active_model = {
                    "id": str(row.id),
                    "version": row.version,
                    "decision_threshold": float(row.decision_threshold) if row.decision_threshold else None,
                    "model_path": row.model_path,
                    "activated_at": row.activated_at.isoformat() if row.activated_at else None,
                }
        except Exception:
            pass

        # Check if GCS model is cached in memory
        model_loaded = False
        try:
            _get_gcs_model()
            model_loaded = True
        except Exception:
            pass

        return {
            "status": "success",
            "ml_enabled": ml_enabled,
            "model_loaded": model_loaded,
            "active_model": active_model,
            "settings": ai_settings,
        }

    except Exception as e:
        logger.exception(f"Status check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Status check failed: {str(e)}",
        )
