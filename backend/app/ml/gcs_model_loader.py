"""Model loader — loads the active XGBoost model from the PostgreSQL ml_models table.

The ML Trainer serializes the model with joblib and stores it in model_blob (BYTEA).
This loader queries the active row, deserializes in-memory, and caches for MODEL_CACHE_TTL
seconds so a newly trained model is picked up automatically without restarting the API.
"""

import io
import logging
import os
import time
from threading import Lock
from typing import Optional

import joblib
import psycopg2

logger = logging.getLogger(__name__)

MODEL_CACHE_TTL = int(os.getenv("MODEL_CACHE_TTL", "300"))  # seconds


class GCSModelLoader:
    """
    Singleton que carrega e cacheia o modelo XGBoost do PostgreSQL (ml_models.model_blob).

    Comportamento:
    - Cold start: lê blob do DB e deserializa em memória (~50-200ms dependendo do tamanho)
    - Requests subsequentes: usa cache em memória (<1ms)
    - Cache expira em MODEL_CACHE_TTL s → detecta novo modelo após re-treino automaticamente
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
        """Retorna modelo — recarrega do DB se cache expirado."""
        now = time.time()
        if (now - self._loaded_at) > MODEL_CACHE_TTL:
            self._load_from_db()
        return self._model

    def _load_from_db(self):
        """Lê model_blob da linha active em ml_models e deserializa."""
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            raise RuntimeError("DATABASE_URL not set — cannot load model")

        # Normalise asyncpg URL to psycopg2
        if db_url.startswith("postgresql+asyncpg://"):
            db_url = "postgresql://" + db_url[len("postgresql+asyncpg://"):]
        elif db_url.startswith("postgres://"):
            db_url = "postgresql://" + db_url[len("postgres://"):]

        logger.info("Carregando modelo do DB (ml_models WHERE status='active')...")
        t0 = time.time()
        try:
            conn = psycopg2.connect(db_url, connect_timeout=10)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT model_blob, version FROM ml_models "
                        "WHERE status = 'active' AND model_blob IS NOT NULL "
                        "ORDER BY version DESC LIMIT 1"
                    )
                    row = cur.fetchone()
            finally:
                conn.close()

            if row is None:
                raise FileNotFoundError(
                    "Nenhum modelo ativo com model_blob no DB — "
                    "execute o ML Trainer para treinar e registrar um modelo."
                )

            blob_bytes, version = row
            # psycopg2 retorna BYTEA como memoryview
            if isinstance(blob_bytes, memoryview):
                blob_bytes = bytes(blob_bytes)

            self._model = joblib.load(io.BytesIO(blob_bytes))
            self._loaded_at = time.time()
            self._model_version = version

            elapsed = round((time.time() - t0) * 1000, 1)
            logger.info(f"Modelo v{version} carregado do DB em {elapsed}ms")

        except Exception as e:
            logger.error(f"Erro ao carregar modelo do DB: {e}")
            self._loaded_at = time.time()  # evita retry em cada request
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
