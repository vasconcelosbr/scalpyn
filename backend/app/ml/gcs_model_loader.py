"""Model loader — loads the active XGBoost model from the PostgreSQL ml_models table.

The ML Trainer serializes the model with joblib and stores it in model_blob (BYTEA).
This loader queries the active row, deserializes in-memory, and caches for MODEL_CACHE_TTL
seconds so a newly trained model is picked up automatically without restarting the API.
"""

import io
import logging
import os
import time
import sys
from importlib.metadata import PackageNotFoundError, version as package_version
from threading import Lock
from typing import Dict, Optional

import joblib
import psycopg2

logger = logging.getLogger(__name__)

MODEL_CACHE_TTL = int(os.getenv("MODEL_CACHE_TTL", "300"))  # seconds


def _ml_dependency_versions() -> Dict[str, Optional[str]]:
    deps = {
        "xgboost": "xgboost",
        "scikit_learn": "scikit-learn",
        "numpy": "numpy",
        "pandas": "pandas",
        "joblib": "joblib",
        "scipy": "scipy",
    }
    versions: Dict[str, Optional[str]] = {"python": sys.version.split()[0]}
    for key, package_name in deps.items():
        try:
            versions[key] = package_version(package_name)
        except PackageNotFoundError:
            versions[key] = None
    return versions


