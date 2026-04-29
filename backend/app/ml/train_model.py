"""Model Training — Train XGBoost model for trade outcome prediction."""

import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import (
    roc_auc_score,
    log_loss,
    precision_recall_fscore_support,
    accuracy_score,
    confusion_matrix,
)
import joblib
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ModelTrainer:
    """Train and manage XGBoost models for trade outcome prediction."""

    def __init__(
        self,
        model_dir: str = "/tmp/scalpyn_models",
        params: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize model trainer.

        Args:
            model_dir: Directory to save trained models
            params: XGBoost parameters (optional, defaults will be used)
        """
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

        # Default XGBoost parameters
        self.params = params or {
            "objective": "binary:logistic",
            "eval_metric": ["auc", "logloss"],
            "max_depth": 5,
            "learning_rate": 0.08,
            "n_estimators": 300,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 1,
            "gamma": 0,
            "reg_alpha": 0,
            "reg_lambda": 1,
            "random_state": 42,
            "tree_method": "hist",
            "device": "cpu",
        }

        self.model: Optional[xgb.XGBClassifier] = None
        self.feature_columns: Optional[List[str]] = None
        self.training_metadata: Optional[Dict[str, Any]] = None

    def calculate_scale_pos_weight(
        self, y_train: pd.Series
    ) -> float:
        """
        Calculate scale_pos_weight to handle class imbalance.

        Args:
            y_train: Training labels

        Returns:
            scale_pos_weight value
        """
        n_neg = (y_train == 0).sum()
        n_pos = (y_train == 1).sum()

        if n_pos == 0:
            logger.warning("No positive samples in training data!")
            return 1.0

        weight = n_neg / n_pos
        logger.info(f"Class imbalance: {n_neg} negatives, {n_pos} positives")
        logger.info(f"Setting scale_pos_weight={weight:.2f}")

        return weight

    def train(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: pd.DataFrame,
        y_val: pd.Series,
        feature_columns: List[str],
        early_stopping_rounds: int = 20,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """
        Train XGBoost model.

        Args:
            X_train: Training features
            y_train: Training labels
            X_val: Validation features
            y_val: Validation labels
            feature_columns: List of feature column names
            early_stopping_rounds: Early stopping patience
            verbose: Whether to print training progress

        Returns:
            Dictionary with training metrics
        """
        logger.info("Starting model training...")
        logger.info(f"Training samples: {len(X_train)}, Validation samples: {len(X_val)}")
        logger.info(f"Features: {len(feature_columns)}")

        self.feature_columns = feature_columns

        # Extract feature matrices
        X_train_features = X_train[feature_columns]
        X_val_features = X_val[feature_columns]

        # Calculate class weight
        scale_pos_weight = self.calculate_scale_pos_weight(y_train)
        self.params["scale_pos_weight"] = scale_pos_weight

        # Initialize model
        self.model = xgb.XGBClassifier(**self.params)

        # Train with early stopping
        eval_set = [(X_train_features, y_train), (X_val_features, y_val)]

        self.model.fit(
            X_train_features,
            y_train,
            eval_set=eval_set,
            early_stopping_rounds=early_stopping_rounds,
            verbose=verbose,
        )

        logger.info(f"Training complete. Best iteration: {self.model.best_iteration}")

        # Get predictions for evaluation
        y_train_pred_proba = self.model.predict_proba(X_train_features)[:, 1]
        y_val_pred_proba = self.model.predict_proba(X_val_features)[:, 1]

        # Calculate metrics
        train_auc = roc_auc_score(y_train, y_train_pred_proba)
        val_auc = roc_auc_score(y_val, y_val_pred_proba)

        train_logloss = log_loss(y_train, y_train_pred_proba)
        val_logloss = log_loss(y_val, y_val_pred_proba)

        metrics = {
            "train_auc": train_auc,
            "val_auc": val_auc,
            "train_logloss": train_logloss,
            "val_logloss": val_logloss,
            "best_iteration": self.model.best_iteration,
            "n_features": len(feature_columns),
            "n_train": len(X_train),
            "n_val": len(X_val),
        }

        logger.info(f"Training AUC: {train_auc:.4f}, Validation AUC: {val_auc:.4f}")
        logger.info(f"Training LogLoss: {train_logloss:.4f}, Validation LogLoss: {val_logloss:.4f}")

        # Store metadata
        self.training_metadata = {
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "params": self.params,
            "metrics": metrics,
            "feature_columns": feature_columns,
        }

        return metrics

    def evaluate(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        feature_columns: List[str],
        threshold: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Evaluate model performance.

        Args:
            X: Features
            y: Labels
            feature_columns: Feature column names
            threshold: Classification threshold

        Returns:
            Dictionary with evaluation metrics
        """
        if self.model is None:
            raise ValueError("Model not trained yet")

        X_features = X[feature_columns]

        # Predictions
        y_pred_proba = self.model.predict_proba(X_features)[:, 1]
        y_pred = (y_pred_proba >= threshold).astype(int)

        # Metrics
        auc = roc_auc_score(y, y_pred_proba)
        logloss = log_loss(y, y_pred_proba)
        accuracy = accuracy_score(y, y_pred)

        precision, recall, f1, support = precision_recall_fscore_support(
            y, y_pred, average="binary", zero_division=0
        )

        tn, fp, fn, tp = confusion_matrix(y, y_pred).ravel()

        metrics = {
            "auc": auc,
            "logloss": logloss,
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "true_negatives": int(tn),
            "false_positives": int(fp),
            "false_negatives": int(fn),
            "true_positives": int(tp),
        }

        logger.info(f"Evaluation: AUC={auc:.4f}, Accuracy={accuracy:.4f}, F1={f1:.4f}")

        return metrics

    def get_feature_importance(
        self, top_n: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Get feature importance scores.

        Args:
            top_n: Number of top features to return (None for all)

        Returns:
            DataFrame with feature names and importance scores
        """
        if self.model is None or self.feature_columns is None:
            raise ValueError("Model not trained yet")

        importance = self.model.feature_importances_
        importance_df = pd.DataFrame(
            {
                "feature": self.feature_columns,
                "importance": importance,
            }
        )

        importance_df = importance_df.sort_values("importance", ascending=False)

        if top_n is not None:
            importance_df = importance_df.head(top_n)

        return importance_df

    def analyze_by_probability_bucket(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        feature_columns: List[str],
        n_buckets: int = 10,
    ) -> pd.DataFrame:
        """
        Analyze win rate by probability bucket.

        Args:
            X: Features
            y: Labels
            feature_columns: Feature column names
            n_buckets: Number of probability buckets

        Returns:
            DataFrame with bucket analysis
        """
        if self.model is None:
            raise ValueError("Model not trained yet")

        X_features = X[feature_columns]
        y_pred_proba = self.model.predict_proba(X_features)[:, 1]

        # Create buckets
        buckets = pd.cut(y_pred_proba, bins=n_buckets, labels=False, duplicates="drop")

        # Calculate statistics per bucket
        bucket_stats = []
        for bucket_id in sorted(pd.Series(buckets).dropna().unique()):
            mask = buckets == bucket_id
            bucket_probs = y_pred_proba[mask]
            bucket_labels = y[mask]

            if len(bucket_labels) > 0:
                bucket_stats.append(
                    {
                        "bucket": int(bucket_id),
                        "prob_min": bucket_probs.min(),
                        "prob_max": bucket_probs.max(),
                        "prob_mean": bucket_probs.mean(),
                        "count": len(bucket_labels),
                        "wins": int(bucket_labels.sum()),
                        "win_rate": bucket_labels.mean(),
                    }
                )

        return pd.DataFrame(bucket_stats)

    def save(self, model_name: str = "model.pkl") -> str:
        """
        Save trained model to disk.

        Args:
            model_name: Name of the model file

        Returns:
            Path to saved model
        """
        if self.model is None:
            raise ValueError("Model not trained yet")

        model_path = self.model_dir / model_name

        # Save model and metadata
        model_data = {
            "model": self.model,
            "feature_columns": self.feature_columns,
            "metadata": self.training_metadata,
        }

        joblib.dump(model_data, model_path)
        logger.info(f"Model saved to {model_path}")

        return str(model_path)

    def load(self, model_path: str) -> None:
        """
        Load trained model from disk.

        Args:
            model_path: Path to model file
        """
        logger.info(f"Loading model from {model_path}")

        model_data = joblib.load(model_path)

        self.model = model_data["model"]
        self.feature_columns = model_data["feature_columns"]
        self.training_metadata = model_data.get("metadata")

        logger.info("Model loaded successfully")


async def train_model_pipeline(
    db,
    min_date: Optional[str] = None,
    max_date: Optional[str] = None,
    model_name: str = "model.pkl",
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Complete model training pipeline.

    Args:
        db: Database session
        min_date: Minimum date for training data
        max_date: Maximum date for training data
        model_name: Name for saved model
        params: XGBoost parameters

    Returns:
        Dictionary with training results
    """
    from .dataset_builder import DatasetBuilder

    logger.info("Starting model training pipeline...")

    # Build dataset
    builder = DatasetBuilder()
    simulations = await builder.load_simulations(
        db=db,
        min_date=min_date,
        max_date=max_date,
        decision_type="ALLOW",  # Only train on allowed trades
    )

    if len(simulations) < 100:
        raise ValueError(f"Insufficient training data: {len(simulations)} simulations")

    # Prepare dataset
    df, labels, feature_cols = builder.prepare_dataset(simulations)

    # Time-based split
    X_train, X_val, y_train, y_val = builder.time_based_split(df, labels, train_ratio=0.8)

    # Train model
    trainer = ModelTrainer(params=params)
    metrics = trainer.train(
        X_train=X_train,
        y_train=y_train,
        X_val=X_val,
        y_val=y_val,
        feature_columns=feature_cols,
        early_stopping_rounds=20,
        verbose=True,
    )

    # Get feature importance
    feature_importance = trainer.get_feature_importance(top_n=20)
    logger.info("\nTop 20 features:")
    logger.info("\n" + feature_importance.to_string())

    # Analyze by probability bucket
    bucket_analysis = trainer.analyze_by_probability_bucket(
        X=X_val, y=y_val, feature_columns=feature_cols, n_buckets=10
    )
    logger.info("\nWin rate by probability bucket:")
    logger.info("\n" + bucket_analysis.to_string())

    # Save model
    model_path = trainer.save(model_name=model_name)

    return {
        "model_path": model_path,
        "metrics": metrics,
        "feature_importance": feature_importance.to_dict("records"),
        "bucket_analysis": bucket_analysis.to_dict("records"),
        "n_simulations": len(simulations),
    }
