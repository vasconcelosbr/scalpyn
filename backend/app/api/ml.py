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
from ..services.crypto_ev_score_service import crypto_ev_score_service

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
    # Audit P2-5: explicit lane avoids ambiguous model selection when more
    # than one lane has an active+approved model simultaneously. None keeps
    # the legacy lane-agnostic behavior for existing diagnostic callers.
    model_lane: Optional[str] = None


class BatchPredictRequest(BaseModel):
    """Request for batch prediction."""

    assets: List[Dict[str, Any]]
    profile_type: str = "FUTURES"
    model_lane: Optional[str] = None


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


@router.get("/models/health")
async def get_ml_models_health(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    crypto_ev_config = await config_service.get_config(db, "crypto_ev", user_id)
    if not crypto_ev_config:
        return {
            "crypto_ev_ml_component": {
                "healthy": False,
                "reason": "crypto_ev_config_missing",
            }
        }
    health = await crypto_ev_score_service.ml_component_health(db, crypto_ev_config)
    return {"crypto_ev_ml_component": health}


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
            model_lane=request.model_lane,
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
                    model_lane=request.model_lane,
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
    from ..ml.dataset_policy import governance_flags_for_model

    rows = await db.execute(sa_text("""
        SELECT
            id, version, status,
            hyperparams,
            train_samples, val_samples, test_samples,
            precision_score, recall_score, f1_score, roc_auc,
            win_fast_capture_rate, false_positive_rate,
            train_from, train_to,
            model_path, decision_threshold,
            activated_at, retired_at, notes,
            feature_columns_json, feature_columns_hash,
            feature_count, feature_schema_version,
            dataset_query_cutoff,
            label_version, metrics_json, target_window_seconds,
            model_lane, source_filter,
            dataset_contract_id, label_contract_id, feature_contract_id,
            descriptive_status, predictive_status,
            calibration_authority, rule_generation_authority,
            autopilot_authority, execution_authority, governance_reason
        FROM ml_models
        ORDER BY version DESC
    """))
    models = []
    for r in rows.mappings():
        hp = r["hyperparams"] or {}
        mj = r["metrics_json"] or {}
        gov = governance_flags_for_model({
            "status":              r["status"],
            "model_lane":          r["model_lane"],
            "precision_score":     r["precision_score"],
            "recall_score":        r["recall_score"],
            "test_samples":        r["test_samples"],
            "feature_columns_json": r["feature_columns_json"],
            "metrics_json":        mj,
            "hyperparams":         hp,
            "predictive_status":   r["predictive_status"],
            "calibration_authority": bool(r["calibration_authority"]),
            "rule_generation_authority": bool(r["rule_generation_authority"]),
        })
        models.append({
            "id":                   str(r["id"]),
            "version":              r["version"],
            "status":               r["status"],
            "governance_warning":   gov["governance_warning"],
            "allowed_usage":        gov["allowed_usage"],
            "blocked_reasons":      gov["blocked_reasons"],
            "eligible_for_orchestrator": gov["eligible_for_orchestrator"],
            "eligible_for_autopilot":    gov["eligible_for_autopilot"],
            "eligible_for_allow_block":  gov["eligible_for_allow_block"],
            "hyperparams":          hp,
            "train_samples":        r["train_samples"],
            "val_samples":          r["val_samples"],
            "test_samples":         r["test_samples"],
            "precision_score":      r["precision_score"],
            "recall_score":         r["recall_score"],
            "f1_score":             r["f1_score"],
            "roc_auc":              r["roc_auc"],
            "win_fast_capture_rate": r["win_fast_capture_rate"],
            "false_positive_rate":  r["false_positive_rate"],
            "train_from":           r["train_from"].isoformat() if r["train_from"] else None,
            "train_to":             r["train_to"].isoformat() if r["train_to"] else None,
            "model_path":           r["model_path"],
            "decision_threshold":   r["decision_threshold"],
            "activated_at":         r["activated_at"].isoformat() if r["activated_at"] else None,
            "retired_at":           r["retired_at"].isoformat() if r["retired_at"] else None,
            "notes":                r["notes"],
            "model_lane":           r["model_lane"],
            "source_filter":        r["source_filter"],
            "dataset_contract_id":  str(r["dataset_contract_id"]) if r["dataset_contract_id"] else None,
            "label_contract_id":    str(r["label_contract_id"]) if r["label_contract_id"] else None,
            "feature_contract_id":  str(r["feature_contract_id"]) if r["feature_contract_id"] else None,
            "feature_columns_json":  r["feature_columns_json"],
            "feature_columns_hash":  r["feature_columns_hash"],
            "feature_count":         r["feature_count"],
            "feature_schema_version": r["feature_schema_version"],
            "dataset_query_cutoff":  r["dataset_query_cutoff"].isoformat() if r["dataset_query_cutoff"] else None,
            "label_version":         r["label_version"],
            "metrics_json":          mj,
            "target_window_seconds": r["target_window_seconds"],
            "descriptive_status":    r["descriptive_status"],
            "predictive_status":     r["predictive_status"],
            "calibration_authority": bool(r["calibration_authority"]),
            "rule_generation_authority": bool(r["rule_generation_authority"]),
            "autopilot_authority":   bool(r["autopilot_authority"]),
            "execution_authority":   bool(r["execution_authority"]),
            "governance_reason":     r["governance_reason"] or {},
        })
    return {"models": models}


@router.get("/models/eligible")
async def list_eligible_models(
    lane: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """List active models that have passed the Promotion Gate for a given lane.

    Audit P0-1/P2-5 fix — this is the query any future ML Opportunity Ranking
    job must use to pick a model, instead of "status='active' ORDER BY
    activated_at DESC LIMIT 1" without a lane/quality filter. Returns an empty
    list (not an error) when there's no eligible model — callers must handle
    that as NO_ELIGIBLE_MODEL_FOR_LANE, never fall back to a random model.
    """
    from sqlalchemy import text as sa_text

    if lane not in ("L1_SPECTRUM", "L3_PROFILE"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"lane inválida: {lane!r} — use L1_SPECTRUM ou L3_PROFILE",
        )

    rows = await db.execute(sa_text("""
        SELECT id, version, model_lane, label_version, metrics_json,
               decision_threshold, activated_at
        FROM ml_models
        WHERE status = 'active'
          AND model_lane = :lane
          AND (metrics_json->'promotion_gate'->>'status') = 'APPROVED'
        ORDER BY activated_at DESC NULLS LAST
    """), {"lane": lane})

    models = [
        {
            "id": str(r["id"]),
            "version": r["version"],
            "model_lane": r["model_lane"],
            "label_version": r["label_version"],
            "decision_threshold": r["decision_threshold"],
            "activated_at": r["activated_at"].isoformat() if r["activated_at"] else None,
            "promotion_gate": (r["metrics_json"] or {}).get("promotion_gate"),
        }
        for r in rows.mappings()
    ]
    return {"lane": lane, "eligible_models": models, "count": len(models)}


@router.get("/models/intelligence/approved")
async def list_approved_intelligence_models(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Return advisory models approved for explanations, never for execution."""
    from sqlalchemy import text as sa_text

    rows = await db.execute(sa_text("""
        SELECT id, version, model_lane, label_version, metrics_json,
               decision_threshold, created_at
        FROM ml_models
        WHERE model_lane IN (
            'L3_INTELLIGENCE',
            'L3_APPROVED_INTELLIGENCE',
            'L3_CONTEXTUAL_INTELLIGENCE'
        )
          AND predictive_status = 'PREDICTIVE_APPROVED_FOR_INTELLIGENCE'
          AND calibration_authority = true
          AND rule_generation_authority = true
          AND execution_authority = false
        ORDER BY created_at DESC
    """))
    models = []
    for row in rows.mappings():
        metrics = row["metrics_json"] or {}
        models.append({
            "id": str(row["id"]),
            "version": row["version"],
            "model_lane": row["model_lane"],
            "label_version": row["label_version"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "intelligence_gate": metrics.get("intelligence_gate"),
            "indicator_intelligence": metrics.get("indicator_intelligence"),
            "execution_authority": False,
        })
    return {"lane": "L3_INTELLIGENCE", "models": models, "count": len(models)}


@router.post("/models/{model_id}/evaluate-promotion-gate")
async def evaluate_model_promotion_gate(
    model_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Re-evaluate the Promotion Gate for a single model and persist the result
    into metrics_json.promotion_gate. Never changes `status` — this endpoint
    only computes and records eligibility; promoting/demoting status is a
    separate, deliberately unimplemented action (no auto-promotion exists
    anywhere in this codebase as of the 2026-06-24 audit)."""
    import json
    from sqlalchemy import text as sa_text
    from ..ml.promotion_gate import evaluate_promotion_gate, merge_promotion_gate_into_metrics_json
    from ..services.profile_intelligence_audit_service import log_pi_event

    row = (await db.execute(sa_text("""
        SELECT id, model_lane, label_version, source_filter, dataset_contract_id,
               label_contract_id, feature_contract_id,
               feature_count, test_samples, roc_auc, metrics_json,
               train_from, train_to, dataset_query_cutoff, dataset_hash
        FROM ml_models WHERE id = :id
    """), {"id": str(model_id)})).mappings().first()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Modelo não encontrado")

    cfg_row = (await db.execute(sa_text("""
        SELECT config_json FROM config_profiles
        WHERE config_type = 'ml' AND is_active = true
        LIMIT 1
    """))).first()
    promotion_config = cfg_row[0] if cfg_row and cfg_row[0] else {}

    before = dict(row["metrics_json"] or {})
    gate_result = evaluate_promotion_gate(dict(row), promotion_config=promotion_config)
    after = merge_promotion_gate_into_metrics_json(row["metrics_json"], gate_result)

    await db.execute(sa_text("""
        UPDATE ml_models SET metrics_json = :mj WHERE id = :id
    """), {"mj": json.dumps(after), "id": str(model_id)})
    await db.commit()

    await log_pi_event(
        db, user_id,
        event_type="ML_PROMOTION_GATE_EVALUATED",
        event_description=f"model_id={model_id} status={gate_result['status']}",
        before_json=before,
        after_json=after,
        diff_json={"promotion_gate": gate_result},
        actor_user_id=user_id,
    )
    await db.commit()

    return {"model_id": str(model_id), "promotion_gate": gate_result}


@router.post("/models/{model_id}/promote")
async def promote_ml_model(
    model_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Promote one candidate only after a fresh, fail-closed gate evaluation."""
    import json
    from sqlalchemy import text as sa_text

    from ..ml.model_governance import governance_from_gate
    from ..ml.promotion_gate import (
        APPROVED,
        evaluate_promotion_gate,
        merge_promotion_gate_into_metrics_json,
    )
    from ..services.profile_intelligence_audit_service import log_pi_event

    row = (await db.execute(sa_text("""
        SELECT id, status, model_lane, model_scope, profile_id,
               label_version, source_filter,
               dataset_contract_id, label_contract_id, feature_contract_id,
               feature_count, test_samples, roc_auc, metrics_json,
               train_from, train_to, dataset_query_cutoff, dataset_hash,
               predictive_status, calibration_authority,
               rule_generation_authority, execution_authority
        FROM ml_models
        WHERE id = :id
        FOR UPDATE
    """), {"id": str(model_id)})).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Modelo nao encontrado",
        )
    if row["model_lane"] not in {"L1_SPECTRUM", "L3_PROFILE"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Lane nao promovivel: {row['model_lane']}",
        )

    cfg_row = (await db.execute(sa_text("""
        SELECT config_json FROM config_profiles
        WHERE config_type = 'ml' AND is_active = true
        LIMIT 1
    """))).first()
    promotion_config = cfg_row[0] if cfg_row and cfg_row[0] else {}
    before = json.loads(json.dumps(dict(row), default=str))
    gate_result = evaluate_promotion_gate(
        dict(row), promotion_config=promotion_config
    )
    metrics_json = merge_promotion_gate_into_metrics_json(
        row["metrics_json"], gate_result
    )
    governance = governance_from_gate(
        descriptive_gate=None,
        predictive_gate=gate_result,
    )
    governance_reason = {
        "promotion_gate_status": gate_result["status"],
        "promotion_gate_reasons": gate_result["reasons"],
    }
    await db.execute(sa_text("""
        UPDATE ml_models
        SET metrics_json = :metrics_json,
            descriptive_status = :descriptive_status,
            predictive_status = :predictive_status,
            calibration_authority = :calibration_authority,
            rule_generation_authority = :rule_generation_authority,
            autopilot_authority = :autopilot_authority,
            execution_authority = :execution_authority,
            governance_reason = :governance_reason
        WHERE id = :id
    """), {
        "id": str(model_id),
        "metrics_json": json.dumps(metrics_json),
        "governance_reason": json.dumps(governance_reason),
        **governance,
    })

    if gate_result["status"] != APPROVED:
        await log_pi_event(
            db,
            user_id,
            event_type="ML_MODEL_PROMOTION_BLOCKED",
            event_description=(
                f"model_id={model_id} gate={gate_result['status']}"
            ),
            before_json=before,
            after_json={"status": row["status"], **governance},
            diff_json={"promotion_gate": gate_result},
            actor_user_id=user_id,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "MODEL_PROMOTION_GATE_NOT_APPROVED",
                "promotion_gate": gate_result,
            },
        )

    if row["status"] == "active":
        await db.commit()
        return {
            "model_id": str(model_id),
            "status": "active",
            "promotion_gate": gate_result,
            "retired_models": 0,
            "idempotent": True,
        }
    if row["status"] != "candidate":
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Status nao promovivel: {row['status']}",
        )

    retired = await db.execute(sa_text("""
        UPDATE ml_models
        SET status = 'retired', retired_at = now()
        WHERE model_lane = :lane
          AND status = 'active'
          AND id <> :id
          AND COALESCE(model_scope, 'global') = COALESCE(:model_scope, 'global')
          AND profile_id IS NOT DISTINCT FROM CAST(:profile_id AS uuid)
        RETURNING id
    """), {
        "lane": row["model_lane"],
        "id": str(model_id),
        "model_scope": row["model_scope"],
        "profile_id": str(row["profile_id"]) if row["profile_id"] else None,
    })
    retired_ids = [str(item[0]) for item in retired.fetchall()]
    if retired_ids:
        await db.execute(sa_text("""
            UPDATE ml_model_registry
            SET status = 'retired', updated_at = now()
            WHERE source_ml_model_id = ANY(CAST(:retired_ids AS uuid[]))
        """), {"retired_ids": retired_ids})

    await db.execute(sa_text("""
        UPDATE ml_models
        SET status = 'active', activated_at = now(), retired_at = NULL
        WHERE id = :id
    """), {"id": str(model_id)})
    await db.execute(sa_text("""
        UPDATE ml_model_registry
        SET status = 'champion', updated_at = now()
        WHERE source_ml_model_id = :id
    """), {"id": str(model_id)})
    await log_pi_event(
        db,
        user_id,
        event_type="ML_MODEL_PROMOTED",
        event_description=f"model_id={model_id} lane={row['model_lane']}",
        before_json=before,
        after_json={"status": "active", **governance},
        diff_json={
            "promotion_gate": gate_result,
            "retired_model_ids": retired_ids,
        },
        actor_user_id=user_id,
    )
    await db.commit()
    return {
        "model_id": str(model_id),
        "status": "active",
        "promotion_gate": gate_result,
        "retired_models": len(retired_ids),
        "idempotent": False,
    }


@router.get("/catboost/readiness")
async def catboost_readiness(
    source: str = "L3",
    label_version: str = "is_tp_4h_v1",
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    """Evaluate whether the CatBoost dataset is ready for training.

    Parameters
    ----------
    source : str
        'L3' (→ L3_ONLY policy), 'L3_LAB' (→ L3_LAB_ONLY policy),
        or 'combined' (→ L3_COMBINED — always blocked).
    label_version : str
        e.g. 'is_tp_4h_v1'
    """
    from ..ml.dataset_policy import CatBoostReadinessGate, DatasetPolicy

    _POLICY_MAP = {
        "L3":       DatasetPolicy.L3_ONLY,
        "l3":       DatasetPolicy.L3_ONLY,
        "L3_LAB":   DatasetPolicy.L3_LAB_ONLY,
        "l3_lab":   DatasetPolicy.L3_LAB_ONLY,
        "L3_REJECTED": DatasetPolicy.L3_REJECTED_ONLY,
        "l3_rejected": DatasetPolicy.L3_REJECTED_ONLY,
        "combined": DatasetPolicy.L3_COMBINED,
        "L3_COMBINED": DatasetPolicy.L3_COMBINED,
    }

    _WIN_MAP = {
        "is_tp_4h_v1":    14400.0,
        "is_win_fast_v1": 1800.0,
    }

    policy = _POLICY_MAP.get(source, DatasetPolicy.L3_ONLY)
    win_s  = _WIN_MAP.get(label_version, 14400.0)

    gate = CatBoostReadinessGate()
    report = await gate.check(db, user_id, policy, label_version, win_s)
    return report.to_dict()


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


# ── Decision Orchestrator ──────────────────────────────────────────────────────

class OrchestratorBackfillRequest(BaseModel):
    limit: int = 300
    source_filter: Optional[List[str]] = None
    dry_run: bool = True
    profile_id: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    only_null_scores: bool = True


@router.post("/orchestrator/backfill")
async def orchestrator_backfill(
    request: OrchestratorBackfillRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Backfill final_priority_score + orchestrator_payload para shadow trades sem score.

    SEGURO POR PADRÃO: dry_run=True (retorna preview sem escrever no banco).

    ml_probability NÃO é sobrescrito — pertence ao modelo original da decisão.
    Os scores vão para orchestrator_payload JSONB + final_priority_score.

    Idempotente: só processa trades com final_priority_score IS NULL.
    """
    try:
        from ..services.decision_orchestrator import backfill_orchestrator_scores
        result = await backfill_orchestrator_scores(
            db=db,
            user_id=str(user_id),
            limit=request.limit,
            source_filter=request.source_filter,
            dry_run=request.dry_run,
            profile_id=request.profile_id,
            from_date=request.from_date,
            to_date=request.to_date,
            only_null_scores=request.only_null_scores,
        )
        return {"status": "success", **result}
    except Exception as exc:
        logger.exception("[Orchestrator] backfill falhou: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Orchestrator backfill failed: {exc}",
        )


@router.get("/orchestrator/status")
async def orchestrator_status(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Dict[str, Any]:
    """
    Retorna métricas de cobertura do final_priority_score (últimos 30 dias).
    """
    try:
        from ..services.decision_orchestrator import get_orchestrator_status
        return await get_orchestrator_status(db=db, user_id=str(user_id))
    except Exception as exc:
        logger.exception("[Orchestrator] status falhou: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
