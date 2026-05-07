"""Evaluation Report — Generate comprehensive model evaluation metrics."""

import logging
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

from .model_loader import ModelLoader
from .dataset_builder import DatasetBuilder

logger = logging.getLogger(__name__)


class EvaluationReport:
    """Generate comprehensive evaluation reports for trained models."""

    def __init__(self, model_path: str):
        """
        Initialize evaluation report generator.

        Args:
            model_path: Path to trained model
        """
        self.model_path = model_path
        self.loader = ModelLoader(model_path=model_path)

    async def generate_report(
        self,
        db,
        min_date: Optional[str] = None,
        max_date: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate complete evaluation report.

        Args:
            db: Database session
            min_date: Minimum date for evaluation data
            max_date: Maximum date for evaluation data
            output_path: Path to save report (optional)

        Returns:
            Dictionary with evaluation results
        """
        logger.info("Generating evaluation report...")

        # Load model
        if not self.loader.load():
            raise ValueError("Failed to load model")

        # Load simulations
        builder = DatasetBuilder()
        simulations = await builder.load_simulations(
            db=db,
            min_date=min_date,
            max_date=max_date,
            decision_type="ALLOW",
        )

        if len(simulations) == 0:
            raise ValueError("No simulations found for evaluation")

        # Prepare dataset
        df, labels, feature_cols = builder.prepare_dataset(simulations)

        # Get predictions
        X = df[feature_cols]
        y_pred_proba = self.loader.model.predict_proba(X)[:, 1]
        y_pred = (y_pred_proba >= 0.5).astype(int)

        # Calculate metrics
        from sklearn.metrics import (
            roc_auc_score,
            log_loss,
            accuracy_score,
            precision_recall_fscore_support,
            confusion_matrix,
            roc_curve,
        )

        auc = roc_auc_score(labels, y_pred_proba)
        logloss = log_loss(labels, y_pred_proba)
        accuracy = accuracy_score(labels, y_pred)

        precision, recall, f1, support = precision_recall_fscore_support(
            labels, y_pred, average="binary", zero_division=0
        )

        tn, fp, fn, tp = confusion_matrix(labels, y_pred).ravel()

        # Calculate ROC curve
        fpr, tpr, thresholds = roc_curve(labels, y_pred_proba)

        # Feature importance
        feature_importance = pd.DataFrame(
            {
                "feature": feature_cols,
                "importance": self.loader.model.feature_importances_,
            }
        ).sort_values("importance", ascending=False)

        # Win rate by probability bucket
        bucket_analysis = self._analyze_probability_buckets(
            y_pred_proba, labels, n_buckets=10
        )

        # Direction breakdown
        direction_stats = self._analyze_by_direction(df, labels, y_pred_proba)

        # Summary report
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_path": self.model_path,
            "model_metadata": self.loader.metadata,
            "dataset": {
                "n_samples": len(simulations),
                "n_features": len(feature_cols),
                "n_wins": int(labels.sum()),
                "n_losses": int((labels == 0).sum()),
                "win_rate": float(labels.mean()),
                "date_range": {
                    "min": min_date,
                    "max": max_date,
                },
            },
            "metrics": {
                "auc": float(auc),
                "logloss": float(logloss),
                "accuracy": float(accuracy),
                "precision": float(precision),
                "recall": float(recall),
                "f1": float(f1),
                "confusion_matrix": {
                    "true_negatives": int(tn),
                    "false_positives": int(fp),
                    "false_negatives": int(fn),
                    "true_positives": int(tp),
                },
            },
            "feature_importance": feature_importance.head(20).to_dict("records"),
            "bucket_analysis": bucket_analysis.to_dict("records"),
            "direction_stats": direction_stats,
        }

        # Log summary
        logger.info("=" * 60)
        logger.info("EVALUATION REPORT")
        logger.info("=" * 60)
        logger.info(f"Samples: {len(simulations)}")
        logger.info(f"AUC: {auc:.4f}")
        logger.info(f"Accuracy: {accuracy:.4f}")
        logger.info(f"Precision: {precision:.4f}")
        logger.info(f"Recall: {recall:.4f}")
        logger.info(f"F1: {f1:.4f}")
        logger.info("=" * 60)
        logger.info("\nTop 10 Features:")
        for i, row in feature_importance.head(10).iterrows():
            logger.info(f"  {row['feature']}: {row['importance']:.4f}")
        logger.info("=" * 60)

        # Save to file if requested
        if output_path:
            self._save_report(report, output_path)

        return report

    def _analyze_probability_buckets(
        self,
        y_pred_proba: np.ndarray,
        labels: pd.Series,
        n_buckets: int = 10,
    ) -> pd.DataFrame:
        """Analyze win rate by probability bucket."""
        buckets = pd.cut(y_pred_proba, bins=n_buckets, labels=False, duplicates="drop")

        bucket_stats = []
        for bucket_id in sorted(pd.Series(buckets).dropna().unique()):
            mask = buckets == bucket_id
            bucket_probs = y_pred_proba[mask]
            bucket_labels = labels[mask]

            if len(bucket_labels) > 0:
                bucket_stats.append(
                    {
                        "bucket": int(bucket_id),
                        "prob_min": float(bucket_probs.min()),
                        "prob_max": float(bucket_probs.max()),
                        "prob_mean": float(bucket_probs.mean()),
                        "count": int(len(bucket_labels)),
                        "wins": int(bucket_labels.sum()),
                        "losses": int((bucket_labels == 0).sum()),
                        "win_rate": float(bucket_labels.mean()),
                    }
                )

        return pd.DataFrame(bucket_stats)

    def _analyze_by_direction(
        self,
        df: pd.DataFrame,
        labels: pd.Series,
        y_pred_proba: np.ndarray,
    ) -> Dict[str, Any]:
        """Analyze performance by trade direction."""
        stats = {}

        for direction in ["LONG", "SHORT", "SPOT"]:
            mask = df["direction"] == direction
            if mask.sum() == 0:
                continue

            direction_labels = labels[mask]
            direction_probs = y_pred_proba[mask]

            stats[direction] = {
                "count": int(mask.sum()),
                "wins": int(direction_labels.sum()),
                "losses": int((direction_labels == 0).sum()),
                "win_rate": float(direction_labels.mean()),
                "avg_probability": float(direction_probs.mean()),
            }

        return stats

    def _save_report(self, report: Dict[str, Any], output_path: str) -> None:
        """Save report to JSON file."""
        import json

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            json.dump(report, f, indent=2, default=str)

        logger.info(f"Report saved to {path}")


async def generate_evaluation_report(
    db,
    model_path: str = "/tmp/scalpyn_models/model.pkl",
    min_date: Optional[str] = None,
    max_date: Optional[str] = None,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate evaluation report for a trained model.

    Args:
        db: Database session
        model_path: Path to model file
        min_date: Minimum date for evaluation
        max_date: Maximum date for evaluation
        output_path: Path to save report

    Returns:
        Evaluation report dictionary
    """
    reporter = EvaluationReport(model_path=model_path)
    return await reporter.generate_report(
        db=db,
        min_date=min_date,
        max_date=max_date,
        output_path=output_path,
    )
