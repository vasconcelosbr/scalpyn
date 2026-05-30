import logging
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.ml.feature_extractor import FEATURE_COLUMNS, ML_EXCLUDED_FIELDS, extract_features
from app.ml.gcs_model_loader import get_model
from app.ml.macro_client import fetch_macro_context

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.500


class WinFastPredictor:
    """
    Preditor stateless — compatível com Cloud Run.
    Modelo vive no GCS, carregado via singleton em memória.
    Threshold lido do banco (Zero Hardcode).
    """

    async def _get_threshold(self, db: AsyncSession) -> tuple:
        """Busca model_id e threshold do modelo ativo."""
        result = await db.execute(text("""
            SELECT id, decision_threshold
            FROM ml_models
            WHERE status = 'active'
            LIMIT 1
        """))
        row = result.fetchone()
        if not row:
            return None, DEFAULT_THRESHOLD
        return str(row.id), float(row.decision_threshold)

    async def predict(
        self,
        metrics: dict,
        db: AsyncSession,
        decision_id: int | None = None,
        symbol: str | None = None,
    ) -> dict:
        """
        Prediz probabilidade WIN_FAST para um sinal L3.

        Returns:
            {
                "win_fast_probability": 0.73,
                "model_approved": True,
                "threshold_used": 0.50,
                "model_id": "uuid" | None
            }
        """
        # Carrega modelo (GCS cache)
        try:
            model = get_model()
        except Exception as e:
            logger.warning(f"Modelo indisponível: {e} — aprovando por padrão")
            return {
                "win_fast_probability": None,
                "model_approved": True,
                "threshold_used": None,
                "model_id": None,
                "reason": "model_unavailable",
            }

        # Threshold do banco
        model_id, threshold = await self._get_threshold(db)

        # ── Macro enrichment (Market Data Hub) ──────────────────────────────
        # Fetch global macro context concurrently. Never blocks inference on
        # failure — on timeout/error returns macro_context_available=False and
        # all numeric features as None (treated as NaN by XGBoost missing=nan).
        try:
            macro = await fetch_macro_context()
        except Exception as _macro_exc:
            logger.warning("[ML] macro_client failed: %s — proceeding without macro", _macro_exc)
            macro = {"macro_context_available": False}

        # Merge macro features into metrics (additive — never overwrite symbol indicators).
        # Macro keys are at the END of FEATURE_COLUMNS so they never collide with
        # existing indicator features (e.g. rsi, adx).
        if metrics is None:
            metrics = {}
        metrics = {**metrics, **macro}

        # Extrai e vetoriza features.
        # Task #324 — preserve NaN. XGBoost was treinado com missing=nan;
        # default 0.0 colapsaria "ausente" e "zero real" (ex.: taker_ratio=0
        # = 100% venda) e o runtime divergiria do treino.
        #
        # ML_EXCLUDED_FIELDS — strip leakage fields aqui também (defesa em
        # profundidade, espelha o filtro de extract_features para o caso de
        # callers que bypassem extract_features no futuro).
        if any(k in metrics for k in ML_EXCLUDED_FIELDS):
            metrics = {k: v for k, v in metrics.items() if k not in ML_EXCLUDED_FIELDS}
        features = extract_features(metrics)
        _nan = float("nan")
        # Assert defensivo: nenhum campo proibido pode acabar no vetor de inferência.
        assert not ML_EXCLUDED_FIELDS.intersection(FEATURE_COLUMNS), (
            "ML_EXCLUDED_FIELDS contaminou FEATURE_COLUMNS — abortar inferência."
        )
        X = np.array(
            [[features.get(f, _nan) for f in FEATURE_COLUMNS]],
            dtype="float32",
        )

        # Backwards-compat: models trained before macro features were added to
        # FEATURE_COLUMNS expect fewer columns. Truncate X to what the model
        # was trained with — macro features appear at the end of FEATURE_COLUMNS
        # so truncation is safe. When the model is retrained with macro data,
        # n_features_in_ matches len(FEATURE_COLUMNS) and this branch is skipped.
        expected_features = getattr(model, "n_features_in_", None)
        if expected_features is not None and X.shape[1] > expected_features:
            logger.info(
                "[ML] Model expects %d features, vector has %d — truncating macro features. "
                "Retrain model to enable macro enrichment in inference.",
                expected_features, X.shape[1],
            )
            X = X[:, :expected_features]
        elif expected_features is not None and X.shape[1] < expected_features:
            # Forward-compat: model was trained with INCLUDE_REJECTED_IN_TRAIN=true,
            # which appends 'was_rejected' as the last feature column in trainer.py.
            # During live inference all L3 candidates are non-rejected (was_rejected=0).
            n_pad = expected_features - X.shape[1]
            X = np.concatenate([X, np.zeros((1, n_pad), dtype="float32")], axis=1)
            logger.info(
                "[ML] Padded %d feature(s) with 0.0 (was_rejected=0 for L3 inference) "
                "to match model's %d expected features.",
                n_pad, expected_features,
            )

        # Predição
        proba = float(model.predict_proba(X)[0][1])
        approved = proba >= threshold

        result = {
            "win_fast_probability": round(proba, 4),
            "model_approved":       approved,
            "threshold_used":       threshold,
            "model_id":             model_id,
            # Macro context returned so callers can persist it to decisions_log.metrics
            # for future ML training without re-fetching. Internal flags stripped.
            "macro_context": {k: v for k, v in macro.items() if k != "macro_context_available"},
        }

        # Log assíncrono — não bloqueia o pipeline
        if decision_id and model_id:
            try:
                await db.execute(text("""
                    INSERT INTO ml_predictions
                        (model_id, decision_id, symbol,
                         win_fast_probability, model_approved, threshold_used)
                    VALUES
                        (:model_id, :decision_id, :symbol,
                         :probability, :approved, :threshold)
                """), {
                    "model_id":    model_id,
                    "decision_id": decision_id,
                    "symbol":      symbol or "UNKNOWN",
                    "probability": proba,
                    "approved":    approved,
                    "threshold":   threshold,
                })
                await db.commit()
            except Exception as e:
                logger.warning(f"Erro ao logar predição: {e}")

        return result


# Instância global stateless
predictor = WinFastPredictor()
