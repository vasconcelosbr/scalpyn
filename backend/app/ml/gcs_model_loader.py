import os
import logging
import time
from threading import Lock
from typing import Optional

import joblib

logger = logging.getLogger(__name__)

MODEL_DIR      = os.getenv("MODEL_DIR", "/models")
MODEL_PATH     = os.path.join(MODEL_DIR, "win_fast_latest.pkl")
MODEL_CACHE_TTL = 300  # segundos — recarrega se modelo novo for treinado


class GCSModelLoader:
    """
    Singleton que carrega e cacheia o modelo XGBoost do Railway Volume.

    Comportamento:
    - Cold start: lê win_fast_latest.pkl do volume local (~1ms)
    - Requests subsequentes: usa cache em memória (<1ms)
    - Cache expira em 5min → detecta novo modelo automaticamente após re-treino
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
        """Retorna modelo — carrega do volume se cache expirado.

        Cached failure (_model=None but _loaded_at set) does not retry
        on every call — only retries after MODEL_CACHE_TTL expires.
        """
        now = time.time()
        if (now - self._loaded_at) > MODEL_CACHE_TTL:
            self._load_from_volume()
        return self._model

    def _load_from_volume(self):
        """Leitura e deserialização do modelo do Railway Volume."""
        logger.info(f"Carregando modelo do volume: {MODEL_PATH}")
        t0 = time.time()

        try:
            self._model = joblib.load(MODEL_PATH)
            self._loaded_at = time.time()

            elapsed = round((time.time() - t0) * 1000, 1)
            logger.info(f"Modelo carregado em {elapsed}ms")

        except Exception as e:
            logger.error(f"Erro ao carregar modelo do volume: {e}")
            # Cache the failure timestamp so we don't retry on every request.
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
