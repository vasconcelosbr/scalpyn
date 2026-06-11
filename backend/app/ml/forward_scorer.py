"""Passive forward scorer — scores new shadow trades at creation time.

ISOLATION INVARIANT: this module only WRITES to ml_predictions. It never
reads ml_predictions and is never called from any decision-making path.
Controlled by ml_forward_scoring_enabled in config_profiles type='ml'.

Purpose: accumulate genuine out-of-sample predictions so that when scored
shadows close, we have a forward AUC computed without data leakage. Even a
weak smoke-train model is valuable here — the plumbing matters, not the AUC.
Each retrain produces a new model_id, keeping prediction histories distinct.
"""
from __future__ import annotations

import logging
from typing import Any, Dict
from uuid import UUID

import numpy as np

logger = logging.getLogger(__name__)


async def safe_score_shadow_trade(
    shadow_trade_id: UUID,
    features_snapshot: Dict[str, Any],
    symbol: str,
) -> None:
    """Score a shadow trade and record the prediction in ml_predictions.

    Opens its own DB session (fire-and-forget). Never raises. Silently
    returns when: feature flag is off, no active model, or any error.

    Args:
        shadow_trade_id: UUID of the newly created shadow trade.
        features_snapshot: Flat indicator dict from _build_features_snapshot.
        symbol: Trading pair symbol (e.g. 'BTC_USDT').
    """
    try:
        from ..database import CeleryAsyncSessionLocal
        from .gcs_model_loader import get_model
        from .feature_extractor import FEATURE_COLUMNS, extract_features
        from sqlalchemy import text as _t

        async with CeleryAsyncSessionLocal() as db:
            # 1. Feature flag — off by default; set true after smoke train passes
            cfg_row = await db.execute(_t("""
                SELECT config_json->>'ml_forward_scoring_enabled'
                FROM config_profiles
                WHERE config_type = 'ml' AND is_active = true
                LIMIT 1
            """))
            cfg = cfg_row.fetchone()
            if not cfg or str(cfg[0] or "").lower() != "true":
                return

            # 2. Load model from DB blob (singleton, 5-min cache)
            try:
                model = get_model()
            except Exception as _me:
                logger.debug("[forward_scorer] model unavailable: %s", _me)
                return

            if model is None:
                return

            # 3. Active model metadata
            mr = await db.execute(_t("""
                SELECT id::text, decision_threshold
                FROM ml_models WHERE status = 'active'
                ORDER BY version DESC LIMIT 1
            """))
            model_row = mr.fetchone()
            if not model_row:
                return
            model_id, threshold = model_row[0], float(model_row[1] or 0.5)

            # 4. Feature extraction → inference vector
            features = extract_features(features_snapshot or {})
            nan = float("nan")
            X = np.array(
                [[features.get(f, nan) for f in FEATURE_COLUMNS]],
                dtype="float32",
            )
            # Compat with models trained on fewer features (pre-macro)
            expected = getattr(model, "n_features_in_", None)
            if expected is not None and X.shape[1] > expected:
                X = X[:, :expected]
            elif expected is not None and X.shape[1] < expected:
                pad = expected - X.shape[1]
                X = np.concatenate(
                    [X, np.zeros((1, pad), dtype="float32")], axis=1
                )

            proba = float(model.predict_proba(X)[0][1])

            # 5. Write audit row — own transaction
            async with db.begin():
                await db.execute(_t("""
                    INSERT INTO ml_predictions
                        (model_id, shadow_trade_id, symbol,
                         win_fast_probability, model_approved, threshold_used)
                    VALUES
                        (:model_id, :shadow_trade_id, :symbol,
                         :probability, :approved, :threshold)
                """), {
                    "model_id":        model_id,
                    "shadow_trade_id": str(shadow_trade_id),
                    "symbol":          symbol,
                    "probability":     proba,
                    "approved":        proba >= threshold,
                    "threshold":       threshold,
                })

            logger.debug(
                "[forward_scorer] scored shadow=%s symbol=%s prob=%.4f",
                shadow_trade_id, symbol, proba,
            )

    except Exception as exc:
        logger.warning(
            "[forward_scorer] safe_score_shadow_trade failed id=%s: %s",
            shadow_trade_id, exc,
        )
