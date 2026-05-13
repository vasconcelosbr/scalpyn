# =============================================================
# STEP 5 — Prediction Service (Cloud Run stateless)
# Modelo carregado do GCS no cold start
# Cache em memória durante a vida do container
# =============================================================

# -------------------------------------------------------------
# ARQUIVO: backend/app/ml/gcs_model_loader.py
# Responsável por carregar o modelo do GCS
# Cache em memória do processo (vive enquanto o container existir)
# -------------------------------------------------------------

import os
import io
import logging
import time
from threading import Lock
from typing import Optional

import joblib
import xgboost as xgb
from google.cloud import storage

logger = logging.getLogger(__name__)

BUCKET_NAME       = os.getenv("BUCKET_NAME", "scalpyn-mlflow")
MODEL_GCS_PATH    = "models/win_fast_latest.pkl"
MODEL_CACHE_TTL   = 300  # segundos — recarrega se modelo novo for deployado


class GCSModelLoader:
    """
    Singleton que carrega e cacheia o modelo XGBoost do GCS.
    
    Comportamento:
    - Cold start: baixa win_fast_latest.pkl do GCS (~1-2s)
    - Requests subsequentes: usa cache em memória (<1ms)
    - Cache expira em 5min → detecta novo modelo automaticamente
    """

    _instance: Optional["GCSModelLoader"] = None
    _lock = Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._model = None
                cls._instance._loaded_at = 0.0
                cls._instance._model_version = None
        return cls._instance

    def get_model(self):
        """Retorna modelo — carrega do GCS se cache expirado."""
        now = time.time()
        if self._model is None or (now - self._loaded_at) > MODEL_CACHE_TTL:
            self._load_from_gcs()
        return self._model

    def _load_from_gcs(self):
        """Download e deserialização do modelo do GCS."""
        logger.info(f"Carregando modelo do GCS: gs://{BUCKET_NAME}/{MODEL_GCS_PATH}")
        t0 = time.time()

        try:
            client = storage.Client()
            bucket = client.bucket(BUCKET_NAME)
            blob = bucket.blob(MODEL_GCS_PATH)

            buffer = io.BytesIO()
            blob.download_to_file(buffer)
            buffer.seek(0)

            self._model = joblib.load(buffer)
            self._loaded_at = time.time()

            elapsed = round((time.time() - t0) * 1000, 1)
            logger.info(f"Modelo carregado em {elapsed}ms")

        except Exception as e:
            logger.error(f"Erro ao carregar modelo do GCS: {e}")
            # Mantém modelo anterior se existir
            if self._model is None:
                raise

    def invalidate(self):
        """Força reload no próximo request."""
        self._model = None
        self._loaded_at = 0.0
        logger.info("Cache do modelo invalidado.")


# Instância global
_loader = GCSModelLoader()


def get_model():
    """Função de conveniência — use em qualquer lugar."""
    return _loader.get_model()


def invalidate_model_cache():
    _loader.invalidate()


# -------------------------------------------------------------
# ARQUIVO: backend/app/ml/prediction_service.py (versão GCP)
# Substitui a versão anterior — usa GCS loader
# -------------------------------------------------------------

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

    async def _get_threshold(self, db: AsyncSession) -> tuple[str | None, float]:
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

        # Extrai e vetoriza features
        features = extract_features(metrics)
        X = np.array([[features.get(f, 0.0) for f in FEATURE_COLUMNS]])

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
