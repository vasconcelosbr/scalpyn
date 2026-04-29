"""Prediction Service — Real-time inference for L3 ranking and blocking."""

import logging
from typing import Dict, Any, Optional, List, Tuple
from .model_loader import get_model_loader, ModelLoader

logger = logging.getLogger(__name__)


class PredictService:
    """Prediction service for real-time trade outcome prediction."""

    def __init__(
        self,
        model_path: str = "/tmp/scalpyn_models/model.pkl",
        fallback_probability: float = 1.0,
    ):
        """
        Initialize prediction service.

        Args:
            model_path: Path to trained model
            fallback_probability: Probability to return if model fails
        """
        self.model_path = model_path
        self.fallback_probability = fallback_probability
        self.loader: Optional[ModelLoader] = None

    def initialize(self) -> bool:
        """
        Initialize and load model.

        Returns:
            True if successful, False otherwise
        """
        try:
            self.loader = get_model_loader(model_path=self.model_path)
            return self.loader.is_loaded()
        except Exception as e:
            logger.exception(f"Failed to initialize prediction service: {e}")
            return False

    def is_ready(self) -> bool:
        """Check if service is ready."""
        return self.loader is not None and self.loader.is_loaded()

    def _prepare_features(
        self,
        features: Dict[str, Any],
        direction: str,
    ) -> Dict[str, Any]:
        """
        Prepare feature dictionary with direction encoding.

        Args:
            features: Base features from indicators
            direction: Trade direction (LONG/SHORT/SPOT)

        Returns:
            Complete feature dictionary
        """
        from .dataset_builder import DatasetBuilder

        builder = DatasetBuilder()

        # Copy features
        prepared = dict(features)

        # Add direction encoding
        direction_map = {
            "LONG": 1,
            "SHORT": -1,
            "SPOT": 0,
        }
        prepared["direction_encoded"] = direction_map.get(direction, 0)

        # Engineer features (same as training)
        prepared["flow_strength"] = (
            prepared.get("taker_ratio", 0) * prepared.get("volume_delta", 0)
        )

        prepared["trend_alignment"] = (
            (1 if prepared.get("ema9_gt_ema21") else 0)
            + (1 if prepared.get("ema50_gt_ema200") else 0)
        )

        prepared["momentum_strength"] = (
            prepared.get("macd_histogram", 0) * prepared.get("adx", 0)
        )

        volume_24h = prepared.get("volume_24h_usdt", 0)
        if volume_24h > 0:
            prepared["delta_normalized"] = prepared.get("volume_delta", 0) / volume_24h
        else:
            prepared["delta_normalized"] = 0.0

        ema9 = prepared.get("ema9", 0)
        ema21 = prepared.get("ema21", 0)
        if ema21 > 0:
            prepared["ema_distance_pct"] = (ema9 - ema21) / ema21 * 100
        else:
            prepared["ema_distance_pct"] = 0.0

        return prepared

    def predict_single_direction(
        self,
        features: Dict[str, Any],
        direction: str,
    ) -> float:
        """
        Predict probability for a single direction.

        Args:
            features: Feature dictionary
            direction: Trade direction (LONG/SHORT/SPOT)

        Returns:
            Predicted probability
        """
        if not self.is_ready():
            logger.warning("Model not loaded, using fallback probability")
            return self.fallback_probability

        try:
            # Prepare features with direction
            prepared_features = self._prepare_features(features, direction)

            # Predict
            probability = self.loader.predict(prepared_features)

            if probability is None:
                logger.warning("Prediction failed, using fallback")
                return self.fallback_probability

            return probability

        except Exception as e:
            logger.exception(f"Prediction error: {e}")
            return self.fallback_probability

    def predict_both_directions(
        self,
        features: Dict[str, Any],
    ) -> Tuple[float, float]:
        """
        Predict probabilities for both LONG and SHORT directions.

        Args:
            features: Feature dictionary

        Returns:
            Tuple of (prob_long, prob_short)
        """
        prob_long = self.predict_single_direction(features, "LONG")
        prob_short = self.predict_single_direction(features, "SHORT")

        return prob_long, prob_short

    def predict_best_direction(
        self,
        features: Dict[str, Any],
        profile_type: str = "FUTURES",
    ) -> Dict[str, Any]:
        """
        Predict best direction and probability.

        For SPOT: only predict LONG
        For FUTURES: predict both LONG and SHORT, return best

        Args:
            features: Feature dictionary
            profile_type: Profile type (SPOT/FUTURES)

        Returns:
            Dictionary with direction, probability, and final_score
        """
        if profile_type == "SPOT":
            # SPOT: only LONG direction
            probability = self.predict_single_direction(features, "SPOT")
            direction = "LONG"

        else:
            # FUTURES: evaluate both directions
            prob_long, prob_short = self.predict_both_directions(features)

            if prob_long >= prob_short:
                direction = "LONG"
                probability = prob_long
            else:
                direction = "SHORT"
                probability = prob_short

        return {
            "probability": probability,
            "direction": direction,
        }

    def calculate_final_score(
        self,
        score: float,
        probability: float,
    ) -> float:
        """
        Calculate final score as (score / 100) * probability.

        Args:
            score: Base score (0-100)
            probability: ML probability (0-1)

        Returns:
            Final score
        """
        # Normalize score to 0-1 range
        score_normalized = score / 100.0

        # Multiply by probability
        final_score = score_normalized * probability

        return final_score

    def should_block(
        self,
        probability: float,
        ai_block_threshold: float,
    ) -> bool:
        """
        Determine if trade should be blocked based on ML prediction.

        Args:
            probability: Predicted probability
            ai_block_threshold: Threshold from config

        Returns:
            True if trade should be blocked
        """
        return probability < ai_block_threshold

    def predict_for_l3_ranking(
        self,
        asset: Dict[str, Any],
        profile_type: str = "FUTURES",
    ) -> Dict[str, Any]:
        """
        Complete prediction pipeline for L3 ranking.

        Args:
            asset: Asset dictionary with features and score
            profile_type: Profile type (SPOT/FUTURES)

        Returns:
            Dictionary with enriched prediction data
        """
        features = asset.get("features", {})
        base_score = asset.get("score", 0)

        # Predict best direction
        prediction = self.predict_best_direction(features, profile_type)

        # Calculate final score
        final_score = self.calculate_final_score(base_score, prediction["probability"])

        return {
            "symbol": asset.get("symbol"),
            "base_score": base_score,
            "probability": prediction["probability"],
            "direction": prediction["direction"],
            "final_score": final_score,
        }

    def batch_predict_for_l3(
        self,
        assets: List[Dict[str, Any]],
        profile_type: str = "FUTURES",
        ai_block_threshold: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        Batch prediction for L3 asset list.

        Args:
            assets: List of asset dictionaries
            profile_type: Profile type
            ai_block_threshold: Blocking threshold

        Returns:
            List of enriched assets sorted by final_score
        """
        results = []

        for asset in assets:
            try:
                prediction = self.predict_for_l3_ranking(asset, profile_type)

                # Add blocking decision
                prediction["blocked_by_ml"] = self.should_block(
                    prediction["probability"], ai_block_threshold
                )

                results.append(prediction)

            except Exception as e:
                logger.exception(f"Error predicting for {asset.get('symbol')}: {e}")
                # Fallback: use base score only
                results.append(
                    {
                        "symbol": asset.get("symbol"),
                        "base_score": asset.get("score", 0),
                        "probability": self.fallback_probability,
                        "direction": "LONG",
                        "final_score": asset.get("score", 0) / 100.0,
                        "blocked_by_ml": False,
                    }
                )

        # Sort by final_score descending
        results.sort(key=lambda x: x["final_score"], reverse=True)

        return results


# Global service instance
_global_service: Optional[PredictService] = None


def get_predict_service(
    model_path: str = "/tmp/scalpyn_models/model.pkl",
    reload: bool = False,
) -> PredictService:
    """
    Get global prediction service instance.

    Args:
        model_path: Path to model file
        reload: Force reload

    Returns:
        PredictService instance
    """
    global _global_service

    if _global_service is None or reload:
        _global_service = PredictService(model_path=model_path)
        _global_service.initialize()

    return _global_service
