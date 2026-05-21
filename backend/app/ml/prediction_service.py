import logging
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.ml.feature_extractor import FEATURE_COLUMNS, extract_features
from app.ml.gcs_model_loader import get_model

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

        # Extrai e vetoriza features.
        # Task #324 — preserve NaN. XGBoost was treinado com missing=nan;
        # default 0.0 colapsaria "ausente" e "zero real" (ex.: taker_ratio=0
        # = 100% venda) e o runtime divergiria do treino.
        features = extract_features(metrics)
        _nan = float("nan")
        X = np.array(
            [[features.get(f, _nan) for f in FEATURE_COLUMNS]],
            dtype="float32",
        )

        # Predição
        proba = float(model.predict_proba(X)[0][1])
        approved = proba >= threshold

        result = {
            "win_fast_probability": round(proba, 4),
            "model_approved":       approved,
            "threshold_used":       threshold,
            "model_id":             model_id,
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
