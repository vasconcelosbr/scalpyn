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
        """Retorna modelo — carrega do GCS se cache expirado.

        Uses AND instead of OR so a cached failure (_model=None but _loaded_at set)
        does not trigger a retry on every call until MODEL_CACHE_TTL expires.
        """
        now = time.time()
        if (now - self._loaded_at) > MODEL_CACHE_TTL:
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
            # Cache the failure timestamp so we don't retry GCS on every request.
            # Without this, _loaded_at stays at 0.0 and _load_from_gcs is called
            # on every get_model() invocation when running without GCS credentials.
            self._loaded_at = time.time()
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