class GCSModelLoader:
    """
    Singleton que carrega e cacheia o modelo XGBoost do PostgreSQL (ml_models.model_blob).

    Comportamento:
    - Cold start: lê blob do DB e deserializa em memória (~50-200ms dependendo do tamanho)
    - Requests subsequentes: usa cache em memória (<1ms)
    - Cache expira em MODEL_CACHE_TTL s → detecta novo modelo após re-treino automaticamente
    - Profile models: cached separately by (scope:profile_id) key with same TTL
    """

    _instance: Optional["GCSModelLoader"] = None
    _lock = Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                # Legacy single-model cache (global model)
                cls._instance._model = None
                cls._instance._loaded_at = 0.0
                cls._instance._model_version = None
                cls._instance._feature_columns = None
                # Per-cache-key storage for profile models:
                # {cache_key: {"model": ..., "loaded_at": float, "version": ...}}
                cls._instance._cache: Dict[str, Dict] = {}
        return cls._instance

    def get_model(self, profile_id: Optional[str] = None):
        """Retorna modelo — recarrega do DB se cache expirado.

        Args:
            profile_id: Optional UUID string. When provided, loads the active
                        profile-specific model first; falls back to global model
                        if no profile model exists.
        """
        if profile_id:
            cache_key = f"profile:{profile_id}"
        else:
            cache_key = "global"

        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and (now - cached.get("loaded_at", 0.0)) <= MODEL_CACHE_TTL:
            return cached["model"]

        return self._load_from_db(profile_id=profile_id, cache_key=cache_key)

    def _normalize_db_url(self) -> str:
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            raise RuntimeError("DATABASE_URL not set — cannot load model")
        if db_url.startswith("postgresql+asyncpg://"):
            db_url = "postgresql://" + db_url[len("postgresql+asyncpg://"):]
        elif db_url.startswith("postgres://"):
            db_url = "postgresql://" + db_url[len("postgres://"):]
        return db_url

    def _deserialize_blob(self, blob_bytes, version) -> Dict:
        """Deserialize model_blob and return dict with model + feature_columns."""
        if isinstance(blob_bytes, memoryview):
            blob_bytes = bytes(blob_bytes)
        loaded = joblib.load(io.BytesIO(blob_bytes))
        if isinstance(loaded, dict) and "model" in loaded:
            model = loaded["model"]
            feature_columns = loaded.get("feature_columns")
            metadata = loaded.get("metadata") or {}
            trained_versions = metadata.get("dependency_versions") or {}
            runtime_versions = _ml_dependency_versions()
            mismatches = {
                key: {"trained": trained_versions.get(key), "runtime": value}
                for key, value in runtime_versions.items()
                if trained_versions.get(key) not in (None, value)
            }
            if mismatches:
                logger.warning("ML runtime differs from trained model: %s", mismatches)
            logger.info("Model loaded from dict format (feature_columns=%d)",
                        len(feature_columns or []))
        else:
            model = loaded
            feature_columns = None
        return {"model": model, "feature_columns": feature_columns, "version": version}

    def _load_from_db(self, profile_id: Optional[str] = None, cache_key: str = "global"):
        """Lê model_blob da linha active em ml_models e deserializa.

        When profile_id is provided:
        1. Try profile-specific model (model_scope='profile' AND profile_id=profile_id)
        2. Fall back to global model if no profile model exists
        """
        db_url = self._normalize_db_url()
        logger.info("Carregando modelo do DB (cache_key=%s)...", cache_key)
        t0 = time.time()

        try:
            conn = psycopg2.connect(db_url, connect_timeout=10)
            try:
                with conn.cursor() as cur:
                    row = None

                    # Try profile-specific model first (if profile_id given)
                    if profile_id:
                        cur.execute(
                            "SELECT model_blob, version FROM ml_models "
                            "WHERE status = 'active' "
                            "  AND model_scope = 'profile' "
                            "  AND profile_id = %s "
                            "  AND model_blob IS NOT NULL "
                            "ORDER BY activated_at DESC NULLS LAST, version DESC "
                            "LIMIT 1",
                            (profile_id,)
                        )
                        row = cur.fetchone()
                        if row:
                            logger.info(
                                "Found profile-specific model for profile_id=%s", profile_id
                            )

                    # Fall back to global model
                    if row is None:
                        cur.execute(
                            "SELECT model_blob, version FROM ml_models "
                            "WHERE status = 'active' "
                            "  AND (model_scope = 'global' OR model_scope IS NULL OR profile_id IS NULL) "
                            "  AND model_blob IS NOT NULL "
                            "ORDER BY activated_at DESC NULLS LAST, version DESC LIMIT 1"
                        )
                        row = cur.fetchone()
                        if row and profile_id:
                            logger.info(
                                "No profile model for %s — using global fallback", profile_id
                            )
            finally:
                conn.close()

            if row is None:
                raise FileNotFoundError(
                    "Nenhum modelo ativo com model_blob no DB — "
                    "execute o ML Trainer para treinar e registrar um modelo."
                )

            blob_bytes, version = row
            result = self._deserialize_blob(blob_bytes, version)

            # Store in per-key cache
            self._cache[cache_key] = {
                "model": result["model"],
                "feature_columns": result["feature_columns"],
                "version": version,
                "loaded_at": time.time(),
            }

            # Also update legacy attributes for backward compatibility
            if cache_key == "global":
                self._model = result["model"]
                self._feature_columns = result["feature_columns"]
                self._loaded_at = time.time()
                self._model_version = version

            elapsed = round((time.time() - t0) * 1000, 1)
            logger.info("Modelo v%s carregado do DB em %sms (cache_key=%s)",
                        version, elapsed, cache_key)

            return result["model"]

        except Exception as e:
            logger.error("Erro ao carregar modelo do DB (cache_key=%s): %s", cache_key, e)
            # Avoid retry storm — set loaded_at so next call waits for TTL
            self._cache[cache_key] = {
                "model": None,
                "loaded_at": time.time(),
                "version": None,
                "feature_columns": None,
            }
            if cache_key == "global":
                self._loaded_at = time.time()
            # Re-raise only if we have no cached model at all
            if self._cache.get(cache_key, {}).get("model") is None and self._model is None:
                raise

    def invalidate(self, profile_id: Optional[str] = None):
        """Força reload no próximo request.

        When profile_id given, invalidates only that profile's cache entry.
        When called with no args, invalidates ALL cached entries (global + all profiles).
        """
        if profile_id:
            cache_key = f"profile:{profile_id}"
            self._cache.pop(cache_key, None)
            logger.info("Cache do modelo invalidado para profile_id=%s.", profile_id)
        else:
            self._cache.clear()
            self._model = None
            self._loaded_at = 0.0
            logger.info("Cache de todos os modelos invalidado.")


# Instância global
_loader = GCSModelLoader()


def get_model(profile_id: Optional[str] = None):
    """Função de conveniência — use em qualquer lugar.

    Args:
        profile_id: Optional UUID string. When provided, tries profile-specific
                    model first, falls back to global model.
    """
    return _loader.get_model(profile_id=profile_id)


def invalidate_model_cache(profile_id: Optional[str] = None):
    """Invalidate model cache. Pass profile_id to invalidate only that profile."""
    _loader.invalidate(profile_id=profile_id)
