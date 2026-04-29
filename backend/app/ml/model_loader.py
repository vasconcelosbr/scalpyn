"""Model Loader — Load and manage trained XGBoost models."""

import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
import joblib
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class ModelLoader:
    """Load and manage trained models for inference."""

    def __init__(self, model_path: Optional[str] = None):
        """
        Initialize model loader.

        Args:
            model_path: Path to trained model file (optional)
        """
        self.model_path = model_path
        self.model = None
        self.feature_columns: Optional[List[str]] = None
        self.metadata: Optional[Dict[str, Any]] = None
        self._loaded = False

    def load(self, model_path: Optional[str] = None) -> bool:
        """
        Load model from disk.

        Args:
            model_path: Path to model file (overrides constructor path)

        Returns:
            True if loaded successfully, False otherwise
        """
        path = model_path or self.model_path

        if not path:
            logger.error("No model path provided")
            return False

        path = Path(path)
        if not path.exists():
            logger.error(f"Model file not found: {path}")
            return False

        try:
            logger.info(f"Loading model from {path}")
            model_data = joblib.load(path)

            self.model = model_data["model"]
            self.feature_columns = model_data["feature_columns"]
            self.metadata = model_data.get("metadata")

            self._loaded = True
            logger.info("Model loaded successfully")
            logger.info(f"Features: {len(self.feature_columns)}")

            if self.metadata:
                trained_at = self.metadata.get("trained_at", "unknown")
                metrics = self.metadata.get("metrics", {})
                val_auc = metrics.get("val_auc", "N/A")
                logger.info(f"Model trained at: {trained_at}")
                logger.info(f"Validation AUC: {val_auc}")

            return True

        except Exception as e:
            logger.exception(f"Failed to load model: {e}")
            self._loaded = False
            return False

    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._loaded and self.model is not None

    def predict(self, features: Dict[str, Any]) -> Optional[float]:
        """
        Predict probability for a single feature vector.

        Args:
            features: Dictionary of feature_name -> value

        Returns:
            Predicted probability or None on error
        """
        if not self.is_loaded():
            logger.error("Model not loaded")
            return None

        try:
            # Build feature vector in correct order
            feature_vector = []
            for feat in self.feature_columns:
                value = features.get(feat)

                # Handle missing features
                if value is None:
                    logger.warning(f"Missing feature: {feat}, using 0.0")
                    value = 0.0

                # Handle boolean features
                if isinstance(value, bool):
                    value = 1.0 if value else 0.0

                feature_vector.append(float(value))

            # Create DataFrame for prediction
            X = pd.DataFrame([feature_vector], columns=self.feature_columns)

            # Predict
            proba = self.model.predict_proba(X)[0, 1]

            return float(proba)

        except Exception as e:
            logger.exception(f"Prediction error: {e}")
            return None

    def predict_batch(
        self, features_list: List[Dict[str, Any]]
    ) -> List[Optional[float]]:
        """
        Predict probabilities for multiple feature vectors.

        Args:
            features_list: List of feature dictionaries

        Returns:
            List of predicted probabilities
        """
        if not self.is_loaded():
            logger.error("Model not loaded")
            return [None] * len(features_list)

        results = []
        for features in features_list:
            prob = self.predict(features)
            results.append(prob)

        return results

    def get_metadata(self) -> Optional[Dict[str, Any]]:
        """Get model metadata."""
        return self.metadata

    def get_feature_columns(self) -> Optional[List[str]]:
        """Get list of required feature columns."""
        return self.feature_columns


# Global model loader instance
_global_loader: Optional[ModelLoader] = None


def get_model_loader(
    model_path: str = "/tmp/scalpyn_models/model.pkl",
    reload: bool = False,
) -> ModelLoader:
    """
    Get global model loader instance.

    Args:
        model_path: Path to model file
        reload: Force reload from disk

    Returns:
        ModelLoader instance
    """
    global _global_loader

    if _global_loader is None or reload:
        _global_loader = ModelLoader(model_path=model_path)
        _global_loader.load()

    return _global_loader


def load_model(model_path: str) -> ModelLoader:
    """
    Load a model and return loader instance.

    Args:
        model_path: Path to model file

    Returns:
        ModelLoader instance
    """
    loader = ModelLoader(model_path=model_path)
    loaded = loader.load()

    if not loaded:
        raise ValueError(f"Failed to load model from {model_path}")

    return loader
