"""ML API endpoints for model training, evaluation, and prediction."""

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
from ..ml.train_model import train_model_pipeline
from ..ml.evaluation_report import generate_evaluation_report
from ..ml.predict_service import get_predict_service
from ..ml.model_loader import get_model_loader

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

        # Start training
        result = await train_model_pipeline(
            db=db,
            min_date=request.min_date,
            max_date=request.max_date,
            model_name=request.model_name,
            params=request.params,
        )

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

        # Get prediction service
        service = get_predict_service(
            model_path=ai_settings.get("model_path", "/tmp/scalpyn_models/model.pkl")
        )

        if not service.is_ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Model not loaded",
            )

        # Predict
        result = service.predict_best_direction(
            features=request.features,
            profile_type=request.profile_type,
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

        # Get prediction service
        service = get_predict_service(
            model_path=ai_settings.get("model_path", "/tmp/scalpyn_models/model.pkl")
        )

        if not service.is_ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Model not loaded",
            )

        # Batch predict
        results = service.batch_predict_for_l3(
            assets=request.assets,
            profile_type=request.profile_type,
            ai_block_threshold=ai_settings.get("ai_block_threshold", 0.5),
        )

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

        # Reload model
        loader = get_model_loader(model_path=request.model_path, reload=True)

        if not loader.is_loaded():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to load model",
            )

        metadata = loader.get_metadata()

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
        model_path = ai_settings.get("model_path", "/tmp/scalpyn_models/model.pkl")

        # Check if model is loaded
        service = get_predict_service(model_path=model_path)
        model_loaded = service.is_ready()

        metadata = None
        if model_loaded and service.loader:
            metadata = service.loader.get_metadata()

        return {
            "status": "success",
            "ml_enabled": ml_enabled,
            "model_loaded": model_loaded,
            "model_path": model_path,
            "metadata": metadata,
            "settings": ai_settings,
        }

    except Exception as e:
        logger.exception(f"Status check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Status check failed: {str(e)}",
        )
